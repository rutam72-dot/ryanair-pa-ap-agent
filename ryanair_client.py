"""Ryanair fare-finder adapter. Only accepts validated PLN prices from Ryanair.

This endpoint is unofficial and may change. Errors never become estimated fares.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

from agent import Fare, ROUTES

BASE_URL = "https://services-api.ryanair.com/farfnd/v4/oneWayFares"

class RyanairReadError(RuntimeError):
    pass

def parse_fares(payload, route, start, end):
    """Validate the known fare-finder shape; fail closed on malformed responses."""
    if route not in ROUTES:
        raise ValueError("Unknown route")
    if not isinstance(payload, dict) or not isinstance(payload.get("fares"), list):
        raise RyanairReadError("Missing Ryanair fares array")
    output = []
    origin, destination = ROUTES[route]
    for entry in payload["fares"]:
        if not isinstance(entry, dict):
            raise RyanairReadError("Invalid fare entry")
        outbound = entry.get("outbound")
        if not isinstance(outbound, dict):
            raise RyanairReadError("Missing outbound flight")
        dep = outbound.get("departureAirport", {})
        arr = outbound.get("arrivalAirport", {})
        if not isinstance(dep, dict) or not isinstance(arr, dict):
            raise RyanairReadError("Invalid airports")
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
            raise RyanairReadError("Price is not confirmed in PLN")
        try:
            raw_amount = price["value"]
            if isinstance(raw_amount, bool):
                raise ValueError("Boolean is not a price")
            amount = Decimal(str(raw_amount))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise RyanairReadError("Invalid price amount") from exc
        if not amount.is_finite() or amount < 0:
            raise RyanairReadError("Invalid price amount")
        output.append(Fare(route, day, amount, True))
    return output

def fetch_fares(route, start, end, timeout=20):
    """Get Ryanair cheapest daily one-way fares for a route and date window."""
    if route not in ROUTES:
        raise ValueError("Unknown route")
    if not isinstance(start, date) or not isinstance(end, date) or end < start:
        raise ValueError("Invalid date window")
    origin, destination = ROUTES[route]
    params = urlencode({
        "departureAirportIataCode": origin,
        "arrivalAirportIataCode": destination,
        "outboundDepartureDateFrom": start.isoformat(),
        "outboundDepartureDateTo": end.isoformat(),
        "currency": "PLN",
    })
    request = Request(BASE_URL + "?" + params, headers={
        "Accept": "application/json",
        "User-Agent": "ryanair-pa-ap-agent/0.1",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RyanairReadError("Unable to read Ryanair prices") from exc
    return parse_fares(payload, route, start, end)
