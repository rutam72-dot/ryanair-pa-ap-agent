"""Price-change notifications: minimum 10 percent, both directions."""
from decimal import Decimal
from agent import Fare, price_change

THRESHOLD_PERCENT = Decimal("10")

def alert(previous: Fare, current: Fare):
    """Return calendar notification metadata or None.

    Percentage uses the previous confirmed price as baseline.
    Calendar color must be applied by the future Google Calendar integration.
    """
    direction = price_change(previous, current)
    if direction is None or previous.price_pln is None or current.price_pln is None:
        return None
    if previous.price_pln <= 0:
        return None
    percent = abs(current.price_pln - previous.price_pln) * 100 / previous.price_pln
    if percent < THRESHOLD_PERCENT:
        return None
    rounded = percent.quantize(Decimal("1"))
    return {
        "title": f"🔔{rounded}% {current.route}",
        "direction": direction,
        "color": "green" if direction == "down" else "red",
        "percent": str(percent),
    }
