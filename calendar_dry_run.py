"""Read-only Google Calendar synchronization preview. NEVER writes events.

Requires service-account JSON in GOOGLE_SERVICE_ACCOUNT_JSON and three
calendar IDs. Google Calendar API scopes are read-only by design.
"""
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from agent import Fare
from calendar_plan import flight_event, plan_upserts

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
DEFAULT_CALENDARS = (
    "rutam72@gmail.com",
    "b.prokglock@gmail.com",
    "ryanairsamolot@gmail.com",
)

def build_desired(history, today):
    desired = []
    for key, value in sorted(history.items()):
        try:
            route, day = key.split(":", 1)
            departure = date.fromisoformat(day)
            if departure < today:
                continue
            fare = Fare(route, departure, Decimal(str(value)), True)
            event = flight_event(fare)
            if event is not None:
                desired.append(event)
        except (ValueError, ArithmeticError) as exc:
            raise ValueError(f"Invalid history record: {key}") from exc
    return desired

def list_existing(service, calendar_id, start, end):
    events = []
    token = None
    while True:
        response = service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat() + "T00:00:00Z",
            timeMax=end.isoformat() + "T00:00:00Z",
            singleEvents=True,
            maxResults=2500,
            pageToken=token,
        ).execute()
        events.extend(response.get("items", []))
        token = response.get("nextPageToken")
        if not token:
            return events

def preview(service, history, calendar_ids, today):
    from datetime import timedelta
    desired = build_desired(history, today)
    results = {}
    for calendar_id in calendar_ids:
        existing = list_existing(service, calendar_id, today, today + timedelta(days=181))
        plan = plan_upserts(desired, existing)
        counts = {"insert": 0, "update": 0}
        for action in plan["actions"]:
            counts[action["operation"]] += 1
        results[calendar_id] = {
            "readable": True, "desired": len(desired),
            "insert": counts["insert"], "update": counts["update"],
            "duplicates_for_review": len(plan["duplicates_for_review"]),
        }
    return results

def main():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    secret = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not secret:
        raise SystemExit("Missing GOOGLE_SERVICE_ACCOUNT_JSON; no calendars accessed")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(secret), scopes=SCOPES
    )
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    ids = tuple(os.getenv("GOOGLE_CALENDAR_IDS", ",".join(DEFAULT_CALENDARS)).split(","))
    if len(ids) != 3 or len(set(ids)) != 3 or not all(ids):
        raise SystemExit("Exactly three distinct calendar IDs required")
    history = json.loads(Path("state/history.json").read_text(encoding="utf-8"))
    today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    print(json.dumps(preview(service, history, ids, today), ensure_ascii=False, indent=2))
    print("DRY RUN ONLY: zero calendar writes")

if __name__ == "__main__":
    main()
