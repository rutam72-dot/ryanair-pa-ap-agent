"""Persist last confirmed Ryanair fares; never invent prices or calendar events."""
import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from agent import Fare
from alerts import alert
from ryanair_client import fetch_fares, RyanairReadError

STATE_PATH = Path(os.getenv("RYANAIR_STATE_PATH", "state/history.json"))

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
    today = today or date.today()
    horizon = today + timedelta(days=30)
    history = load_history(path)
    all_notices = []
    successful = 0
    for route in ("PA", "AP"):
        try:
            fares = fetcher(route, today, horizon)
        except (RyanairReadError, OSError, ValueError) as exc:
            print(f"{route}: Nie odczytano z Ryanair ({exc})")
            continue
        history, notices = compare_and_store(history, fares, today)
        successful += 1
        all_notices.extend(notices)
        print(f"{route}: confirmed {len(fares)} fares")
    if successful:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(path)
    for notice in all_notices:
        print(json.dumps(notice, ensure_ascii=False))
    if not successful:
        raise SystemExit("No confirmed Ryanair response; no history update")
    return all_notices

if __name__ == "__main__":
    run()
