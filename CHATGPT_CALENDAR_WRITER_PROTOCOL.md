# ChatGPT calendar writer — draft receipt protocol (NOT DEPLOYED)

GitHub collects Ryanair fare data. ChatGPT remains the only Google Calendar writer. Never enable GitHub calendar writes in production.

1. Read `state/pending_alerts.json`; each entry has a persistent `change_id`.
2. For each change, inspect PA or AP events separately on all three calendars. Never create combined PA/AP events or alter personal entries.
3. If missing, create an alert titled `🔔 PA•22,45%` (or AP equivalent), no plus/minus sign; green for a decrease, red for an increase. Store `change_id` in Google Calendar `extendedProperties.private`.
4. Read back every created/found event, checking calendar ID, event ID, flight date, route, and private `change_id`.
5. Only when **all three** calendars independently verify the same change, call `acknowledge_alerts(history_path, receipts, verifier=...)`. The verifier must check real calendar data; never use unconditional `True`.
6. Keep any incompletely verified change pending and retry idempotently. Do not treat GitHub job success as delivery confirmation.

This is a protocol specification only. The ChatGPT automation has not been modified, and there is no live calendar receipt adapter. Do not clear the queue until a real verifier is integrated and tested.

## Integration gate — no production writes yet

- The live ChatGPT automation currently reads `state/latest_verified.json` from `main`. It has **not** been switched to the pending outbox and does not persist receipts.
- First merge and verify durable `state/pending_alerts.json` publication from the scheduled GitHub workflow. A missing file means zero or unknown pending state, **not** proof of delivery.
- On each ChatGPT run, read the outbox from the exact production commit SHA; process only alerts with a stable `change_id`. Verify that the source commit has not changed before acknowledging.
- Read all three calendar events using their event IDs and verify `extendedProperties.private.change_id`, `route`, date and `managed_by`. If a calendar is inaccessible, keep the alert pending; do not infer success from other calendars.
- The connected Google Calendar interface may not expose private extended properties. Until real read-back is available, do not acknowledge any alert; document this integration blocker.
- The acknowledgement adapter `acknowledge_verified_calendar_events` is Python code; the live ChatGPT automation cannot invoke it merely by reading this Markdown. A separate authenticated bridge or approved automation update is required.
- Never commit a receipt that clears the outbox without concurrency protection (compare expected commit SHA, retry on conflict). Avoid exposing OAuth tokens.
- Cross-process test `tests/test_outbox_restart.py` is isolated, uses a temporary directory and a fake verifier, and does not establish GitHub-to-ChatGPT or Google Calendar end-to-end delivery.
