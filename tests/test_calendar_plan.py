import unittest
from datetime import date
from decimal import Decimal
from agent import Fare
from calendar_plan import flight_event, change_event, plan_upserts, event_key

class CalendarPlanTests(unittest.TestCase):
    def test_short_title_and_all_day(self):
        event = flight_event(Fare("PA", date(2026, 12, 4), Decimal("199"), True))
        self.assertEqual(event["summary"], "PA•199zł")
        self.assertEqual(event["end"]["date"], "2026-12-05")
    def test_missing_price_no_event(self):
        self.assertIsNone(flight_event(Fare("AP", date(2026, 12, 4), None, True)))
    def test_green_red(self):
        base = {"date": "2026-12-04", "title": "🔔10% PA"}
        self.assertEqual(change_event({**base, "direction": "down"}, date(2026, 11, 1))["colorId"], "10")
        self.assertEqual(change_event({**base, "direction": "up"}, date(2026, 11, 1))["colorId"], "11")
    def test_idempotent_and_unrelated_untouched(self):
        desired = flight_event(Fare("PA", date(2026, 12, 4), Decimal("199"), True))
        old = {**desired, "id": "owned-1"}
        unrelated = {"id": "personal", "summary": "Dentist"}
        self.assertEqual(plan_upserts([desired], [old, unrelated])["actions"], [])
        changed = {**desired, "summary": "PA•180zł"}
        result = plan_upserts([changed], [old, unrelated])
        self.assertEqual(result["actions"][0]["operation"], "update")
        self.assertEqual(result["actions"][0]["id"], "owned-1")
    def test_duplicates_not_deleted(self):
        event = flight_event(Fare("AP", date(2026, 12, 4), Decimal("199"), True))
        result = plan_upserts([event], [{**event, "id": "a"}, {**event, "id": "b"}])
        self.assertEqual(result["duplicates_for_review"], ["b"])

if __name__ == "__main__":
    unittest.main()
