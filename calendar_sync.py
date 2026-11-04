"""Optional, idempotent Google Calendar writer for verified Ryanair fares.

Enable with GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN.
OAuth token must have calendar.events permission to all three shared calendars.
Never infer a current fare from an old history value.
"""
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CALENDARS = ("rutam72@gmail.com", "b.prokglock@gmail.com", "ryanairsamolot@gmail.com")
MARKER = "ryanair-pa-ap-agent"
FLIGHT = re.compile(r"^\s*(?:✈\s*)?(PA|AP)(?=•|[0-9 ]|$)")
ALERT = re.compile(r"^\s*🔔\s*(PA|AP)(?=•|[+−\-0-9])")
COMBINED = re.compile(r"\b(?:PA\s*[+/]\s*AP|AP\s*[+/]\s*PA)\b", re.I)
ROUTE_LINK = {
    "PA": "https://www.ryanair.com/pl/pl/flights/flights-from-poznan-to-alicante",
    "AP": "https://www.ryanair.com/pl/pl/flights/flights-from-alicante-to-poznan",
}

class CalendarSyncError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

def _json_request(url, method="GET", token=None, payload=None, form=None):
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif form is not None:
        data = urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    for attempt in range(3):
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
            return json.loads(body) if body else {}
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2 and method != "POST":
                time.sleep(2 ** attempt)
                continue
            raise CalendarSyncError(f"Google Calendar HTTP {exc.code}: {method}", exc.code) from exc
        except (URLError, TimeoutError, ValueError) as exc:
            raise CalendarSyncError(f"Google Calendar request failed: {method}") from exc

def oauth_token():
    creds = [os.getenv(key) for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")]
    if not all(creds):
        return None
    response = _json_request("https://oauth2.googleapis.com/token", "POST", form={
        "client_id": creds[0], "client_secret": creds[1],
        "refresh_token": creds[2], "grant_type": "refresh_token",
    })
    if not response.get("access_token"):
        raise CalendarSyncError("OAuth returned no access token")
    return response["access_token"]

class GoogleCalendarApi:
    def __init__(self, token):
        self.token = token
    def request(self, calendar, method="GET", event_id=None, params=None, payload=None):
        url = "https://www.googleapis.com/calendar/v3/calendars/" + quote(calendar, safe="") + "/events"
        if event_id:
            url += "/" + quote(event_id, safe="")
        if params:
            url += "?" + urlencode(params)
        return _json_request(url, method, token=self.token, payload=payload)
    def list_events(self, calendar, start, end):
        params = {"timeMin": datetime.combine(date.fromisoformat(start), datetime.min.time(), ZoneInfo("Europe/Warsaw")).isoformat(),
                  "timeMax": datetime.combine(date.fromisoformat(end), datetime.min.time(), ZoneInfo("Europe/Warsaw")).isoformat(),
                  "maxResults": 2500, "singleEvents": "true", "showDeleted": "false"}
        out = []
        while True:
            page = self.request(calendar, params=params)
            out.extend(page.get("items", []))
            if not page.get("nextPageToken"):
                return out
            params["pageToken"] = page["nextPageToken"]
    def find_alerts(self, calendar, change_id):
        params = {"privateExtendedProperty": f"change_id={change_id}", "showDeleted": "false"}
        events = []
        while True:
            page = self.request(calendar, params=params)
            events.extend(page.get("items", []))
            if not page.get("nextPageToken"):
                return events
            params["pageToken"] = page["nextPageToken"]

    def create(self, calendar, payload):
        return self.request(calendar, "POST", params={"sendUpdates": "none"}, payload=payload)
    def patch(self, calendar, event_id, payload):
        return self.request(calendar, "PATCH", event_id=event_id, payload=payload)
    def get_event(self, calendar, event_id):
        return self.request(calendar, "GET", event_id=event_id)
    def delete(self, calendar, event_id):
        return self.request(calendar, "DELETE", event_id=event_id)

def classify(event):
    title = event.get("summary", "")
    private = (event.get("extendedProperties") or {}).get("private") or {}
    description = event.get("description", "")
    legacy_signature = any(s in description for s in ("Monitoring oferty", "Odczyt użyty do aktualizacji wpisu", "Zmiana ceny Ryanair", "Nie odczytano z Ryanair"))
    if private.get("managed_by") != MARKER and not legacy_signature:
        return None
    if COMBINED.search(title):
        return ("combined", None)
    match = ALERT.match(title)
    if match:
        return ("alert", match.group(1))
    match = FLIGHT.match(title)
    if match:
        return ("flight", match.group(1))
    return None

def event_date(event):
    return (event.get("start") or {}).get("date")

def display_price(amount):
    return format(Decimal(str(amount)).quantize(Decimal("0.01")), ".2f").replace(".", ",")

def color_for(amount):
    amount = Decimal(str(amount))
    if amount < 200: return "10"
    if amount < 400: return "9"
    if amount < 600: return "5"
    if amount < 800: return "6"
    return "11"

def flight_payload(route, day, fare, now):
    if fare is None:
        title = f"✈ {route}•Nie odczytano z Ryanair"
        description = ("Bezpośredni lot w rozkładzie Ryanair; ceny nie odczytano.\n"
                       "Brak potwierdzenia taryfy Basic i ceny koszyka.\n" + ROUTE_LINK[route])
        color = "8"
    else:
        title = f"✈ {route}•{display_price(fare.price_pln)}zł"
        description = (f"Odczyt: {now:%Y-%m-%d %H:%M} Europe/Warsaw\n"
                       f"Ryanair API: {display_price(fare.price_pln)} PLN (cena ofertowa)\n"
                       "Basic i cena koszyka: niepotwierdzone\n" + ROUTE_LINK[route])
        color = color_for(fare.price_pln)
    return {
        "summary": title, "description": description, "colorId": color,
        "start": {"date": day}, "end": {"date": (date.fromisoformat(day) + timedelta(days=1)).isoformat()},
        "transparency": "transparent", "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {"private": {"managed_by": MARKER, "route": route, "kind": "flight"}},
    }

def alert_payload(notice, now):
    route, day = notice["route"], notice["date"]
    return {
        "summary": notice["title"],
        "description": (f"Zmiana Ryanair: {notice['previous_pln']} → {notice['current_pln']} PLN\n"
                        f"Różnica: {notice['delta_pln']} PLN\n"
                        f"Odczyt: {now:%Y-%m-%d %H:%M} Europe/Warsaw\n"
                        + ROUTE_LINK[route]),
        "colorId": "10" if notice["direction"] == "down" else "11",
        "start": {"date": day}, "end": {"date": (date.fromisoformat(day) + timedelta(days=1)).isoformat()},
        "transparency": "transparent", "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {"private": {"managed_by": MARKER, "route": route, "kind": "alert", "change_id": notice.get("change_id", "")}},
    }

def _stable_description(value):
    """Ignore volatile read time when checking whether an event needs a patch."""
    return "\n".join(line for line in (value or "").splitlines() if not line.startswith("Odczyt:"))

def sync_calendars(fares, schedules, notices, today, api=None, now=None, limit=60):
    """Synchronize confirmed direct flights; keep historical prices if today's read failed.

    Missing credentials cause an explicit skip; credentials with errors cause a failure.
    Limits are per-calendar, and each update is a single-item batch.
    """
    if api is None:
        token = oauth_token()
        if token is None:
            print("Google Calendar SYNC SKIPPED: configure GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN")
            return False
        api = GoogleCalendarApi(token)
    now = now or datetime.now(ZoneInfo("Europe/Warsaw"))
    end = (today + timedelta(days=365)).isoformat()
    fresh = {(f.route, f.departure_date.isoformat()): f for f in fares
             if f.direct and f.price_pln is not None and today <= f.departure_date <= date.fromisoformat(end)}
    scheduled = {(route, d.isoformat()) for route, dates in schedules.items() for d in dates
                 if today <= d <= date.fromisoformat(end)}
    desired = sorted(set(fresh) | scheduled, key=lambda x: (x[1], x[0]))
    for calendar in CALENDARS:
        events = api.list_events(calendar, today.isoformat(), (today + timedelta(days=366)).isoformat())
        existing, duplicates, combined = {}, [], []
        known_alert_ids = set()
        for event in events:
            if event.get("status") == "cancelled":
                continue
            detected = classify(event)
            day = event_date(event)
            if detected is None or day is None:
                continue
            kind, route = detected
            if kind == "combined":
                combined.append(event)
                continue
            if kind == "alert":
                change_id = ((event.get("extendedProperties") or {}).get("private") or {}).get("change_id")
                if change_id:
                    known_alert_ids.add(change_id)
                    continue  # Preserve all previously recorded changes.
                continue  # Do not overwrite or delete legacy alerts.
            key = (kind, route, day)
            if key in existing:
                duplicates.append(event)
            else:
                existing[key] = event
        writes = 0
        # First persist new verified changes, so price alerts are not lost behind a migration batch.
        for notice in sorted(notices, key=lambda n: (n["date"], n["route"])):
            if writes >= limit: break
            day, route = notice["date"], notice["route"]
            payload = alert_payload(notice, now)
            change_id = notice.get("change_id")
            if not change_id:
                continue  # Refuse unsafe, non-idempotent alert creation.
            if change_id in known_alert_ids:
                continue
            api.create(calendar, payload)
            known_alert_ids.add(change_id)
            writes += 1
        for route, day in desired:
            if writes >= limit: break
            fare = fresh.get((route, day))
            prior = existing.get(("flight", route, day))
            if prior and fare is None:
                continue  # Preserve the last confirmed price and timestamp.
            payload = flight_payload(route, day, fare, now)
            if prior:
                # Avoid rewriting identical fares on all six scheduled checks.
                marker = ((prior.get("extendedProperties") or {}).get("private") or {})
                if (prior.get("summary") == payload["summary"] and prior.get("colorId") == payload["colorId"]
                        and _stable_description(prior.get("description")) == _stable_description(payload["description"])
                        and prior.get("start", {}).get("date") == day
                        and marker.get("managed_by") == MARKER):
                    continue
                api.patch(calendar, prior["id"], payload)
            else:
                api.create(calendar, payload)
            writes += 1
        # Remove only unambiguous agent-owned combined trips and duplicate copies.
        for item in combined + duplicates:
            if writes >= limit: break
            api.delete(calendar, item["id"])
            writes += 1
        # Remove historical agent-created flights and alerts; never touch personal entries.
        if writes < limit:
            old = api.list_events(calendar, "2024-01-01", today.isoformat())
            for item in old:
                if writes >= limit: break
                if classify(item) is not None and classify(item)[0] == "flight":
                    api.delete(calendar, item["id"])
                    writes += 1
        print(f"Calendar {calendar}: future flight/alert synchronization attempted; operations verified by API responses")
    return True


def verify_alert_receipt(api, calendar, event_id, change_id, route, day):
    """Read back an alert; fail closed if identity, route, or date differs."""
    if calendar not in CALENDARS or not event_id or not change_id or route not in ("PA", "AP"):
        return False
    try:
        event = api.get_event(calendar, event_id)
    except (CalendarSyncError, KeyError, ValueError, OSError):
        return False
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    return (event.get("status") != "cancelled"
            and private.get("managed_by") == MARKER
            and private.get("kind") == "alert"
            and private.get("route") == route
            and private.get("change_id") == change_id
            and event_date(event) == day
            and classify(event) == ("alert", route))
