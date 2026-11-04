import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from agent import Fare
from monitor import compare_and_store, run, save_recent_verifications, acknowledge_alerts, save_pending, pending_path, REQUIRED_CALENDARS, load_pending, acknowledge_verified_calendar_events

class MonitorTests(unittest.TestCase):
    def test_ten_percent_change_and_history(self):
        d = date(2026, 12, 4)
        updated, notices = compare_and_store({"PA:2026-12-04": "200"}, [Fare("PA", d, Decimal("180"), True)], date(2026, 11, 1))
        self.assertEqual(updated["PA:2026-12-04"], "180")
        self.assertEqual(notices[0]["color"], "green")
    def test_missing_price_does_not_overwrite(self):
        d = date(2026, 12, 4)
        updated, notices = compare_and_store({"PA:2026-12-04": "200"}, [Fare("PA", d, None, True)], date(2026, 11, 1))
        self.assertEqual(updated["PA:2026-12-04"], "200")
        self.assertEqual(notices, [])
    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            def fake_fetch(route, start, end):
                return [Fare(route, date(2026, 11, 6), Decimal("199"), True)]
            run(date(2026, 11, 1), path, fake_fetch, schedule_fetcher=lambda route, start, end: set())
            self.assertTrue((Path(tmp) / "latest_verified.json").exists())
            self.assertIn('"PA:2026-11-06": "199"', path.read_text())
    def test_pending_alert_survives_following_run_without_ack(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text(json.dumps({"PA:2026-11-06": "200"}), encoding="utf-8")
            def fake_fetch(route, start, end):
                return [Fare(route, date(2026, 11, 6), Decimal("180"), True)] if route == "PA" else []
            run(date(2026, 11, 4), path, fake_fetch, schedule_fetcher=lambda *args: set())
            outbox = path.with_name("pending_alerts.json")
            first = json.loads(outbox.read_text(encoding="utf-8"))
            self.assertEqual(len(first), 1)
            self.assertTrue(first[0]["change_id"])
            run(date(2026, 11, 4), path, fake_fetch, schedule_fetcher=lambda *args: set())
            second = json.loads(outbox.read_text(encoding="utf-8"))
            self.assertEqual(first, second)

    def test_crash_after_outbox_write_does_not_duplicate_alert(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text(json.dumps({"PA:2026-11-06": "200"}), encoding="utf-8")
            existing = {"change_id": "original-1", "route": "PA", "date": "2026-11-06",
                        "previous_pln": "200", "current_pln": "180"}
            save_pending(pending_path(path), [existing])
            def fetch(route, start, end):
                return [Fare("PA", date(2026, 11, 6), Decimal("180"), True)] if route == "PA" else []
            notices = run(date(2026, 11, 4), path, fetch,
                          schedule_fetcher=lambda *args: set())
            self.assertEqual(notices, [])
            self.assertEqual(load_pending(pending_path(path)), [existing])
            self.assertEqual(json.loads(path.read_text())["PA:2026-11-06"], "180")

    def test_acknowledgement_requires_all_three_verified_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            notice = {"change_id": "change-1", "route": "PA", "date": "2026-11-06"}
            save_pending(pending_path(path), [notice])
            receipts = {"change-1": {calendar: "event-" + str(i)
                                      for i, calendar in enumerate(REQUIRED_CALENDARS)}}
            self.assertEqual(acknowledge_alerts(path, receipts, verifier=lambda *args: False), [])
            self.assertEqual(load_pending(pending_path(path)), [notice])
            self.assertEqual(acknowledge_alerts(path, {"change-1": {REQUIRED_CALENDARS[0]: "event-0"}},
                                                 verifier=lambda *args: True), [])
            self.assertEqual(load_pending(pending_path(path)), [notice])
            self.assertEqual(acknowledge_alerts(path, receipts, verifier=lambda *args: True), ["change-1"])
            self.assertEqual(load_pending(pending_path(path)), [])

    def test_real_receipt_adapter_refuses_nonmatching_calendar_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            save_pending(pending_path(path), [{"change_id": "c1", "route": "PA", "date": "2026-11-06"}])
            receipts = {"c1": {calendar: "id1" for calendar in REQUIRED_CALENDARS}}
            class ReadOnlyApi:
                def get_event(self, calendar, event_id):
                    return {"id": event_id, "status": "confirmed", "summary": "🔔 PA•10,00%",
                            "start": {"date": "2026-11-06"},
                            "extendedProperties": {"private": {
                                "managed_by": "ryanair-pa-ap-agent", "kind": "alert",
                                "route": "PA", "change_id": "WRONG"}}}
            self.assertEqual(acknowledge_verified_calendar_events(path, receipts, ReadOnlyApi()), [])
            self.assertEqual(len(load_pending(pending_path(path))), 1)

    def test_acknowledgement_refuses_missing_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                acknowledge_alerts(Path(tmp) / "history.json", {})

    def test_timestamped_snapshot_is_source_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latest_verified.json"
            fare = Fare("PA", date(2026, 11, 6), Decimal("199.99"), True)
            save_recent_verifications([fare], date(2026, 11, 4), path)
            import json
            recorded = json.loads(path.read_text(encoding="utf-8"))["PA:2026-11-06"]
            self.assertEqual(recorded["price_pln"], "199.99")
            self.assertFalse(recorded["basic_confirmed"])
            self.assertIn("T", recorded["observed_at"])
if __name__ == "__main__":
    unittest.main()
