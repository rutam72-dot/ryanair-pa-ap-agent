"""Monitor Ryanair fares with durable history and optional calendar sync."""
import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime
from uuid import uuid4

from agent import Fare
from alerts import alert
from ryanair_client import fetch_fares, RyanairReadError
from ryanair_schedule import fetch_schedule, report_coverage, ScheduleReadError
from fare_retries import retry_missing
from calendar_sync import sync_calendars

STATE_PATH = Path(os.getenv("RYANAIR_STATE_PATH", "state/history.json"))
HORIZON_DAYS = int(os.getenv("RYANAIR_HORIZON_DAYS", "365"))
LATEST_PATH = Path(os.getenv("RYANAIR_LATEST_PATH", "state/latest_verified.json"))

# Durable outbox: entries remain pending until a separate calendar writer explicitly acknowledges them.
def pending_path(history_path):
    return history_path.with_name("pending_alerts.json")

def load_pending(path):
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or any(not isinstance(x, dict) or not x.get("change_id") for x in data):
        raise ValueError("Pending alerts must be a list with change_id values")
    return data

def save_pending(path, notices):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(notices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def key(fare):
    return f"{fare.route}:{fare.departure_date.isoformat()}"

def load_history(path=STATE_PATH):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("History must be a JSON object")
    return data

def compare_and_store(history, fares, today):
    updated = {k: v for k, v in history.items() if k.split(":")[-1] >= today.isoformat()}
    notifications = []
    for fare in fares:
        if fare.departure_date < today or fare.price_pln is None:
            continue
        k = key(fare)
        old = updated.get(k)
        if old is not None:
            previous = Fare(fare.route, fare.departure_date, Decimal(str(old)), True)
            notice = alert(previous, fare)
            if notice:
                notifications.append({"date": fare.departure_date.isoformat(), **notice})
        updated[k] = str(fare.price_pln)
    return updated, notifications


# Receipts are accepted only after the ChatGPT calendar writer verifies each
# event on all three calendars. No receipt is inferred from a GitHub run.
REQUIRED_CALENDARS = ("rutam72@gmail.com", "b.prokglock@gmail.com", "ryanairsamolot@gmail.com")

def acknowledge_alerts(history_path, receipts, verifier=None):
    """Remove only IDs independently verified on all required calendars.

    receipts: mapping change_id -> mapping calendar_id -> Google event ID.
    verifier(calendar_id, event_id, change_id) must return True after checking
    the actual calendar event's private change_id, route and date. Fail closed.
    """
    if verifier is None:
        raise ValueError("An independent calendar event verifier is required")
    path = pending_path(history_path)
    pending = load_pending(path)
    if not isinstance(receipts, dict):
        raise ValueError("Receipts must be a mapping")
    acknowledged = set()
    for notice in pending:
        change_id = notice["change_id"]
        by_calendar = receipts.get(change_id)
        if not isinstance(by_calendar, dict) or set(by_calendar) != set(REQUIRED_CALENDARS):
            continue
        if not all(isinstance(by_calendar[calendar], str) and by_calendar[calendar]
                   and verifier(calendar, by_calendar[calendar], change_id)
                   for calendar in REQUIRED_CALENDARS):
            continue
        acknowledged.add(change_id)
    if acknowledged:
        save_pending(path, [notice for notice in pending if notice["change_id"] not in acknowledged])
    return sorted(acknowledged)

def save_recent_verifications(fares, today, path=LATEST_PATH):
    """Record the observation time per day; old history is NOT a fresh quote."""
    if not fares:
        return
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(records, dict):
        raise ValueError("Latest verified fares must be a JSON object")
    timestamp = datetime.now(ZoneInfo("Europe/Warsaw")).isoformat(timespec="seconds")
    records = {k: v for k, v in records.items()
               if isinstance(v, dict) and k.split(":")[-1] >= today.isoformat()}
    for fare in fares:
        if fare.direct and fare.price_pln is not None and fare.departure_date >= today:
            records[key(fare)] = {
                "price_pln": str(fare.price_pln),
                "observed_at": timestamp,
                "source": "Ryanair unofficial API",
                "currency": "PLN",
                "direct": True,
                "basic_confirmed": False,
            }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)

def run(today=None, path=STATE_PATH, fetcher=fetch_fares, latest_path=None, schedule_fetcher=fetch_schedule):
    today = today or datetime.now(ZoneInfo("Europe/Warsaw")).date()
    horizon = today + timedelta(days=HORIZON_DAYS)
    history = load_history(path)
    notices = []
    queue_path = pending_path(path)
    pending = load_pending(queue_path)
    fresh_fares = []
    successes = 0
    # Timetable and fare data are independent. A missing fare is not a missing flight.
    schedules = {}
    for route in ("PA", "AP"):
        try:
            schedules[route] = schedule_fetcher(route, today, horizon)
            print(f"{route}: timetable lists {len(schedules[route])} flight dates")
        except (ScheduleReadError, OSError, ValueError) as exc:
            print(f"{route}: Nie odczytano rozkladu Ryanair ({exc}); existing fares preserved")
    if schedules:
        coverage = report_coverage(history, schedules)
        for route, stats in coverage.items():
            print(f"{route}: schedule={stats['scheduled']} already_priced={stats['priced']} missing_prices={len(stats['missing_price'])}")
            print(f"{route}: dates without confirmed prices: {','.join(stats['missing_price'])}")
    for route in ("PA", "AP"):
        try:
            fares = fetcher(route, today, horizon)
        except (RyanairReadError, OSError, ValueError) as exc:
            print(f"{route}: Nie odczytano z Ryanair ({exc})")
            continue
        if route in schedules:
            recovered = retry_missing(route, schedules[route], history, today, fetcher=fetcher)
            fares = list(fares) + recovered
        fresh_fares.extend(fares)
        history, new_notices = compare_and_store(history, fares, today)
        successes += 1
        for notice in new_notices:
            notice["change_id"] = uuid4().hex
        notices.extend(new_notices)
        print(f"{route}: {len(fares)} dates returned, {len([f for f in fares if f.price_pln is not None])} confirmed prices")
    if successes:
        # Persist changes before advancing the price baseline. This is an outbox,
        # not proof of delivery to Google Calendar.
        if notices:
            pending.extend(notices)
            save_pending(queue_path, pending)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    if successes:
        target_latest = latest_path or (LATEST_PATH if path == STATE_PATH else path.with_name("latest_verified.json"))
        save_recent_verifications(fresh_fares, today, target_latest)
        if os.getenv("GOOGLE_CALENDAR_SYNC_ENABLED") == "1":
            sync_calendars(fresh_fares, schedules, pending, today)
        else:
            print("Google Calendar write is owned by the ChatGPT agent; GitHub sync disabled")
    for notice in notices:
        print(json.dumps(notice, ensure_ascii=False))
    if pending:
        print(f"Pending unacknowledged calendar alerts: {len(pending)}; see {queue_path}")
    if not successes:
        raise SystemExit("No Ryanair response confirmed; history unchanged")
    return notices

if __name__ == "__main__":
    run()
