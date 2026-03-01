# CSI Incident Log

Last updated: 2026-03-01

Use this file for production-like incident entries during rebuild rollout.

## Entry Template
- Timestamp (UTC):
- Area:
- Symptom:
- Impact:
- Root cause:
- Fix implemented:
- Verification evidence:
- Follow-up prevention:

## Active/Recent Entries

### 2026-03-01 - Telegram stream silence (RSS/Reddit)
- Area: CSI digest delivery
- Symptom: Telegram channels only showed quality alerts, no regular content updates.
- Impact: Perceived CSI inactivity and low trust.
- Root cause: digest `last_sent_id` persisted ahead of current DB max ID; zero new rows selected.
- Fix implemented: cursor auto-heal reset when cursor exceeds max ID.
- Verification evidence: unit tests added (`test_digest_cursor_recovery.py`) and digest script output confirms reset path.
- Follow-up prevention: add cursor reset metric and delivery-health endpoint visibility.

