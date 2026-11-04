import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from agent import Fare
from monitor import compare_and_store, run, save_recent_verifications

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
