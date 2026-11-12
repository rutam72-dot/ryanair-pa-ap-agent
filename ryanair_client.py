"""Ryanair fare-finder adapter; unofficial API, PLN-only, no fallback prices."""
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from agent import Fare, ROUTES
from request_limits import RYANAIR_PACER

BASE_URL = "https://services-api.ryanair.com/farfnd/v4/oneWayFares"
MAX_WINDOW_DAYS = 7

class RyanairReadError(RuntimeError):
    pass

def parse_fares(payload, route, start, end):
    if route not in ROUTES:
        raise ValueError("Unknown route")
    if not isinstance(payload, dict) or not isinstance(payload.get("fares"), list):
        raise RyanairReadError("Missing Ryanair fares array")
    output = []
    origin, destination = ROUTES[route]
    for entry in payload["fares"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("outbound"), dict):
            raise RyanairReadError("Invalid outbound flight")
        outbound = entry["outbound"]
        dep, arr = outbound.get("departureAirport"), outbound.get("arrivalAirport")
        if not isinstance(dep, dict) or not isinstance(arr, dict):
            raise RyanairReadError("Missing airports")
        if dep.get("iataCode") != origin or arr.get("iataCode") != destination:
            raise RyanairReadError("Unexpected flight route")
        try:
            day = date.fromisoformat(outbound["departureDate"][:10])
        except (KeyError, TypeError, ValueError) as exc:
            raise RyanairReadError("Invalid departure date") from exc
        if not start <= day <= end:
            raise RyanairReadError("Date outside requested window")
        price = outbound.get("price")
        if not isinstance(price, dict) or price.get("currencyCode") != "PLN":
            raise RyanairReadError("Price not confirmed in PLN")
        try:
            value = price["value"]
            if isinstance(value, bool):
                raise ValueError("Boolean price")
            amount = Decimal(str(value))
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            raise RyanairReadError("Invalid price") from exc
        if not amount.is_finite() or amount < 0:
            raise RyanairReadError("Invalid price")
        output.append(Fare(route, day, amount, True))
    return output

def fetch_fares(route, start, end, timeout=20, opener=None):
    """Read in seven-day windows to avoid treating a partial month as complete."""
    if route not in ROUTES:
        raise ValueError("Unknown route")
    if not isinstance(start, date) or not isinstance(end, date) or end < start:
        raise ValueError("Invalid dates")
    live = opener is None
    opener = opener or urlopen
    origin, destination = ROUTES[route]
    found = {}
    current = start
    while current <= end:
        stop = min(current + timedelta(days=MAX_WINDOW_DAYS - 1), end)
        params = urlencode({
            "departureAirportIataCode": origin,
            "arrivalAirportIataCode": destination,
            "outboundDepartureDateFrom": current.isoformat(),
            "outboundDepartureDateTo": stop.isoformat(),
            "currency": "PLN",
        })
        request = Request(BASE_URL + "?" + params, headers={
            "Accept": "application/json",
            "User-Agent": "ryanair-pa-ap-agent/0.2",
        })
        try:
            if live:
                RYANAIR_PACER.wait()
            with opener(request, timeout=timeout) as response:
                payload = json.load(response)
        except (OSError, ValueError) as exc:
            raise RyanairReadError(f"Ryanair read failed for {current}..{stop}") from exc
        for fare in parse_fares(payload, route, current, stop):
            old = found.get(fare.departure_date)
            if old is None or fare.price_pln < old.price_pln:
                found[fare.departure_date] = fare
        current = stop + timedelta(days=1)
    return [found[day] for day in sorted(found)]
