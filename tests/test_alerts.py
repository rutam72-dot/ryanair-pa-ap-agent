import unittest
from datetime import date
from decimal import Decimal
from agent import Fare
from alerts import alert

def fare(value):
    return Fare("PA", date(2026, 12, 4), Decimal(str(value)), True)

class AlertTests(unittest.TestCase):
    def test_ten_percent_drop(self):
        result = alert(fare(200), fare(180))
        self.assertEqual(result["color"], "green")
        self.assertEqual(result["title"], "🔔 PA•10,00%")
    def test_ten_percent_increase(self):
        result = alert(fare(200), fare(220))
        self.assertEqual(result["color"], "red")
        self.assertEqual(result["title"], "🔔 PA•10,00%")
    def test_every_confirmed_change(self):
        self.assertEqual(alert(fare(200), fare("199.99"))["title"], "🔔 PA•0,01%")
    def test_no_duplicate_when_unchanged(self):
        self.assertIsNone(alert(fare(200), fare(200)))
    def test_no_divide_by_zero(self):
        self.assertIsNone(alert(fare(0), fare(20)))

if __name__ == "__main__":
    unittest.main()
