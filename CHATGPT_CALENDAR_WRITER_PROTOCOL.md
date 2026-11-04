# ChatGPT calendar writer — draft receipt protocol (NOT DEPLOYED)

GitHub collects Ryanair fare data. ChatGPT remains the only Google Calendar writer. Never enable GitHub calendar writes in production.

1. Read `state/pending_alerts.json`; each entry has a persistent `change_id`.
2. For each change, inspect PA or AP events separately on all three calendars. Never create combined PA/AP events or alter personal entries.
3. If missing, create an alert titled `🔔 PA•22,45%` (or AP equivalent), no plus/minus sign; green for a decrease, red for an increase. Store `change_id` in Google Calendar `extendedProperties.private`.
4. Read back every created/found event, checking calendar ID, event ID, flight date, route, and private `change_id`.
5. Only when **all three** calendars independently verify the same change, call `acknowledge_alerts(history_path, receipts, verifier=...)`. The verifier must check real calendar data; never use unconditional `True`.
6. Keep any incompletely verified change pending and retry idempotently. Do not treat GitHub job success as delivery confirmation.

This is a protocol specification only. The ChatGPT automation has not been modified, and there is no live calendar receipt adapter. Do not clear the queue until a real verifier is integrated and tested.
