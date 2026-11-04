"""Deliver only durable Ryanair alert entries, then independently verify delivery."""
import hashlib
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from calendar_sync import CalendarSyncError, GoogleCalendarApi, alert_payload, oauth_token
from monitor import REQUIRED_CALENDARS, STATE_PATH, acknowledge_alerts, load_pending, pending_path


def deliver(history, api, now):
    pending = load_pending(pending_path(history))
    receipts = {}
    attempts = Counter()
    for notice in pending:
        change_id = notice["change_id"]
        route = notice["route"]
        if route not in ("PA", "AP") or notice["direction"] not in ("down", "up"):
            raise ValueError("Invalid pending alert route or direction")
        if not re.fullmatch(rf"🔔 {route}•[0-9]+(?:,[0-9]+)?%", notice["title"]):
            raise ValueError("Invalid pending alert title")
        payload = alert_payload(notice, now)
        event_id = hashlib.sha256(change_id.encode()).hexdigest()
        receipts[change_id] = {}
        for calendar in REQUIRED_CALENDARS:
            try:
                matches = api.find_alerts(calendar, change_id)
                if len(matches) > 1:
                    print(f"{calendar}: duplicate alert {change_id}; left pending")
                    continue
                if matches:
                    receipts[change_id][calendar] = matches[0]["id"]
                    continue
                if attempts.total() >= 20 or attempts[calendar] >= 10:
                    continue
                attempts[calendar] += 1
                try:
                    api.create(calendar, {**payload, "id": event_id})
                except CalendarSyncError as exc:
                    if exc.status != 409:
                        raise
                receipts[change_id][calendar] = event_id
            except CalendarSyncError as exc:
                print(f"{calendar}: {exc}; alert {change_id} left pending")

    by_id = {item["change_id"]: item for item in pending}

    def verify(calendar, event_id, change_id, route, day):
        try:
            event = api.get_event(calendar, event_id)
        except CalendarSyncError as exc:
            print(f"{calendar}: {exc}; alert {change_id} left pending")
            return False
        expected = alert_payload(by_id[change_id], now)
        reminders = event.get("reminders") or {}
        private = (event.get("extendedProperties") or {}).get("private") or {}
        return (
            event.get("id") == event_id
            and event.get("status") == "confirmed"
            and all(event.get(key) == expected[key] for key in ("summary", "colorId", "start", "end"))
            and all(private.get(key) == value
                    for key, value in expected["extendedProperties"]["private"].items())
            and reminders.get("useDefault") is False
            and not reminders.get("overrides")
            and not event.get("attendees")
        )

    acknowledged = acknowledge_alerts(history, receipts, verifier=verify)
    print(f"Calendar insert attempts: {dict(attempts)}; "
          f"confirmed in all three: {len(acknowledged)}; pending: {len(pending) - len(acknowledged)}")
    return acknowledged


if __name__ == "__main__":
    token = oauth_token()
    if not token:
        raise SystemExit("Google Calendar OAuth credentials missing; queue left pending")
    deliver(STATE_PATH, GoogleCalendarApi(token), datetime.now(ZoneInfo("Europe/Warsaw")))
