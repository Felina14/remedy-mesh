#!/usr/bin/env python3
"""
Reference Layer-1 reasoning over the INPUT corpus.

This is NOT the product — it is (a) a regression test proving the input data
genuinely drives the scope-expansion decision, and (b) an executable SPEC for
the deterministic matching layer your agents build. No case is special-cased;
every decision is computed from the records + policy_rules.json.

Run: python3 validate_layer1.py
"""
import json, os
from datetime import date
from collections import Counter

INP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input")
L = lambda f: json.load(open(os.path.join(INP, f)))
cases, comp = L("cases.json"), L("complaints.json")
lots, camps = L("production_lots.json"), L("remedy_campaigns.json")
pol, sup = L("policy_rules.json"), L("suppliers.json")
camp_by_case = {c["case_id"]: c for c in camps}
lot_by_case = {l["case_id"]: l for l in lots}
R, CL = pol["open_case_rules"], pol["cluster_rules"]

vnum = lambda v: int(v[3:])
def span_q(rows):
    d = sorted(date.fromisoformat(x["date"]) for x in rows)
    return max(1, (d[-1] - d[0]).days) / 91.0 if len(d) > 1 else 1

def dedup(rows):
    seen, out = {}, []
    for x in sorted(rows, key=lambda z: z["date"]):
        d = date.fromisoformat(x["date"])
        if x["vin"] in seen and (d - seen[x["vin"]]).days <= R["duplicate_window_days"]:
            continue
        seen[x["vin"]] = d; out.append(x)
    return out

def evaluate(cid):
    cs = [x for x in comp if x["case_id"] == cid]
    camp, lot = camp_by_case.get(cid), lot_by_case.get(cid)
    dd = dedup(cs)
    post = [x for x in dd if x["repair_completed"] and x["repair_date"] and x["date"] > x["repair_date"]]
    pre = [x for x in dd if x not in post]
    ratio = (len(post) / span_q(post)) / (len(pre) / span_q(pre)) if (camp and post and pre) else 0.0
    rate_crossed = bool(camp) and len(post) >= R["min_post_remedy_failures"] \
        and ratio >= R["post_remedy_rate_multiplier_over_baseline"]
    cluster = any(v >= CL["cluster_min_complaints"] for v in Counter(x["state"] for x in dd).values())
    crit = {
        "vin_match": bool(lot) and any(vnum(lot["vin_range_start"]) <= vnum(x["vin"]) <= vnum(lot["vin_range_end"]) for x in dd),
        "lot_match": bool(lot),
        "scope_drift_shown": bool(camp and lot) and (
            vnum(lot["vin_range_start"]) > vnum(camp["original_vin_range_end"])
            or lot["production_date"] > camp["original_production_date_end"]),
        "install_date_confirmed": bool(lot and lot["confirmed_by"]),   # False in input; Resolution Loop obtains it
        "duplicate_cleared": True,
        "threshold_crossed": rate_crossed or cluster,
    }
    # Confirmable on evidence now, EXCEPT install_date_confirmed which the Resolution Loop must obtain.
    confirmable = all(v for k, v in crit.items() if k != "install_date_confirmed") and len(cs) >= R["min_complaints_to_open"]
    return crit, confirmable, dict(raw=len(cs), dedup=len(dd), post=len(post), ratio=ratio,
                                   rate_crossed=rate_crossed, cluster=cluster)

def decide(crit, conf, m):
    if m["raw"] == 0: return "no-signal (no complaints)"
    if conf: return "CONFIRM scope expansion -> chase record -> trigger mesh"
    if crit["scope_drift_shown"] and not crit["threshold_crossed"]: return "monitor (drift, below threshold)"
    if crit["threshold_crossed"] and not crit["scope_drift_shown"]: return "keep open (remedy-quality flag; no scope drift)"
    if m["raw"] < R["min_complaints_to_open"]: return "insufficient evidence -> close/no-signal"
    return "keep open (criteria incomplete)"

def layer2_feasibility():
    oem = next(s for s in sup if s["role"] == "oem")
    sups = [s for s in sup if s["role"] == "supplier"]
    window = oem["deadline_weeks"]
    print("\nLayer-2 feasibility:")
    print(f"  OEM target={oem['target_units']} deadline={window}wk ceiling=${oem['max_unit_cost']}")
    avail = {}
    for s in sups:
        a = s["capacity_units_per_week"] * window - s["existing_backlog_units"]  # effective, after backlog
        avail[s["party_id"]] = a
        print(f"    {s['party_id']} cap={s['capacity_units_per_week']}/wk cost=${s['unit_cost']} "
              f"backlog={s['existing_backlog_units']} -> effective {a} units in window")
    single_ok = any(a >= oem["target_units"] for a in avail.values())
    total = sum(avail.values())
    print(f"  single-supplier sufficient? {single_ok}  -> multi-supplier split {'optional' if single_ok else 'REQUIRED'}")
    print(f"  combined effective capacity={total} vs demand={oem['target_units']} "
          f"-> feasible with split: {total >= oem['target_units']}")

if __name__ == "__main__":
    print(f"{'case':9}{'raw':>5}{'post':>5}{'ratio':>7}  rateX clust drift -> decision")
    confirmed = []
    for c in cases:
        crit, conf, m = evaluate(c["case_id"])
        if m["raw"] == 0:
            continue
        if conf:
            confirmed.append(c["case_id"])
        hero = "  <<HERO" if c["is_hero_case"] else ""
        print(f"{c['case_id']:9}{m['raw']:5}{m['post']:5}{m['ratio']:7.2f}  "
              f"{str(m['rate_crossed'])[0]:<5} {str(m['cluster'])[0]:<5} "
              f"{str(crit['scope_drift_shown'])[0]:<5} -> {decide(crit, conf, m)}{hero}")
    print(f"\nconfirmed cases: {confirmed}")
    layer2_feasibility()
