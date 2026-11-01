import unittest
from datetime import date
from ryanair_schedule import months_between, parse_schedule, report_coverage, ScheduleReadError

class ScheduleTests(unittest.TestCase):
    def test_month_boundary(self):
        self.assertEqual(list(months_between(date(2026, 11, 1), date(2027, 4, 30))),
                         [(2026,11),(2026,12),(2027,1),(2027,2),(2027,3),(2027,4)])
    def test_only_days_with_flights(self):
        payload = {"days": [{"day": 2, "flights": [{"flightNumber": "FR123"}]},
                            {"day": 3, "flights": []}]}
        self.assertEqual(parse_schedule(payload, 2027, 2, date(2027,2,1), date(2027,2,28)),
                         {date(2027,2,2)})
    def test_invalid_response_fails_closed(self):
        with self.assertRaises(ScheduleReadError):
            parse_schedule({"error": "unavailable"}, 2027, 2, date(2027,2,1), date(2027,2,28))
    def test_missing_prices(self):
        result = report_coverage({"PA:2027-02-02": "129"}, {"PA": {date(2027,2,2), date(2027,2,9)}})
        self.assertEqual(result["PA"]["missing_price"], ["2027-02-09"])

if __name__ == "__main__":
    unittest.main()
