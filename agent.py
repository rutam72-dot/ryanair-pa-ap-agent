"""Core domain model for Ryanair PA/AP. No network or calendar side effects."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

ROUTES = {"PA": ("POZ", "ALC"), "AP": ("ALC", "POZ")}

@dataclass(frozen=True)
class Fare:
    route: str
    departure_date: date
    price_pln: Optional[Decimal]
    direct: bool
    source: str = "Ryanair"

    def __post_init__(self):
        if self.route not in ROUTES:
            raise ValueError("Route must be PA or AP")
        if self.source != "Ryanair":
            raise ValueError("Only Ryanair-confirmed fares are allowed")
        if not self.direct:
            raise ValueError("Only direct flights are allowed")
        if self.price_pln is not None and (not self.price_pln.is_finite() or self.price_pln < 0):
            raise ValueError("Invalid price")

def flight_title(fare: Fare) -> str:
    if fare.price_pln is None:
        return f"{fare.route}•Nie odczytano z Ryanair"
    amount = format(fare.price_pln.normalize(), "f")
    return f"{fare.route}•{amount}zł"

def price_change(previous: Fare, current: Fare) -> Optional[str]:
    """Return 'down', 'up' or None; no comparisons against missing data."""
    if (previous.route, previous.departure_date) != (current.route, current.departure_date):
        raise ValueError("Cannot compare different flights")
    if previous.price_pln is None or current.price_pln is None:
        return None
    if current.price_pln < previous.price_pln:
        return "down"
    if current.price_pln > previous.price_pln:
        return "up"
    return None

def eligible(fare: Fare, today: date) -> bool:
    return fare.departure_date >= today
