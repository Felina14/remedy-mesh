#!/usr/bin/env python3
"""Reference Layer-2 path over real supplier data — proves the deterministic
backbone (allocation + ledger + selective re-negotiation) works end to end.

Offers here are produced by negotiation.fallback_offer() (the offline stand-in
for the scoped Gemini supplier agent). Run: python validate_layer2.py
"""
from remedy_mesh import data, negotiation as neg

CASE = "CASE-001"
oem = next(s for s in data.suppliers() if s["role"] == "oem")
suppliers = [s for s in data.suppliers() if s["role"] == "supplier"]
DEMAND, WEEKS, CEIL = oem["target_units"], oem["deadline_weeks"], oem["max_unit_cost"]

print(f"OEM need: {DEMAND} units in {WEEKS} wks, private ceiling ${CEIL}\n")

# 1. Each supplier's scoped offer (offline stand-in for the Gemini agent) — isolated per supplier
offers = [neg.fallback_offer(s, DEMAND, WEEKS) for s in suppliers]
print("Sanitized offers the orchestrator sees (no raw cost/capacity):")
for o in offers:
    print(f"  {o['supplier_id']}: {o['quantity']:>6} units @ ${o['unit_price']}")

# 2. Allocate
res = neg.allocate(offers, DEMAND, CEIL)
print(f"\nAllocation ({'FULLY MET' if res['fully_met'] else 'SHORTFALL '+str(res['unmet_units'])}),"
      f" total ${res['total_cost']:,}, over-ceiling used={res['over_ceiling_used']}:")
for a in res["allocation"]:
    flag = "  (over ceiling!)" if a["over_ceiling"] else ""
    print(f"  {a['supplier_id']}: {a['quantity']:>6} @ ${a['unit_price']}{flag}")

# 3. Record commitments
ledger = neg.CommitmentLedger(CASE)
ledger.record_allocation(res["allocation"])
print("\nCommitment ledger:")
for r in ledger.to_list():
    print(f"  {r['commitment_id']} {r['supplier_id']:7} {r['quantity']:>6} @ ${r['unit_price']} [{r['status']}]")

# 4. Selective re-negotiation — break SUP_A, re-solicit from the rest, repair ONLY that row
print("\n--- SIMULATE SUPPLIER DEFAULT: SUP_A breaks ---")
broken = next(r for r in ledger.rows.values() if r.supplier_id == "SUP_A")
ledger.break_commitment(broken.commitment_id)
need = broken.quantity

# re-solicit offers from the OTHER suppliers only (fresh scoped offers)
others = [s for s in suppliers if s["party_id"] != "SUP_A"]
reoffers = [neg.fallback_offer(s, need, WEEKS) for s in others]
# subtract what they already committed (headroom only)
already = {r.supplier_id: r.quantity for r in ledger.rows.values() if r.status == "Committed"}
for o in reoffers:
    o["quantity"] = max(0, o["quantity"] - already.get(o["supplier_id"], 0))
backfill = neg.allocate(reoffers, need, CEIL)
best = max(backfill["allocation"], key=lambda a: a["quantity"], default=None)
if best:
    ledger.repair(broken.commitment_id, best["supplier_id"], best["unit_price"], quantity=best["quantity"])
    covered = best["quantity"]
else:
    covered = 0

short = need - covered
print(f"Re-solicited backups for {need} units; best backfill = {covered} units"
      f"{f' (SHORTFALL {short} → escalate to OEM)' if short > 0 else ' (fully covered)'}")
print("\nLedger after break (note: ONLY the SUP_A row moved):")
for r in ledger.to_list():
    hist = " → ".join(h["status"] for h in r["status_history"])
    sf = f"  shortfall={r['shortfall']}" if r["shortfall"] else ""
    print(f"  {r['commitment_id']} {r['supplier_id']:7} {r['quantity']:>6} @ ${r['unit_price']} [{hist}]{sf}")
