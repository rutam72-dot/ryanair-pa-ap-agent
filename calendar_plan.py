"""Pure calendar synchronization planner. No network calls or calendar writes.

Events are deterministic and scoped to this agent; a future adapter must only
upsert/delete events tagged with the matching private extendedProperties.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from agent import Fare, ROUTES, flight_title

TIMEZONE = "Europe/Warsaw"
AGENT_TAG = "ryanair-pa-ap-agent"
COLOR = {"down": "10", "up": "11"}  # Google Calendar green/red color IDs

def flight_event(fare: Fare):
    if fare.route not in ROUTES or fare.price_pln is None:
        return None
    origin, destination = ROUTES[fare.route]
    day = fare.departure_date.isoformat()
    return {
        "summary": flight_title(fare),
        "start": {"date": day},
        "end": {"date": (fare.departure_date + timedelta(days=1)).isoformat()},
        "extendedProperties": {"private": {
            "agent": AGENT_TAG, "kind": "fare", "route": fare.route, "date": day,
        }},
        "description": f"Ryanair {origin}-{destination}. Cena potwierdzona w PLN. Monitoring, nie rezerwacja.",
    }

def change_event(notification, today=None):
    """One bell per route/flight date/observation date; update rather than duplicate."""
    if notification.get("direction") not in COLOR:
        raise ValueError("Unknown direction")
    route = notification["title"].split()[-1]
    if route not in ROUTES:
        raise ValueError("Unknown route")
    flight_date = date.fromisoformat(notification["date"])
    observed = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    return {
        "summary": notification["title"],
        "start": {"date": flight_date.isoformat()},
        "end": {"date": (flight_date + timedelta(days=1)).isoformat()},
        "colorId": COLOR[notification["direction"]],
        "extendedProperties": {"private": {
            "agent": AGENT_TAG, "kind": "alert", "route": route,
            "date": flight_date.isoformat(), "observed": observed.isoformat(),
        }},
    }

def event_key(event):
    tags = event.get("extendedProperties", {}).get("private", {})
    if tags.get("agent") != AGENT_TAG or tags.get("kind") not in ("fare", "alert"):
        raise ValueError("Not an agent-managed event")
    return tuple(tags.get(k, "") for k in ("kind", "route", "date", "observed"))

def plan_upserts(desired_events, existing_events):
    """Plan insert/update only; never delete unrelated events.

    Existing events must have id and the same private agent tags. Duplicates
    are reported for review rather than deleted automatically.
    """
    existing = {}
    duplicates = []
    for event in existing_events:
        try:
            k = event_key(event)
        except ValueError:
            continue
        if k in existing:
            duplicates.append(event.get("id"))
        else:
            existing[k] = event
    actions = []
    for event in desired_events:
        k = event_key(event)
        old = existing.get(k)
        if old is None:
            actions.append({"operation": "insert", "body": event})
        elif any(old.get(field) != event.get(field) for field in ("summary", "start", "end", "colorId", "description")):
            actions.append({"operation": "update", "id": old["id"], "body": event})
    return {"actions": actions, "duplicates_for_review": duplicates}
