import unittest
from datetime import date
from decimal import Decimal
from agent import Fare, flight_title, price_change, eligible

class AgentTests(unittest.TestCase):
    def test_title(self):
        fare = Fare("PA", date(2026, 12, 4), Decimal("199.00"), True)
        self.assertEqual(flight_title(fare), "PA•199zł")

    def test_missing_price(self):
        fare = Fare("AP", date(2026, 12, 7), None, True)
        self.assertEqual(flight_title(fare), "AP•Nie odczytano z Ryanair")

    def test_change(self):
        before = Fare("PA", date(2026, 12, 4), Decimal("200"), True)
        after = Fare("PA", date(2026, 12, 4), Decimal("150"), True)
        self.assertEqual(price_change(before, after), "down")

    def test_past(self):
        fare = Fare("AP", date(2026, 10, 30), Decimal("120"), True)
        self.assertFalse(eligible(fare, date(2026, 11, 1)))

    def test_no_other_source(self):
        with self.assertRaises(ValueError):
            Fare("PA", date(2026, 12, 4), Decimal("200"), True, "HeroFly")

if __name__ == "__main__":
    unittest.main()
