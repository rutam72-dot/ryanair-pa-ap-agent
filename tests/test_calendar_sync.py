import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from agent import Fare
from calendar_sync import CALENDARS, classify, sync_calendars, flight_payload, alert_payload

class FakeApi:
    def __init__(self, seeded=None):
        self.events = {calendar: [dict(e) for e in (seeded or [])] for calendar in CALENDARS}
        self.calls = []
    def list_events(self, calendar, start, end):
        return [e for e in self.events[calendar] if start <= e["start"]["date"] < end]
    def create(self, calendar, payload):
        self.calls.append(("create", calendar, payload["summary"]))
        obj = {**payload, "id": str(len(self.events[calendar]) + 1)}
        self.events[calendar].append(obj)
        return obj
    def patch(self, calendar, event_id, payload):
        self.calls.append(("patch", calendar, payload["summary"]))
        item = next(e for e in self.events[calendar] if e["id"] == event_id)
        item.update(payload)
        return item
    def delete(self, calendar, event_id):
        self.calls.append(("delete", calendar, event_id))
        self.events[calendar] = [e for e in self.events[calendar] if e["id"] != event_id]
        return {}

class CalendarSyncTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 4)
        self.now = datetime(2026, 11, 4, 8, 0, tzinfo=ZoneInfo("Europe/Warsaw"))

    def test_existing_personal_event_not_touched(self):
        personal = {"id": "x", "summary": "PA party", "description": "Family event",
                    "start": {"date": "2026-11-05"}}
        api = FakeApi([personal])
        sync_calendars([], {}, [], self.today, api=api, now=self.now)
        self.assertEqual(api.calls, [])

    def test_all_three_calendars_updated_once(self):
        fare = Fare("PA", date(2026, 11, 5), Decimal("199.99"), True)
        api = FakeApi()
        sync_calendars([fare], {}, [], self.today, api=api, now=self.now)
        self.assertEqual(len([x for x in api.calls if x[0] == "create"]), 3)
        self.assertEqual({x[1] for x in api.calls}, set(CALENDARS))
        sync_calendars([fare], {}, [], self.today, api=api, now=self.now)
        self.assertEqual(len(api.calls), 3)

    def test_preserve_existing_price_if_no_fresh_read(self):
        old = {"id": "x", "summary": "PA199,99", "description": "Monitoring oferty, Ryanair; historical price",
               "start": {"date": "2026-11-05"}}
        api = FakeApi([old])
        sync_calendars([], {"PA": {date(2026, 11, 5)}}, [], self.today, api=api, now=self.now)
        self.assertEqual(api.calls, [])

    def test_new_schedule_without_price_is_placeholder(self):
        api = FakeApi()
        sync_calendars([], {"AP": {date(2026, 11, 6)}}, [], self.today, api=api, now=self.now)
        self.assertEqual(api.calls[0][2], "✈ AP•Nie odczytano z Ryanair")

    def test_alert_sign_and_notices(self):
        notice = {"route": "AP", "date": "2026-11-05", "title": "🔔 AP•−0.01%",
                  "direction": "down", "previous_pln": "100", "current_pln": "99.99",
                  "delta_pln": "-0.01"}
        data = alert_payload(notice, self.now)
        self.assertEqual(data["colorId"], "10")
        self.assertEqual(classify(data), ("alert", "AP"))

    def test_legacy_recognized_but_private_not(self):
        self.assertEqual(classify({"summary": "AP1095,56", "description": "Monitoring oferty, Ryanair"}), ("flight", "AP"))
        self.assertIsNone(classify({"summary": "AP birthday", "description": "Private"}))

if __name__ == "__main__":
    unittest.main()
