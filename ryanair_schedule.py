"""Read Ryanair published direct-flight timetable across the next 180 days.

Unofficial endpoint: unavailable months raise errors rather than being treated as
months without flights. Output is independent of fare-finder prices.
"""
import json
from datetime import date, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from agent import ROUTES

BASE = "https://services-api.ryanair.com/timtbl/3/schedules"

class ScheduleReadError(RuntimeError):
    pass

def months_between(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1

def parse_schedule(payload, year, month, start, end):
    if not isinstance(payload, dict) or not isinstance(payload.get("days"), list):
        raise ScheduleReadError(f"Invalid schedule for {year}-{month:02}")
    dates = set()
    for day in payload["days"]:
        if not isinstance(day, dict) or not isinstance(day.get("flights"), list):
            raise ScheduleReadError("Invalid timetable day")
        number = day.get("day")
        if not isinstance(number, int):
            raise ScheduleReadError("Invalid timetable day number")
        try:
            flight_day = date(year, month, number)
        except ValueError as exc:
            raise ScheduleReadError("Invalid timetable date") from exc
        if day["flights"] and start <= flight_day <= end:
            dates.add(flight_day)
    return dates

def fetch_schedule(route, start, end, opener=None, timeout=20):
    if route not in ROUTES:
        raise ValueError("Unknown route")
    opener = opener or urlopen
    origin, destination = ROUTES[route]
    dates = set()
    for year, month in months_between(start, end):
        url = f"{BASE}/{origin}/{destination}/years/{year}/months/{month}"
        try:
            with opener(Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "Mozilla/5.0"}), timeout=timeout) as response:
                payload = json.load(response)
        except (HTTPError, URLError, OSError, ValueError) as exc:
            raise ScheduleReadError(f"Timetable unreadable {route} {year}-{month:02}: {exc}") from exc
        dates.update(parse_schedule(payload, year, month, start, end))
    return dates

def report_coverage(history, schedules):
    """Return confirmed fare dates and scheduled dates without confirmed prices."""
    result = {}
    for route, dates in schedules.items():
        confirmed = {date.fromisoformat(key.split(":")[1])
                     for key, value in history.items()
                     if key.startswith(route + ":") and value is not None}
        result[route] = {
            "scheduled": len(dates),
            "priced": len(dates & confirmed),
            "missing_price": [day.isoformat() for day in sorted(dates - confirmed)]
        }
    return result

if __name__ == "__main__":
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from pathlib import Path
    today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    end = today + timedelta(days=180)
    history = json.loads(Path("state/history.json").read_text(encoding="utf-8"))
    schedules = {route: fetch_schedule(route, today, end) for route in ROUTES}
    print(json.dumps(report_coverage(history, schedules), ensure_ascii=False, indent=2))
