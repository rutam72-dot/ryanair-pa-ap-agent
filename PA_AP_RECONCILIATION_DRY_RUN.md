# PA/AP reconciliation — READ-ONLY PLAN (2026-11-04)

Window: 2026-11-04 through 2026-11-30, Europe/Warsaw.
Observed PA/AP and alert entries: rutam72@gmail.com = 94; b.prokglock@gmail.com = 94; ryanairsamolot@gmail.com = 92.
The first two calendars match by (date, summary, color). Third calendar differences:

- 2026-11-04: missing flight PA547,34 (color 6)
- 2026-11-04: missing flight AP1422,00 (color 11)
- 2026-11-17: missing historical alert 🔔 PA•22,45% (color 10)
- 2026-11-17: extra historical alert 🔔PA-19,9% (color 10)

Safety rules:
1. Do not delete the extra alert. Two distinct price changes on one date can be valid. Compare original event descriptions, private change_id and history before any reconciliation.
2. Do not blindly recreate the 2026-11-04 flight events. They are on the audit boundary and may be historical by the time reconciliation runs. Only repair historical flights with explicit user approval and verified source data.
3. Do not infer missing alert metadata or change_id from the title. An alert can be restored only if the original historical change is verifiable.
4. Do not edit personal events, combined trips, other calendars, production main or active automations.
5. Prepare a proposed operation list with event IDs and exact payloads, and obtain explicit approval before any calendar write.

Implementation status: branch-only queue crash replay guard and read-back receipt adapter exist; not deployed to the active ChatGPT automation. Pending outbox persistence across GitHub workflow runs remains unverified. Tests must complete on the latest commit before merge.
