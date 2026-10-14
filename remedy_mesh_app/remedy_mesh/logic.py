"""Deterministic reasoning — the 'never guesses' core.

This is the SAME logic verified in validate_layer1.py, ported to run against the
data access layer. It is plain Python (no LLM): every decision is computed from
records + policy_rules. The Gemini agents interpret and chase; THIS decides.

Runtime-obtained lot confirmations live in _CONFIRMED (populated by the
Resolution Loop) so the pending -> confirmed flip is produced, not pre-baked.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from . import data

# case_id -> {"confirmed_by": str, "confirmed_ts": str}, filled at runtime by the Resolution Loop.
_CONFIRMED: dict[str, dict] = {}


def _vnum(v: str) -> int:
    return int(v[3:])


def _to_date(s: str) -> date:
    return date.fromisoformat(s)


def _span_quarters(rows) -> float:
    ds = sorted(_to_date(x["date"]) for x in rows)
    return max(1.0, (ds[-1] - ds[0]).days / 91.0) if len(ds) > 1 else 1.0


def _dedup(rows, window_days: int):
    seen, out = {}, []
    for x in sorted(rows, key=lambda z: z["date"]):
        d = _to_date(x["date"])
        if x["vin"] in seen and (d - seen[x["vin"]]).days <= window_days:
            continue
        seen[x["vin"]] = d
        out.append(x)
    return out


def evaluate_case(case_id: str) -> dict:
    """Compute the evidence-sufficiency criteria and the Layer-1 decision for a case.

    Returns criteria flags, supporting metrics, and a decision string. No case is
    special-cased; the same rules run for every case.
    """
    pol = data.policy_rules()
    R, CL = pol["open_case_rules"], pol["cluster_rules"]
    cs = data.complaints_for(case_id)
    camp = data.campaign_for(case_id)
    lot = data.lot_for(case_id)

    dd = _dedup(cs, R["duplicate_window_days"])
    post = [x for x in dd if x["repair_completed"] and x["repair_date"] and x["date"] > x["repair_date"]]
    pre = [x for x in dd if x not in post]

    ratio = 0.0
    if camp and post and pre:
        ratio = (len(post) / _span_quarters(post)) / (len(pre) / _span_quarters(pre))
    rate_crossed = bool(camp) and len(post) >= R["min_post_remedy_failures"] \
        and ratio >= R["post_remedy_rate_multiplier_over_baseline"]
    cluster = any(v >= CL["cluster_min_complaints"] for v in Counter(x["state"] for x in dd).values())

    confirmed_rec = _CONFIRMED.get(case_id) or (
        {"confirmed_by": lot["confirmed_by"], "confirmed_ts": lot["confirmed_ts"]}
        if lot and lot.get("confirmed_by") else None
    )

    crit = {
        "vin_match": bool(lot) and any(
            _vnum(lot["vin_range_start"]) <= _vnum(x["vin"]) <= _vnum(lot["vin_range_end"]) for x in dd),
        "lot_match": bool(lot),
        "scope_drift_shown": bool(camp and lot) and (
            _vnum(lot["vin_range_start"]) > _vnum(camp["original_vin_range_end"])
            or lot["production_date"] > camp["original_production_date_end"]),
        "install_date_confirmed": confirmed_rec is not None,
        "duplicate_cleared": True,
        "threshold_crossed": rate_crossed or cluster,
    }

    evidence_ready = all(v for k, v in crit.items() if k != "install_date_confirmed") \
        and len(cs) >= R["min_complaints_to_open"]
    confirmable = evidence_ready and crit["install_date_confirmed"]

    if len(cs) == 0:
        decision = "no_signal"
    elif confirmable:
        decision = "confirmed_trigger_mesh"
    elif evidence_ready and not crit["install_date_confirmed"]:
        decision = "chase_record"            # hand to Resolution Loop
    elif crit["scope_drift_shown"] and not crit["threshold_crossed"]:
        decision = "monitor"
    elif crit["threshold_crossed"] and not crit["scope_drift_shown"]:
        decision = "remedy_quality_flag"     # failures, but within original scope -> not a scope case
    elif len(cs) < R["min_complaints_to_open"]:
        decision = "insufficient_evidence"
    else:
        decision = "keep_open"

    return {
        "case_id": case_id,
        "decision": decision,
        "criteria": crit,
        "metrics": {
            "raw_complaints": len(cs),
            "deduped": len(dd),
            "post_remedy_failures": len(post),
            "post_pre_rate_ratio": round(ratio, 2),
            "rate_threshold": R["post_remedy_rate_multiplier_over_baseline"],
            "rate_crossed": rate_crossed,
            "cluster_crossed": cluster,
        },
        "confirmed_record": confirmed_rec,
    }


def obtain_lot_record(case_id: str) -> dict:
    """Resolution Loop target: locate the service center that worked the lot and
    'obtain' the confirmation record (plant + production date). Flips
    install_date_confirmed from False to True for this case."""
    lot = data.lot_for(case_id)
    if not lot:
        return {"case_id": case_id, "found": False, "reason": "no lot on record"}
    dealer = next((d for d in data.dealers() if lot["lot_id"] in d.get("serviced_lots", [])), None)
    contact = (dealer or {}).get("regional_quality_contact", {"name": "Regional quality contact"})
    rec = {"confirmed_by": contact["name"], "confirmed_ts": date.today().isoformat() + "T00:00:00"}
    _CONFIRMED[case_id] = rec
    return {
        "case_id": case_id,
        "found": True,
        "lot_id": lot["lot_id"],
        "plant": lot["plant"],
        "production_date": lot["production_date"],
        "obtained_from": dealer["name"] if dealer else "regional escalation",
        "confirmed_record": rec,
    }
