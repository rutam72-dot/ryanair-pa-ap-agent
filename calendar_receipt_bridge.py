"""Fail-closed receipt bridge for the durable PA/AP outbox.

Run in an authenticated environment with Google Calendar read access.
This tool NEVER creates events, sends PUSH, or changes the monitor schedule.
Default mode verifies only; --apply rewrites the LOCAL pending file after readback.
Commit the resulting file with a compare-and-swap against the source Git SHA.
"""
import argparse
import json
import sys
from pathlib import Path

from calendar_sync import GoogleCalendarApi, oauth_token
from monitor import REQUIRED_CALENDARS, acknowledge_verified_calendar_events, load_pending, pending_path


def validate_receipts(raw):
    if not isinstance(raw, dict):
        raise ValueError("Receipt document must be a mapping keyed by change_id")
    for change_id, by_calendar in raw.items():
        if not isinstance(change_id, str) or not change_id:
            raise ValueError("Invalid change_id")
        if not isinstance(by_calendar, dict):
            raise ValueError("Each receipt must map calendar IDs to event IDs")
        if set(by_calendar) != set(REQUIRED_CALENDARS):
            raise ValueError("Each receipt must contain exactly the three required calendars")
        if any(not isinstance(v, str) or not v for v in by_calendar.values()):
            raise ValueError("Every calendar must provide an event ID")
    return raw


def verify_pending(history_path, receipts, api):
    """Return verified IDs WITHOUT mutating the outbox (dry-run)."""
    from calendar_sync import verify_alert_receipt
    pending = load_pending(pending_path(history_path))
    verified = []
    for item in pending:
        by_calendar = receipts.get(item["change_id"])
        if not by_calendar:
            continue
        if all(verify_alert_receipt(api, calendar, by_calendar[calendar],
                                    item["change_id"], item["route"], item["date"])
               for calendar in REQUIRED_CALENDARS):
            verified.append(item["change_id"])
    return sorted(verified)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=Path("state/history.json"))
    parser.add_argument("--receipts", type=Path, required=True,
                        help="Local JSON map change_id -> {calendar email: event id}; never commit OAuth secrets")
    parser.add_argument("--apply", action="store_true",
                        help="Acknowledge in local outbox ONLY after fresh independent Google readback")
    args = parser.parse_args(argv)
    receipts = validate_receipts(json.loads(args.receipts.read_text(encoding="utf-8")))
    if not pending_path(args.history).exists():
        raise FileNotFoundError("Durable outbox missing: cannot treat missing queue as delivered")
    token = oauth_token()
    if not token:
        raise RuntimeError("Google Calendar OAuth not configured; no acknowledgements made")
    api = GoogleCalendarApi(token)
    if args.apply:
        acknowledged = acknowledge_verified_calendar_events(args.history, receipts, api)
        print(json.dumps({"acknowledged": acknowledged, "mode": "local-apply"}))
    else:
        verified = verify_pending(args.history, receipts, api)
        print(json.dumps({"verified": verified, "mode": "dry-run", "queue_unchanged": True}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        print(f"Receipt bridge stopped safely: {exc}", file=sys.stderr)
        sys.exit(1)
