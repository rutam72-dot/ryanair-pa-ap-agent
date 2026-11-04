"""Receipt bridge tests: fake Google API, no network, no calendar writes."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from calendar_receipt_bridge import validate_receipts, verify_pending
from monitor import REQUIRED_CALENDARS, pending_path, save_pending


class FakeApi:
    def __init__(self, missing=None):
        self.missing = missing
        self.reads = []
    def get_event(self, calendar, event_id):
        self.reads.append((calendar, event_id))
        if calendar == self.missing:
            raise ValueError("Calendar inaccessible")
        return {
            "id": event_id, "summary": "🔔 PA•10,00%",
            "start": {"date": "2026-11-20"}, "status": "confirmed",
            "extendedProperties": {"private": {
                "managed_by": "ryanair-pa-ap-agent", "kind": "alert",
                "route": "PA", "change_id": "stable-change-1"}},
        }


class ReceiptBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = Path(self.temp.name) / "history.json"
        self.notice = {"change_id": "stable-change-1", "route": "PA", "date": "2026-11-20"}
        save_pending(pending_path(self.history), [self.notice])
        self.receipts = {"stable-change-1": {c: "event-" + str(i) for i, c in enumerate(REQUIRED_CALENDARS)}}

    def test_all_three_real_readbacks_required(self):
        api = FakeApi()
        self.assertEqual(verify_pending(self.history, validate_receipts(self.receipts), api), ["stable-change-1"])
        self.assertEqual(len(api.reads), 3)
        self.assertEqual(json.loads(pending_path(self.history).read_text()), [self.notice])

    def test_one_calendar_failure_keeps_pending(self):
        api = FakeApi(missing=REQUIRED_CALENDARS[2])
        self.assertEqual(verify_pending(self.history, self.receipts, api), [])
        self.assertEqual(json.loads(pending_path(self.history).read_text()), [self.notice])

    def test_incomplete_receipts_rejected(self):
        with self.assertRaises(ValueError):
            validate_receipts({"stable-change-1": {REQUIRED_CALENDARS[0]: "event"}})

    def test_wrong_event_identity_rejected(self):
        class WrongApi(FakeApi):
            def get_event(self, calendar, event_id):
                event = super().get_event(calendar, event_id)
                event["extendedProperties"]["private"]["change_id"] = "other"
                return event
        self.assertEqual(verify_pending(self.history, self.receipts, WrongApi()), [])


if __name__ == "__main__":
    unittest.main()
