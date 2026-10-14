# Remedy Mesh — synthetic demo fixtures

Generated 2026-10-12. All company/VIN names are fictional. The hero case
(CASE-001, Veltrix Motors) is shaped on the real, publicly documented BMW
NHTSA campaigns 24V-104 and 26V422 — see its `grounding_note` field in
cases.json. Everything in suppliers.json / negotiation_log.json /
commitment_ledger.json is fully fictional and privacy-scoped by design
(each supplier record's `visible_to` lists only itself — enforce that
boundary in the UI, don't just state it).

Files:
- cases.json           — 30 rows for the case dashboard (screen 1)
- complaints.json       — 427 geocoded complaint records (screen 2 map + checklist)
- production_lots.json  — lot -> VIN range -> plant/date (the "lot match" / "install date" checks)
- activity_log.json     — the timestamped "agent visibly working" feed for the hero case
- suppliers.json        — OEM + 2 suppliers, each with only its own numbers
- negotiation_log.json  — proposing -> countering -> committed rounds (screen 3)
- commitment_ledger.json — screen 4 ledger; flip one row live for the "simulate default" demo
