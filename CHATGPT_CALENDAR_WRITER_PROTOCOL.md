# ChatGPT calendar writer — draft receipt protocol (NOT DEPLOYED)

GitHub collects Ryanair fare data. The ChatGPT writer protocol below describes the previous integration plan. The optional scheduled outbox delivery is described at the end.

1. Read `state/pending_alerts.json`; each entry has a persistent `change_id`.
2. For each change, inspect PA or AP events separately on all three calendars. Never create combined PA/AP events or alter personal entries.
3. If missing, create an alert titled `🔔 PA•22,45%` (or AP equivalent), no plus/minus sign; green for a decrease, red for an increase. Store `change_id` in Google Calendar `extendedProperties.private`.
4. Read back every created/found event, checking calendar ID, event ID, flight date, route, and private `change_id`.
5. Only when **all three** calendars independently verify the same change, call `acknowledge_alerts(history_path, receipts, verifier=...)`. The verifier must check real calendar data; never use unconditional `True`.
6. Keep any incompletely verified change pending and retry idempotently. Do not treat GitHub job success as delivery confirmation.

This is a protocol specification only. The ChatGPT automation has not been modified, and its access to private event properties has not been verified. Do not clear the queue until a real verifier is integrated and tested.

## Integration gate — no production writes yet

- The live ChatGPT automation currently reads `state/latest_verified.json` from `main`. It has **not** been switched to the pending outbox and does not persist receipts.
- First merge and verify durable `state/pending_alerts.json` publication from the scheduled GitHub workflow. A missing file means zero or unknown pending state, **not** proof of delivery.
- On each ChatGPT run, read the outbox from the exact production commit SHA; process only alerts with a stable `change_id`. Verify that the source commit has not changed before acknowledging.
- Read all three calendar events using their event IDs and verify `extendedProperties.private.change_id`, `route`, date and `managed_by`. If a calendar is inaccessible, keep the alert pending; do not infer success from other calendars.
- The connected Google Calendar interface may not expose private extended properties. Until real read-back is available, do not acknowledge any alert; document this integration blocker.
- The acknowledgement adapter `acknowledge_verified_calendar_events` is Python code; the live ChatGPT automation cannot invoke it merely by reading this Markdown. A separate authenticated bridge or approved automation update is required.
- Never commit a receipt that clears the outbox without concurrency protection (compare expected commit SHA, retry on conflict). Avoid exposing OAuth tokens.
- Cross-process test `tests/test_outbox_restart.py` is isolated, uses a temporary directory and a fake verifier, and does not establish GitHub-to-ChatGPT or Google Calendar end-to-end delivery.

## Scheduled outbox delivery

The monitor publishes the queue before the optional delivery step. The step
inserts only alerts, uses deterministic event IDs and independently reads
back each event before removing its change from the queue. The workflow
serializes runs and uses ordinary fast-forward pushes for state changes.
An interrupted run can recover events by their private change ID.

The delivery step is gated by the repository variable
`RYANAIR_CALENDAR_DELIVERY_VERIFIED=1`. Before setting it, verify the OAuth
account can insert and read an alert with private properties on all three
calendars. Configure the Actions secrets `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` for that account. Use
one writer for the pending queue. The scheduled delivery step starts disabled
unless the repository variable is set. Its logs report insert attempts and
confirmed deliveries separately.
