"""Hourly PA/AP monitor. Standard library only; prepare never writes to Google."""
import argparse
import copy
import hashlib
from html import escape
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
import time as backoff
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

from agent import ROUTES
from ryanair_client import fetch_fares, RyanairReadError
from ryanair_schedule import fetch_schedule, ScheduleReadError

CALENDAR = "ryanairsamolot@gmail.com"
MARKER = "ryanair-pa-ap-agent"
TZ = ZoneInfo("Europe/Warsaw")
STATE_PATH = Path("state/runtime.json")
HORIZON = 365
ALERT_THRESHOLD = Decimal("8")
PUSH_THRESHOLD = Decimal("20")


def empty_state():
    return {"schema": 2, "prices": {}, "outbox": {}, "last_week": None,
            "weekly_pending": None, "diagnostic": {}}


def load_state(path=STATE_PATH):
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("schema") != 2 or not isinstance(state.get("prices"), dict)
            or not isinstance(state.get("outbox"), dict)):
        raise ValueError("Unsupported runtime state")
    if not all(key in state for key in ("last_week", "weekly_pending", "diagnostic")):
        raise ValueError("Incomplete runtime state")
    for key, record in state["prices"].items():
        route, day = key.split(":")
        if route not in ROUTES or date.fromisoformat(day).isoformat() != day:
            raise ValueError("Invalid price identity")
        for field in ("price", "reference"):
            amount = Decimal(record[field])
            if not amount.is_finite() or amount < 0:
                raise ValueError("Invalid state price")
        if datetime.fromisoformat(record["observed_at"]).tzinfo is None:
            raise ValueError("Observation needs timezone")
    for job in state["outbox"].values():
        private = job["body"]["extendedProperties"]["private"]
        if (job["kind"] not in ("flight", "alert", "push") or job["route"] not in ROUTES
                or date.fromisoformat(job["day"]).isoformat() != job["day"]
                or private.get("managed_by") != MARKER or private.get("schema") != "2"
                or private.get("kind") != job["kind"] or private.get("route") != job["route"]
                or private.get("date") != job["day"]
                or len(job["event_id"]) != 64
                or any(char not in "0123456789abcdef" for char in job["event_id"])):
            raise ValueError("Invalid queued event identity")
    return state


def save_state(state, path=STATE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n", encoding="utf-8")
    temp.replace(path)


def event_id(key):
    return hashlib.sha256(("simple-v2:" + key).encode()).hexdigest()


def booking_url(route, day):
    origin, destination = ROUTES[route]
    return "https://www.ryanair.com/pl/pl/trip/flights/select?" + urlencode({
        "adults": 1, "tpAdults": 1, "teens": 0, "tpTeens": 0,
        "children": 0, "tpChildren": 0, "infants": 0, "tpInfants": 0,
        "dateOut": day, "tpStartDate": day,
        "isConnectedFlight": "false", "tpIsConnectedFlight": "false",
        "isReturn": "false", "tpIsReturn": "false",
        "discount": 0, "tpDiscount": 0,
        "originIata": origin, "tpOriginIata": origin,
        "destinationIata": destination, "tpDestinationIata": destination,
    })


def price_color(price):
    price = Decimal(str(price))
    if price < 300:
        return "10"  # Google Calendar green
    if price < 500:
        return "5"   # yellow
    if price < 650:
        return "6"   # orange
    return "11"      # red


def metadata(kind, route, day, **extra):
    return {"private": {"managed_by": MARKER, "schema": "2", "kind": kind,
                        "route": route, "date": day, **extra}}


def observation_text(route, day, observed_at):
    observed = datetime.fromisoformat(observed_at).astimezone(TZ)
    # Calendar descriptions are HTML. Escape query separators so rendering cannot
    # interpret URL parameters as entities, and preserve the approved paragraph layout.
    url = escape(booking_url(route, day), quote=True)
    return (f"<p>({ROUTES[route][0]})-({ROUTES[route][1]})</p>"
            f"<p>Data lotu: {date.fromisoformat(day):%d.%m.%Y}</p>"
            f"<p>Ostatni odczyt: {observed:%d.%m.%Y, %H:%M} — czas Warszawy</p>"
            f'<p>Zarezerwuj lot:</p><p><a href="{url}">{url}</a></p>'
            "<p>Cena taryfy Basic niepotwierdzona.</p>")


def price_payload(route, day, record):
    return {
        "summary": f"{route} {Decimal(record['price']):.2f}",
        "description": observation_text(route, day, record["observed_at"]),
        "colorId": price_color(record["price"]),
        "start": {"date": day},
        "end": {"date": (date.fromisoformat(day) + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": metadata("flight", route, day),
    }


def week_due(state, now):
    monday = now.date() - timedelta(days=now.weekday())
    boundary = datetime.combine(monday, time(4, 7), TZ)
    if now < boundary:
        monday -= timedelta(days=7)
    week = monday.isoformat()
    return week if state["last_week"] != week else None


def collect(now, fare_fetcher=fetch_fares, schedule_fetcher=fetch_schedule, clock=None):
    """One request per scheduled date; never infer freshness from old history."""
    clock = clock or (lambda: datetime.now(TZ))
    today = now.date()
    end = today + timedelta(days=HORIZON)
    expected, errors, quotes = set(), [], {}
    for route in ROUTES:
        try:
            for day in schedule_fetcher(route, today, end):
                if not today <= day <= end:
                    raise ValueError("Timetable date outside horizon")
                expected.add((route, day))
        except (ScheduleReadError, OSError, ValueError) as exc:
            errors.append(f"{route}: timetable: {exc}")

    def read(route, day):
        for attempt in range(2):
            try:
                fares = fare_fetcher(route, day, day)
                if any(f.route != route or f.departure_date != day for f in fares):
                    raise ValueError("Fare identity differs from request")
                prices = [f.price_pln for f in fares if f.price_pln is not None and f.direct]
                return (min(prices) if prices else None, clock().isoformat(timespec="seconds"))
            except (RyanairReadError, OSError, ValueError):
                if attempt:
                    raise
                backoff.sleep(1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(read, route, day): (route, day)
                   for route, day in sorted(expected)}
        for future in as_completed(futures):
            route, day = futures[future]
            try:
                amount, observed_at = future.result()
                if amount is not None:
                    quotes[f"{route}:{day.isoformat()}"] = {
                        "price": str(amount),
                        "observed_at": observed_at,
                    }
            except (RyanairReadError, OSError, ValueError) as exc:
                errors.append(f"{route}:{day}: {exc}")
    complete = bool(expected) and not errors and len(quotes) == len(expected)
    return quotes, {"expected": len(expected), "read": len(quotes),
                    "missing": len(expected) - len(quotes), "errors": sorted(errors),
                    "complete": complete, "checked_at": now.isoformat(timespec="seconds")}


def prepare(state, quotes, diagnostic, now):
    """Durably plan every effect before delivery. No network calls."""
    state = copy.deepcopy(state)
    if state["weekly_pending"]:
        raise ValueError("Finish pending weekly delivery before collecting new observations")
    state["diagnostic"] = diagnostic
    due = week_due(state, now)
    reset = due is not None and diagnostic["complete"]
    if reset:
        # Weekly reset intentionally retires previous alert and push jobs.
        state["outbox"] = {k: v for k, v in state["outbox"].items()
                           if v["kind"] == "flight"}
        state["weekly_pending"] = due
    today = now.date().isoformat()
    state["prices"] = {k: v for k, v in state["prices"].items()
                       if k.split(":")[1] >= today}
    # Never deliver a stale price-change push for a flight date already in the past.
    state["outbox"] = {k: v for k, v in state["outbox"].items()
                       if v["day"] >= today}
    for key, quote in sorted(quotes.items()):
        route, day = key.split(":")
        old = state["prices"].get(key)
        reference = Decimal(old["reference"]) if old else Decimal(quote["price"])
        current = Decimal(quote["price"])
        record = {**quote, "reference": str(current if reset else reference)}
        if old and not reset and reference > 0:
            percent = abs(current - reference) * 100 / reference
            if percent > ALERT_THRESHOLD:
                change = uuid4().hex
                percent_text = f"{percent.quantize(Decimal('0.01'), rounding='ROUND_HALF_UP'):.2f}".replace(".", ",")
                title = f"🔔{route} {percent_text}%"
                payload = {
                    "summary": title,
                    "description": (f"<p>Zmiana: {reference:.2f} → {current:.2f} PLN</p>" +
                                    observation_text(route, day, quote["observed_at"])),
                    "colorId": "10" if current < reference else "11",
                    "start": {"date": day},
                    "end": {"date": (date.fromisoformat(day) + timedelta(days=1)).isoformat()},
                    "transparency": "transparent",
                    "reminders": {"useDefault": False, "overrides": []},
                    "extendedProperties": metadata("alert", route, day, change_id=change),
                }
                state["outbox"][change] = {"kind": "alert", "route": route, "day": day,
                                           "event_id": event_id(change), "body": payload}
                if percent >= PUSH_THRESHOLD:
                    push = copy.deepcopy(payload)
                    push["extendedProperties"] = metadata("push", route, day, change_id=change)
                    # Start time is assigned immediately before the first insert attempt.
                    push.pop("start")
                    push.pop("end")
                    push["reminders"] = {"useDefault": False,
                                         "overrides": [{"method": "popup", "minutes": 0}]}
                    state["outbox"][change + "-push"] = {
                        "kind": "push", "route": route, "day": day,
                        "event_id": event_id(change + "-push"), "body": push}
                record["reference"] = str(current)
        state["prices"][key] = record
        state["outbox"]["price:" + key] = {
            "kind": "flight", "route": route, "day": day,
            "event_id": event_id("price:" + key), "body": price_payload(route, day, record)}
    return state


def summary(state):
    from collections import Counter
    return {"calendar": CALENDAR, "operations": dict(Counter(
        job["kind"] for job in state["outbox"].values())),
        "weekly_reset": state["weekly_pending"], "diagnostic": state["diagnostic"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preview", "stage", "deliver"))
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    args = parser.parse_args()
    now = datetime.now(TZ)
    state = load_state(args.state)
    if args.mode == "deliver":
        from calendar_sync import GoogleCalendarApi, deliver
        api = GoogleCalendarApi.from_environment()
        errors = deliver(state, api, now, persist=lambda: save_state(state, args.state),
                         clock=lambda: datetime.now(TZ))
        state["diagnostic"]["delivery_errors"] = errors
        save_state(state, args.state)
        print(json.dumps({**summary(state), "delivery_errors": errors}, ensure_ascii=False))
        return int(bool(errors))
    if not state["weekly_pending"]:
        quotes, diagnostic = collect(now)
        state = prepare(state, quotes, diagnostic, now)
    if args.mode == "stage":
        save_state(state, args.state)
    print(json.dumps(summary(state), ensure_ascii=False, indent=2))
    if args.mode == "preview":
        examples = []
        for route in ROUTES:
            keys = sorted(key for key in state["prices"] if key.startswith(route + ":"))
            for key in keys[:3]:
                _, day = key.split(":")
                examples.append(price_payload(route, day, state["prices"][key]))
        print(json.dumps({"preview_examples": examples}, ensure_ascii=False, indent=2))
        print("PREVIEW: no file changes; no Google Calendar access.")
        return int(not state["diagnostic"].get("complete"))
    # Partial observations are kept and delivered. The workflow flags them afterward.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
