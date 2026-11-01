"""Monitor Ryanair fares with durable history. No Google Calendar writes."""
import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

from agent import Fare
from alerts import alert
from ryanair_client import fetch_fares, RyanairReadError

STATE_PATH = Path(os.getenv("RYANAIR_STATE_PATH", "state/history.json"))
HORIZON_DAYS = int(os.getenv("RYANAIR_HORIZON_DAYS", "180"))

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

def run(today=None, path=STATE_PATH, fetcher=fetch_fares):
    today = today or datetime.now(ZoneInfo("Europe/Warsaw")).date()
    horizon = today + timedelta(days=HORIZON_DAYS)
    history = load_history(path)
    notices = []
    successes = 0
    for route in ("PA", "AP"):
        try:
            fares = fetcher(route, today, horizon)
        except (RyanairReadError, OSError, ValueError) as exc:
            print(f"{route}: Nie odczytano z Ryanair ({exc})")
            continue
        history, new_notices = compare_and_store(history, fares, today)
        successes += 1
        notices.extend(new_notices)
        print(f"{route}: {len(fares)} dates returned, {len([f for f in fares if f.price_pln is not None])} confirmed prices")
    if successes:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    for notice in notices:
        print(json.dumps(notice, ensure_ascii=False))
    if not successes:
        raise SystemExit("No Ryanair response confirmed; history unchanged")
    return notices

if __name__ == "__main__":
    run()
