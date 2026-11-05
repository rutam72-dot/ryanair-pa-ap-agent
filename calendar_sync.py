"""Single-calendar OAuth adapter and restart-safe delivery. Standard library only."""
import json
import os
import time
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from monitor import CALENDAR, MARKER, ROUTES, TZ


class CalendarError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class GoogleCalendarApi:
    def __init__(self, client_id, client_secret, refresh_token, opener=urlopen):
        self.credentials = {"client_id": client_id, "client_secret": client_secret,
                            "refresh_token": refresh_token, "grant_type": "refresh_token"}
        self.opener = opener
        self.token = None
        self.expires = 0

    @classmethod
    def from_environment(cls):
        credentials = [os.getenv(key) for key in
                       ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")]
        if not all(credentials):
            raise CalendarError("Missing Google OAuth secrets; no writes attempted")
        return cls(*credentials)

    def refresh(self):
        req = Request("https://oauth2.googleapis.com/token",
                      data=urlencode(self.credentials).encode(),
                      headers={"Content-Type": "application/x-www-form-urlencoded"},
                      method="POST")
        try:
            with self.opener(req, timeout=30) as response:
                data = json.load(response)
            self.token = data["access_token"]
            self.expires = time.monotonic() + max(1, int(data.get("expires_in", 3600)) - 60)
        except (OSError, ValueError, KeyError) as exc:
            raise CalendarError("Google OAuth refresh failed") from exc

    def request(self, method="GET", event_id=None, params=None, body=None):
        # Calendar is a constant, never read from state or caller input.
        url = "https://www.googleapis.com/calendar/v3/calendars/" + quote(CALENDAR, safe="") + "/events"
        if event_id:
            url += "/" + quote(event_id, safe="")
        params = dict(params or {})
        if method in ("POST", "PATCH", "DELETE"):
            params["sendUpdates"] = "none"
        if params:
            url += "?" + urlencode(params)
        refreshed = False
        for attempt in range(4):
            if not self.token or time.monotonic() >= self.expires:
                self.refresh()
            data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            req = Request(url, data=data, method=method, headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json; charset=utf-8", "Accept": "application/json"})
            try:
                with self.opener(req, timeout=30) as response:
                    raw = response.read()
                return json.loads(raw) if raw else {}
            except HTTPError as exc:
                if exc.code == 401 and not refreshed:
                    self.token = None
                    refreshed = True
                    continue
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3 and method != "POST":
                    time.sleep(2 ** attempt)
                    continue
                raise CalendarError(f"Google Calendar HTTP {exc.code} ({method})", exc.code) from exc
            except (URLError, OSError, ValueError) as exc:
                raise CalendarError(f"Google Calendar request failed ({method})") from exc
        raise CalendarError("Google Calendar retry limit reached")

    def get(self, event_id):
        try:
            return self.request(event_id=event_id)
        except CalendarError as exc:
            if exc.status in (404, 410):
                return None
            raise

    def list(self, start, end):
        params = {"timeMin": start, "timeMax": end, "maxResults": 2500,
                  "singleEvents": "true", "showDeleted": "false",
                  "privateExtendedProperty": "managed_by=" + MARKER}
        events = []
        while True:
            page = self.request(params=params)
            events.extend(page.get("items", []))
            if not page.get("nextPageToken"):
                return events
            params["pageToken"] = page["nextPageToken"]

    def insert(self, event_id, body):
        return self.request("POST", body={**body, "id": event_id})

    def patch(self, event_id, body):
        return self.request("PATCH", event_id=event_id, body=body)

    def delete(self, event_id):
        try:
            return self.request("DELETE", event_id=event_id)
        except CalendarError as exc:
            if exc.status not in (404, 410):
                raise
            return {}


def tags(event):
    return ((event.get("extendedProperties") or {}).get("private") or {})


def owned(event):
    private = tags(event)
    return (private.get("managed_by") == MARKER
            and private.get("route") in ROUTES
            and private.get("kind") in ("flight", "alert", "push"))


def time_matches(actual, expected):
    if "date" in expected:
        return actual.get("date") == expected["date"] and not actual.get("dateTime")
    try:
        return datetime.fromisoformat(actual["dateTime"].replace("Z", "+00:00")) == datetime.fromisoformat(
            expected["dateTime"].replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError):
        return False


def verified(event, event_id, body):
    if not event or event.get("id") != event_id or event.get("status") != "confirmed":
        return False
    if event.get("attendees"):
        return False
    if any(event.get(key) != body[key] for key in ("summary", "description", "colorId", "transparency")):
        return False
    if any(tags(event).get(k) != v for k, v in body["extendedProperties"]["private"].items()):
        return False
    reminders = event.get("reminders") or {}
    return (all(time_matches(event.get(k) or {}, body[k]) for k in ("start", "end"))
            and reminders.get("useDefault") is False
            and reminders.get("overrides", []) == body["reminders"]["overrides"])


def deliver(state, api, now, persist=lambda: None, clock=None):
    """Keep jobs pending until readback. Delete alerts only after weekly price delivery."""
    errors = []
    start = datetime.combine(date(2024, 1, 1), datetime.min.time(), TZ).isoformat()
    end = datetime.combine(now.date() + timedelta(days=367), datetime.min.time(), TZ).isoformat()
    try:
        existing = api.list(start, end)
    except CalendarError as exc:
        return [str(exc)]
    slots = {}
    for event in existing:
        if not owned(event) or event.get("status") == "cancelled":
            continue
        private = tags(event)
        if private["kind"] == "flight":
            key = (private["route"], (event.get("start") or {}).get("date"))
            slots.setdefault(key, []).append(event)
    # Alerts and notifications first during hourly work; weekly work contains only flights.
    jobs = sorted(list(state["outbox"].items()),
                  key=lambda pair: (pair[1]["kind"] == "flight", pair[0]))
    for key, job in jobs:
        body = job["body"]
        identifier = job["event_id"]
        try:
            if job["kind"] == "flight":
                matches = slots.get((job["route"], job["day"]), [])
                if len(matches) > 1:
                    raise CalendarError(f"Duplicate owned flight: {job['route']}:{job['day']}; manual review required")
                if matches:
                    identifier = matches[0]["id"]
            prior = api.get(identifier)
            if prior and (not owned(prior) or prior.get("attendees") or prior.get("status") == "cancelled"):
                raise CalendarError("Event ID conflict; unrelated or cancelled event left unchanged")
            if job["kind"] == "push" and not prior:
                # A reminder on the flight date would arrive months too late.
                # This separate short event requests a popup shortly after detection/delivery.
                when = clock() if clock else now
                when = (when + timedelta(minutes=5)).replace(microsecond=0)
                body["start"] = {"dateTime": when.isoformat(), "timeZone": "Europe/Warsaw"}
                body["end"] = {"dateTime": (when + timedelta(minutes=1)).isoformat(),
                               "timeZone": "Europe/Warsaw"}
                persist()
            if not prior:
                try:
                    api.insert(identifier, body)
                except CalendarError as exc:
                    if exc.status != 409:
                        raise
                prior = api.get(identifier)
            elif job["kind"] == "flight" and not verified(prior, identifier, body):
                api.patch(identifier, body)
                prior = api.get(identifier)
            # Preserve already-created push timing across runner crashes.
            if job["kind"] == "push" and prior:
                expected = {**body, "start": prior.get("start", {}), "end": prior.get("end", {})}
            else:
                expected = body
            if not verified(prior, identifier, expected):
                raise CalendarError("Readback differs from planned event")
            del state["outbox"][key]
            persist()
        except CalendarError as exc:
            errors.append(f"{job['kind']} {job['route']}:{job['day']}: {exc}")

    if state["weekly_pending"] and not state["outbox"] and not errors:
        # Safe migration: never recognize ownership by title or description.
        try:
            for event in api.list(start, end):
                if owned(event) and tags(event)["kind"] in ("alert", "push"):
                    api.delete(event["id"])
                    deleted = api.get(event["id"])
                    if deleted and deleted.get("status") != "cancelled":
                        raise CalendarError("Weekly alert deletion not verified")
            state["last_week"] = state["weekly_pending"]
            state["weekly_pending"] = None
            persist()
        except CalendarError as exc:
            errors.append(str(exc))
    return errors
