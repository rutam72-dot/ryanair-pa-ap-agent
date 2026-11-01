"""Audit coverage of Ryanair PA/AP fares by month (no calendar writes)."""
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

HORIZON_DAYS = 180

def audit(path=Path("state/history.json"), today=None):
    today = today or datetime.now(ZoneInfo("Europe/Warsaw")).date()
    end = today + timedelta(days=HORIZON_DAYS)
    data = json.loads(path.read_text(encoding="utf-8"))
    months = sorted({(today + timedelta(days=i)).strftime("%Y-%m") for i in range(HORIZON_DAYS + 1)})
    counts = Counter()
    for key, price in data.items():
        try:
            route, day = key.split(":", 1)
            flight_day = date.fromisoformat(day)
        except (ValueError, AttributeError):
            continue
        if route in ("PA", "AP") and today <= flight_day <= end and price is not None:
            counts[(route, day[:7])] += 1
    print(f"Coverage audit: {today} through {end} (inclusive)")
    print("WARNING: These are confirmed fare dates, NOT a verified full timetable.")
    print("month      PA   AP")
    for month in months:
        pa, ap = counts[("PA", month)], counts[("AP", month)]
        print(f"{month}  {pa:3}  {ap:3}")
        if pa < 8 or ap < 8:
            print(f"  REVIEW {month}: sparse fare coverage; compare with official schedule")

if __name__ == "__main__":
    audit()
