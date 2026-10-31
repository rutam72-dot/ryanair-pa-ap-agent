import unittest
from datetime import date
from decimal import Decimal
from ryanair_client import parse_fares, RyanairReadError

class RyanairClientTests(unittest.TestCase):
    def test_valid_pln(self):
        payload = {"fares": [{"outbound": {
            "departureAirport": {"iataCode": "POZ"},
            "arrivalAirport": {"iataCode": "ALC"},
            "departureDate": "2026-12-04T08:00:00",
            "price": {"value": 199.99, "currencyCode": "PLN"}
        }}]}
        fares = parse_fares(payload, "PA", date(2026,12,1), date(2026,12,31))
        self.assertEqual(fares[0].price_pln, Decimal("199.99"))

    def test_reject_eur(self):
        payload = {"fares": [{"outbound": {
            "departureAirport": {"iataCode": "POZ"},
            "arrivalAirport": {"iataCode": "ALC"},
            "departureDate": "2026-12-04",
            "price": {"value": 50, "currencyCode": "EUR"}
        }}]}
        with self.assertRaises(RyanairReadError):
            parse_fares(payload, "PA", date(2026,12,1), date(2026,12,31))

    def test_reject_missing_fares(self):
        with self.assertRaises(RyanairReadError):
            parse_fares({}, "AP", date(2026,12,1), date(2026,12,31))

if __name__ == "__main__":
    unittest.main()
