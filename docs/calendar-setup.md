# Google Calendar integration — preparation only

No Google Calendar API writes are implemented or enabled in this stage.

## Requirements
- Three explicit Google Calendar IDs (not just email addresses).
- A dedicated Google service account with write permission to each calendar,
  or another authorized OAuth connection. Do not commit credentials.
- GitHub Actions secrets for credentials and calendar IDs; never log them.
- A dry-run that lists planned inserts/updates before enabling writes.

## Synchronization contract
- Each fare is a single all-day event: PA•199zł or AP•249zł.
- Alerts are separate all-day events on the flight date: 🔔10% PA.
- Google Calendar colorId 10 = green (drop); 11 = red (increase).
- Only events with private extendedProperties.agent=ryanair-pa-ap-agent
  may be considered for modification.
- Event identity is based on kind, route, flight date, and for alerts the
  observation date. Repeated runs must upsert rather than duplicate.
- Unrelated events must never be edited or deleted.
- Duplicate agent events are reported for review, not deleted automatically.
- Missing Ryanair prices must never be silently replaced by HeroFly estimates.

## Status
Pure planning module and unit tests only. No credentials, API adapter, or
calendar writes have been configured. Existing workflow does not import
calendar_plan.py.
