import tempfile
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path

from calendar_delivery import deliver
from calendar_sync import CalendarSyncError, alert_payload
from monitor import REQUIRED_CALENDARS, load_pending, pending_path, save_pending


class CalendarApi:
    def __init__(self):
        self.events = {}
        self.attempts = Counter()
        self.unreadable = None

    def find_alerts(self, calendar, change_id):
        return [event for (owner, _), event in self.events.items()
                if owner == calendar
                and event["extendedProperties"]["private"]["change_id"] == change_id]

    def create(self, calendar, payload):
        self.attempts[calendar] += 1
        key = calendar, payload["id"]
        if key in self.events:
            raise CalendarSyncError("conflict", 409)
        self.events[key] = {**payload, "status": "confirmed"}

    def get_event(self, calendar, event_id):
        if calendar == self.unreadable:
            raise CalendarSyncError("readback unavailable", 403)
        return self.events[calendar, event_id]


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history = Path(directory.name) / "history.json"
        self.api = CalendarApi()

    def run_delivery(self):
        return deliver(self.history, self.api, datetime(2026, 11, 4))

    def queue(self, count):
        save_pending(pending_path(self.history), [
            {"change_id": str(i), "route": "PA", "date": "2026-11-20",
             "direction": "down", "title": "🔔 PA•10,00%", "previous_pln": "100",
             "current_pln": "90", "delta_pln": "-10"}
            for i in range(count)
        ])

    def test_partial_readback_retries_without_duplicate_inserts(self):
        self.queue(1)
        self.api.unreadable = REQUIRED_CALENDARS[-1]
        self.assertEqual(self.run_delivery(), [])
        self.assertEqual(len(load_pending(pending_path(self.history))), 1)
        self.api.unreadable = None
        self.assertEqual(self.run_delivery(), ["0"])
        self.assertEqual(self.api.attempts.total(), 3)
        self.assertEqual(load_pending(pending_path(self.history)), [])

    def test_batch_and_calendar_limits(self):
        self.queue(25)
        self.assertEqual(len(self.run_delivery()), 6)
        self.assertEqual(self.api.attempts.total(), 20)
        self.assertEqual(len(load_pending(pending_path(self.history))), 19)
        self.api.events.clear()
        self.queue(12)
        self.api.attempts.clear()
        # The other calendars already have all the alerts.
        for calendar in REQUIRED_CALENDARS[1:]:
            for notice in load_pending(pending_path(self.history)):
                event_id = "existing-" + notice["change_id"]
                self.api.events[calendar, event_id] = {
                    **alert_payload(notice, datetime(2026, 11, 4)),
                    "id": event_id, "status": "confirmed",
                }
        self.assertEqual(len(self.run_delivery()), 10)
        self.assertEqual(self.api.attempts, {REQUIRED_CALENDARS[0]: 10})
