import io
import json
import unittest
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from ryanair_client import fetch_fares, parse_fares, RyanairReadError

def entry(day, value=100, currency="PLN"):
    return {"outbound": {
        "departureAirport": {"iataCode": "POZ"},
        "arrivalAirport": {"iataCode": "ALC"},
        "departureDate": day,
        "price": {"value": value, "currencyCode": currency},
    }}

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()

class RyanairClientTests(unittest.TestCase):
    def test_valid_pln(self):
        fares = parse_fares({"fares": [entry("2026-12-04", 199.99)]}, "PA", date(2026,12,1), date(2026,12,31))
        self.assertEqual(fares[0].price_pln, Decimal("199.99"))
    def test_reject_eur(self):
        with self.assertRaises(RyanairReadError):
            parse_fares({"fares": [entry("2026-12-04", 50, "EUR")]}, "PA", date(2026,12,1), date(2026,12,31))
    def test_reject_missing_fares(self):
        with self.assertRaises(RyanairReadError):
            parse_fares({}, "PA", date(2026,12,1), date(2026,12,31))
    def test_multiple_windows(self):
        requested = []
        def opener(request, timeout):
            q = parse_qs(urlparse(request.full_url).query)
            requested.append((q["outboundDepartureDateFrom"][0], q["outboundDepartureDateTo"][0]))
            day = q["outboundDepartureDateFrom"][0]
            return FakeResponse(json.dumps({"fares": [entry(day)]}).encode())
        fares = fetch_fares("PA", date(2026,11,1), date(2026,11,17), opener=opener)
        self.assertEqual(len(requested), 3)
        self.assertEqual(len(fares), 3)
        self.assertEqual(requested[-1], ("2026-11-15", "2026-11-17"))
    def test_fail_closed_on_bad_window(self):
        calls = 0
        def opener(request, timeout):
            nonlocal calls
            calls += 1
            return FakeResponse(json.dumps({"fares": []} if calls == 1 else {}).encode())
        with self.assertRaises(RyanairReadError):
            fetch_fares("PA", date(2026,11,1), date(2026,11,15), opener=opener)

if __name__ == "__main__":
    unittest.main()
