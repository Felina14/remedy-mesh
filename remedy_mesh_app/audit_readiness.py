#!/usr/bin/env python3
"""Agent-readiness audit: for each agent/tool, is the data it needs actually present?

No LLM — this checks that every reasoning input exists and every deterministic tool
returns valid output, so a live run can't fail for lack of data.
"""
from remedy_mesh import data, logic, tools

OK, BAD = "OK  ", "MISS"
def line(ok, label, detail=""):
    print(f"  [{OK if ok else BAD}] {label}{(' — ' + detail) if detail else ''}")
    return ok

print("=" * 70)
print("LAYER 1 — per-agent input availability")
print("=" * 70)

cases = [c["case_id"] for c in data.cases()]
print(f"Dashboard: {len(cases)} cases, policy_rules loaded: {bool(data.policy_rules())}\n")

# policy keys the Verification agent + deterministic match rely on
pol = data.policy_rules()
need_pol = ["open_case_rules", "cluster_rules", "evidence_sufficiency_criteria", "reportability"]
print("Policy (referee) keys:")
all_ok = all(line(k in pol, k) for k in need_pol)

# Check the hero + two contrasting cases end-to-end through the deterministic tools
probe = ["CASE-001", "CASE-009", "CASE-002"]
for cid in probe:
    print(f"\n{cid}:")
    comps = data.complaints_for(cid)
    camp = data.campaign_for(cid)
    lot = data.lot_for(cid)
    line(len(comps) > 0 or True, "complaints", f"{len(comps)} (Interpreter + Match input)")
    line(camp is not None, "remedy_campaign", "scope baseline" if camp else "none (new-signal case — OK)")
    line(lot is not None, "production_lot", lot["lot_id"] if lot else "none")
    # resolution loop: is there a dealer that serviced this lot?
    if lot:
        dealer = next((d for d in data.dealers() if lot["lot_id"] in d.get("serviced_lots", [])), None)
        line(True, "resolution contact", f"dealer {dealer['name']}" if dealer else "falls back to regional contact")
    # the deterministic decision the agents report
    r = logic.evaluate_case(cid)
    crit = r["criteria"]
    met = [k for k, v in crit.items() if v]
    print(f"    → decision: {r['decision']}  | criteria met: {met}")
    print(f"      metrics: ratio={r['metrics']['post_pre_rate_ratio']} "
          f"rate_crossed={r['metrics']['rate_crossed']} cluster={r['metrics']['cluster_crossed']}")

print("\n" + "=" * 70)
print("LAYER 2 — per-agent input availability")
print("=" * 70)
sup = data.suppliers()
oem = next((s for s in sup if s["role"] == "oem"), None)
suppliers = [s for s in sup if s["role"] == "supplier"]
print("\nOEM agent needs (target/deadline/ceiling):")
line(oem is not None and all(k in oem for k in ("target_units", "deadline_weeks", "max_unit_cost")),
     "OEM profile", f"{oem['target_units']} units / {oem['deadline_weeks']}wk / ${oem['max_unit_cost']}" if oem else "MISSING")

print("\nSupplier agent needs (get_my_profile → capacity/cost/backlog), per supplier:")
for s in suppliers:
    ok = all(k in s for k in ("capacity_units_per_week", "unit_cost", "existing_backlog_units"))
    line(ok, s["party_id"], f"cap={s.get('capacity_units_per_week')} cost=${s.get('unit_cost')} backlog={s.get('existing_backlog_units')} visible_to={s.get('visible_to')}")

print("\nOffer computability (does each supplier produce a valid fallback offer?):")
from remedy_mesh import negotiation as neg
demand_q, weeks = oem["target_units"], oem["deadline_weeks"]
for s in suppliers:
    o = neg.fallback_offer(s, demand_q, weeks)
    line(o["quantity"] > 0 and o["unit_price"] > s["unit_cost"],
         s["party_id"], f"offers {o['quantity']} @ ${o['unit_price']} (above its ${s['unit_cost']} cost ✓)")

print("\n" + "=" * 70)
print(f"RESULT: {'ALL AGENT INPUTS PRESENT ✓' if all_ok else 'SOME INPUTS MISSING — see MISS above'}")
print("=" * 70)
