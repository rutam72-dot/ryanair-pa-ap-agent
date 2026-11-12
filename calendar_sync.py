"""Single-calendar OAuth adapter and restart-safe delivery. Standard library only."""
import json
import os
import random
import re
import time
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from monitor import CALENDAR, MARKER, ROUTES, TZ, event_id
from request_limits import RequestPacer


class CalendarError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class GoogleCalendarApi:
    def __init__(self, client_id, client_secret, refresh_token, opener=urlopen,
                 pacer=None, sleep=time.sleep):
        self.credentials = {"client_id": client_id, "client_secret": client_secret,
                            "refresh_token": refresh_token, "grant_type": "refresh_token"}
        self.opener = opener
        self.token = None
        self.expires = 0
        # 300 weighted units/minute, below the documented 600/user/minute.
        self.pacer = pacer if pacer is not None else RequestPacer(5)
        self.sleep = sleep

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
        except HTTPError as exc:
            # Only emit a small allowlist of error codes, never a response body,
            # error_description, token, client ID, or client secret.
            try:
                code = json.load(exc).get("error")
            except (OSError, ValueError, AttributeError):
                code = None
            safe = code if code in {"invalid_grant", "invalid_client", "deleted_client",
                                    "temporarily_unavailable", "server_error"} else "unknown"
            raise CalendarError(f"Google OAuth refresh failed ({safe}, HTTP {exc.code})") from None
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
                self.pacer.wait(3 if method == "PATCH" else 1)
                with self.opener(req, timeout=30) as response:
                    raw = response.read()
                return json.loads(raw) if raw else {}
            except HTTPError as exc:
                if exc.code == 401 and not refreshed:
                    self.token = None
                    refreshed = True
                    continue
                rate_limited = exc.code == 429
                if exc.code == 403:
                    try:
                        details = json.load(exc).get("error", {}).get("errors", [])
                        rate_limited = any(e.get("reason") in
                                           {"rateLimitExceeded", "userRateLimitExceeded"}
                                           for e in details)
                    except (OSError, ValueError, AttributeError, TypeError):
                        pass
                if (rate_limited or exc.code in (500, 502, 503, 504)) and attempt < 3 and method != "POST":
                    try:
                        retry_after = max(0, float(exc.headers.get("Retry-After", "0")))
                    except (ValueError, AttributeError):
                        retry_after = 0
                    if retry_after > 60:
                        raise CalendarError("Google Calendar cooldown exceeds retry budget; queue preserved", exc.code) from None
                    self.sleep(max(retry_after, 2 ** attempt + random.random()))
                    continue
                raise CalendarError(f"Google Calendar HTTP {exc.code} ({method})", exc.code) from exc
            except (URLError, OSError, ValueError) as exc:
                raise CalendarError(f"Google Calendar request failed ({method})") from exc
        raise CalendarError("Google Calendar retry limit reached")

    def get(self, event_id):
        try:
            return self.request(event_id=event_id)
        except CalendarError as exc:
            if exc.status == 410:
                return {"id": event_id, "status": "cancelled"}
            if exc.status == 404:
                return None
            raise

    def list(self, start, end):
        params = {"maxResults": 2500,
                  "singleEvents": "true", "showDeleted": "false",
                  "privateExtendedProperty": "managed_by=" + MARKER}
        if start is not None:
            params["timeMin"] = start
        if end is not None:
            params["timeMax"] = end
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


def fresh_price_job(state, job, now):
    record = state["prices"].get(f"{job['route']}:{job['day']}", {})
    observed = job.get("observed_at")
    checked = state["diagnostic"].get("checked_at")
    return (bool(observed) and observed == record.get("observed_at")
            and bool(checked) and datetime.fromisoformat(observed) >= datetime.fromisoformat(checked)
            and now - timedelta(hours=1) <= datetime.fromisoformat(observed) <= now + timedelta(minutes=5))


def note(state, message):
    warnings = state["diagnostic"].setdefault("warnings", [])
    if message not in warnings:
        warnings.append(message)


def retire_cancelled_alert(state, key, job):
    change_id = tags(job["body"]).get("change_id")
    retired = [key]
    if job["kind"] == "alert" and change_id:
        retired.extend(k for k, item in state["outbox"].items()
                       if item["kind"] == "push" and tags(item["body"]).get("change_id") == change_id)
    for retired_key in retired:
        state["outbox"].pop(retired_key, None)
    note(state, f"Deleted {job['kind']} not recreated: {job['route']}:{job['day']}")


def alert_slot(event):
    """Only explicitly owned, unattended all-day price alerts may be replaced."""
    if not owned(event) or tags(event)["kind"] != "alert" or event.get("attendees"):
        return None
    private = tags(event)
    day = private.get("date")
    try:
        parsed = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    start, end = event.get("start") or {}, event.get("end") or {}
    if (start.get("date") != day or start.get("dateTime") or end.get("dateTime")
            or end.get("date") != (parsed + timedelta(days=1)).isoformat()):
        return None
    return private["route"], day


def alert_order(event):
    """Order by quote time; support alerts written before the timestamp tag existed."""
    def instant(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else None
        except (AttributeError, TypeError, ValueError):
            return None
    created = instant(event.get("created")) or datetime.min.replace(tzinfo=TZ)
    observed = instant(tags(event).get("observed_at"))
    if observed is None:
        match = re.search(r"Ostatni odczyt:\s*(\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2})",
                          event.get("description") or "")
        if match:
            try:
                observed = datetime.strptime(match[1], "%d.%m.%Y, %H:%M").replace(tzinfo=TZ)
            except ValueError:
                pass
    return observed or created, created, event.get("id", "")


def keep_latest_alert(api, alerts, slot):
    candidates = [event for event in alerts.values() if alert_slot(event) == slot]
    if not candidates:
        return None
    winner = max(candidates, key=alert_order)
    identifier = winner["id"]
    if len(candidates) < 2:
        return identifier
    current = api.get(identifier)
    if (not current or current.get("status") != "confirmed" or alert_slot(current) != slot
            or any(current.get(k) != winner.get(k) for k in
                   ("id", "summary", "description", "colorId", "start", "end", "extendedProperties"))):
        raise CalendarError("Latest alert changed or cannot be verified; older alerts preserved")
    for previous in candidates:
        if previous["id"] == identifier:
            continue
        # Recheck identity before deletion; titles alone never establish ownership.
        prior = api.get(previous["id"])
        if prior and prior.get("status") != "cancelled":
            if alert_slot(prior) != slot or alert_order(prior) > alert_order(current):
                raise CalendarError("Previous alert changed; cleanup deferred")
            api.delete(previous["id"])
            deleted = api.get(previous["id"])
            if deleted and deleted.get("status") != "cancelled":
                raise CalendarError("Previous alert deletion not verified")
        alerts.pop(previous["id"], None)
    return identifier


def deliver(state, api, now, persist=lambda: None, clock=None):
    """Verify new alerts before replacing previous ones; keep unfinished work pending."""
    errors = []
    # Reconcile the whole owned calendar, including historical and distant flight dates.
    start = end = None
    try:
        existing = api.list(start, end)
    except CalendarError as exc:
        return [str(exc)]
    slots = {}
    alerts = {}
    for event in existing:
        if not owned(event) or event.get("status") == "cancelled":
            continue
        private = tags(event)
        if alert_slot(event):
            alerts[event["id"]] = event
        if private["kind"] == "flight":
            key = (private["route"], (event.get("start") or {}).get("date"))
            slots.setdefault(key, []).append(event)
    if not state["weekly_pending"]:
        for slot in sorted({alert_slot(e) for e in alerts.values()}):
            try:
                keep_latest_alert(api, alerts, slot)
            except CalendarError as exc:
                errors.append(f"Alert cleanup {slot[0]}:{slot[1]}: {exc}")
    # Migrate an old queue containing several alerts for one route/date.
    pending = {}
    for key, job in list(state["outbox"].items()):
        if job["kind"] == "alert":
            pending.setdefault((job["route"], job["day"]), []).append((key, job))
    for candidates in pending.values():
        winner = max(candidates, key=lambda pair: alert_order({**pair[1]["body"],
                                                             "id": pair[1]["event_id"]}))
        for key, job in candidates:
            if key != winner[0]:
                retire_cancelled_alert(state, key, job)
                persist()
    known_alerts = list(alerts.values()) + [j["body"] for j in state["outbox"].values()
                                          if j["kind"] == "alert"]
    for key, job in list(state["outbox"].items()):
        if job["kind"] == "push" and any(
                alert_slot(event) == (job["route"], job["day"])
                and alert_order(event)[0] > alert_order(job["body"])[0]
                for event in known_alerts):
            retire_cancelled_alert(state, key, job)
            persist()
    # Alerts and notifications first during hourly work; weekly work contains only flights.
    jobs = sorted(list(state["outbox"].items()),
                  key=lambda pair: (pair[1]["kind"] == "flight", pair[0]))
    for key, job in jobs:
        if key not in state["outbox"]:
            continue
        body = job["body"]
        identifier = job["event_id"]
        try:
            if job["kind"] == "push" and any(
                    item["kind"] == "alert" and tags(item["body"]).get("change_id")
                    == tags(body).get("change_id") for item in state["outbox"].values()):
                continue  # Do not send a push ahead of its unverified all-day alert.
            if job["kind"] == "flight":
                matches = slots.get((job["route"], job["day"]), [])
                if len(matches) > 1:
                    raise CalendarError(f"Duplicate owned flight: {job['route']}:{job['day']}; manual review required")
                if matches:
                    identifier = matches[0]["id"]
            prior = api.get(identifier)
            retired = False
            for recovery in range(8):
                if prior and prior.get("status") == "cancelled":
                    if job["kind"] != "flight":
                        retire_cancelled_alert(state, key, job)
                        persist()
                        retired = True
                        break
                    if not fresh_price_job(state, job, now):
                        raise CalendarError("Fresh Ryanair quote required before restoring a deleted price")
                    # Stable successor survives a lost response or a lost Git acknowledgement.
                    identifier = event_id("restored-price:" + identifier)
                    job["event_id"] = identifier
                    state["prices"][f"{job['route']}:{job['day']}"]["event_id"] = identifier
                    note(state, f"Restoring deleted price from fresh quote: {job['route']}:{job['day']}")
                    persist()
                    prior = api.get(identifier)
                    continue
                if prior and (not owned(prior) or prior.get("attendees")):
                    raise CalendarError("Event ID conflict; unrelated event left unchanged")
                if prior:
                    break
                if job["kind"] == "push":
                    when = clock() if clock else now
                    when = (when + timedelta(minutes=5)).replace(microsecond=0)
                    body["start"] = {"dateTime": when.isoformat(), "timeZone": "Europe/Warsaw"}
                    body["end"] = {"dateTime": (when + timedelta(minutes=1)).isoformat(),
                                   "timeZone": "Europe/Warsaw"}
                    persist()
                try:
                    api.insert(identifier, body)
                except CalendarError as exc:
                    if exc.status != 409:
                        raise
                    prior = api.get(identifier)
                    if prior is None:
                        # 409 + 404 does not prove deletion; the event may be temporarily unreadable.
                        raise CalendarError("Event ID exists but cannot be verified; queue preserved")
                    continue
                prior = api.get(identifier)
                if prior and prior.get("status") == "cancelled":
                    continue
                break
            else:
                raise CalendarError("Too many deleted event IDs; queue preserved for review")
            if retired:
                continue
            if job["kind"] == "flight" and prior and not verified(prior, identifier, body):
                api.patch(identifier, body)
                prior = api.get(identifier)
            # Preserve already-created push timing across runner crashes.
            if job["kind"] == "push" and prior:
                expected = {**body, "start": prior.get("start", {}), "end": prior.get("end", {})}
            else:
                expected = body
            if not verified(prior, identifier, expected):
                raise CalendarError("Readback differs from planned event")
            if job["kind"] == "alert":
                alerts[identifier] = prior
                if keep_latest_alert(api, alerts, (job["route"], job["day"])) != identifier:
                    retire_cancelled_alert(state, key, job)
                    persist()
                    continue
            if (job["kind"] == "flight" and len(identifier) == 64
                    and all(c in "0123456789abcdef" for c in identifier)):
                state["prices"][f"{job['route']}:{job['day']}"]["event_id"] = identifier
            del state["outbox"][key]
            persist()
        except CalendarError as exc:
            errors.append(f"{job['kind']} {job['route']}:{job['day']}: {exc}")

    if (state["weekly_pending"] and not state["outbox"] and not errors
            and not state["diagnostic"].get("errors")
            and state["diagnostic"].get("read", len(state["prices"]))):
        # Safe migration: never recognize ownership by title or description.
        try:
            for event in api.list(start, end):
                if (owned(event) and tags(event)["kind"] in ("alert", "push")
                        and not event.get("attendees")):
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
