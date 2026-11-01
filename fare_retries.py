"""Bounded retries for dates without confirmed Ryanair PLN fares."""
from ryanair_client import fetch_fares, RyanairReadError

def retry_missing(route, dates, history, today, fetcher=fetch_fares, limit=16):
    pending = sorted(d for d in dates if d >= today and f"{route}:{d.isoformat()}" not in history)
    recovered = []
    for day in pending[:limit]:
        try:
            recovered.extend(f for f in fetcher(route, day, day) if f.departure_date == day and f.price_pln is not None)
        except (RyanairReadError, OSError, ValueError) as exc:
            print(f"{route} {day}: Ryanair price unavailable: {exc}")
    print(f"{route}: retried {min(len(pending), limit)} dates; recovered {len(recovered)} fares; remaining pending after this batch: {max(0, len(pending) - len(recovered))}")
    return recovered
