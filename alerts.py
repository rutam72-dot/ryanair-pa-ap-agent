"""Alerts for every confirmed, comparable PLN fare change (not a booking quote)."""
from decimal import Decimal, ROUND_HALF_UP
from agent import Fare, price_change

def alert(previous: Fare, current: Fare):
    """Return one alert for any real price change; never invent percent at zero."""
    direction = price_change(previous, current)
    if direction is None or previous.price_pln is None or current.price_pln is None:
        return None
    if previous.price_pln <= 0:
        return None
    delta = current.price_pln - previous.price_pln
    percent = abs(delta) * Decimal("100") / previous.price_pln
    rounded = percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "−" if direction == "down" else "+"
    return {
        "title": f"🔔 {current.route}•{sign}{rounded:.2f}%",
        "route": current.route,
        "direction": direction,
        "color": "green" if direction == "down" else "red",
        "percent": str(percent),
        "previous_pln": str(previous.price_pln),
        "current_pln": str(current.price_pln),
        "delta_pln": str(delta),
    }
