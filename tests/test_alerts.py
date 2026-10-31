import unittest
from datetime import date
from decimal import Decimal
from agent import Fare
from alerts import alert

def fare(value):
    return Fare("PA", date(2026, 12, 4), Decimal(str(value)), True)

class AlertTests(unittest.TestCase):
    def test_ten_percent_drop(self):
        self.assertEqual(alert(fare(200), fare(180))["color"], "green")
    def test_ten_percent_increase(self):
        self.assertEqual(alert(fare(200), fare(220))["color"], "red")
    def test_below_threshold(self):
        self.assertIsNone(alert(fare(200), fare(181)))
    def test_no_divide_by_zero(self):
        self.assertIsNone(alert(fare(0), fare(20)))

if __name__ == "__main__":
    unittest.main()
