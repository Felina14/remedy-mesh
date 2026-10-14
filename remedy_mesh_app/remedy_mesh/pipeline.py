"""Step 4 — the gate: run Layer 1, and on a confirmed signal run Layer 2, emitting
a unified timeline the UI renders. This is the end-to-end demo spine.
"""
from . import data, logic, layer2, resolution
from .timeline import Timeline


async def run_pipeline(case_id: str, use_llm: bool = False, confirm: bool = True, on_event=None) -> dict:
    """`on_event`, when given, is called the instant each timeline event is added
    (see Timeline.add) — this is what server.api_run_stream uses to turn the run
    into a live Server-Sent-Events feed instead of one long silent wait."""
    tl = Timeline(case_id, on_event=on_event)

    # ---- Layer 1 ----
    n = len(data.complaints_for(case_id))
    tl.add("layer1", "EvidenceInterpreter", f"Structured {n} raw complaints for {case_id}.",
           meta={"raw_complaints": n})
    r = logic.evaluate_case(case_id)
    m, crit = r["metrics"], r["criteria"]
    tl.add("layer1", "DeterministicMatch",
           f"VIN/lot match · dedup · post-remedy rate · cluster. "
           f"ratio={m['post_pre_rate_ratio']} cluster={m['cluster_crossed']} scope_drift={crit['scope_drift_shown']}",
           outcome=r["decision"], meta={"criteria": crit, "metrics": m})

    if r["decision"] == "chase_record":
        rec = logic.obtain_lot_record(case_id)
        tl.add("layer1", "ResolutionLoop",
               f"Chased the missing lot record via {rec.get('obtained_from', 'contact chain')}.",
               outcome=f"Lot {rec.get('lot_id')} · installed {rec.get('production_date')}", meta=rec)
        r = logic.evaluate_case(case_id)        # re-evaluate with the obtained record
        m, crit = r["metrics"], r["criteria"]

    confirmed = r["decision"] == "confirmed_trigger_mesh"

    if confirmed:
        # Real agent narration here too — explains WHICH criterion actually triggered
        # (rate-ratio spike vs geographic cluster vs both), so a low ratio sitting next
        # to a confirmed verdict is explained, not just displayed as raw numbers.
        ver = await resolution.resolve_case(r)
        tl.add("layer1", "VerificationAgent", ver["interpretation"],
               outcome=r["decision"], meta={"criteria": crit, "metrics": m, "source": ver["source"]})
        # ---- Gate → Layer 2 ----
        tl.add("gate", "RemedyWatchdog", "Scope expansion CONFIRMED — triggering the negotiation mesh.",
               outcome="trigger_mesh")
        # layer2.run_negotiation logs every offer/counter/allocation/call/ledger line to
        # `tl` itself, the instant it happens.
        try:
            l2 = await layer2.run_negotiation(case_id, tl, use_llm=use_llm, confirm=confirm)
            return {"case_id": case_id, "confirmed": True, "l1": r, "l2": l2,
                    "resolution": None, "timeline": tl.to_list()}
        except Exception as e:
            # A transient model / rate-limit failure during negotiation must NOT discard the
            # confirmed Layer-1 finding or the Part 573 amendment that rides on it. Degrade
            # gracefully: keep the verdict, drop Layer 2, and surface a clean, retryable note
            # instead of letting a raw 429 exception reach the screen.
            msg = str(e)
            rate = ("RESOURCE_EXHAUSTED" in msg) or ("429" in msg)
            note = ("Supplier negotiation was paused by a temporary model rate limit. "
                    "The confirmed scope-drift finding and the Part 573 amendment are preserved — "
                    "retry to complete supplier sourcing.") if rate else (
                    "Supplier negotiation hit a temporary error and was stopped. The confirmed "
                    "scope-drift finding and the Part 573 amendment are preserved — retry to "
                    "complete supplier sourcing.")
            tl.add("gate", "RemedyWatchdog", note, outcome="paused")
            return {"case_id": case_id, "confirmed": True, "l1": r, "l2": None,
                    "resolution": None, "l2_error": {"rate_limited": rate, "note": note},
                    "timeline": tl.to_list()}

    # ---- Non-Confirmed: a REAL agent interprets the deterministic verdict and
    # proposes the tag-appropriate resolve action. Replaces the old dead-end
    # "Not a scope expansion" line with genuine, model-written reasoning over the
    # real numbers. The decision itself stays deterministic (never changed here). ----
    tl.add("gate", "RemedyWatchdog", f"Not a scope expansion ({r['decision'].replace('_', ' ')}) "
           f"— handing to the resolution agent.", outcome="no_mesh")
    res = await resolution.resolve_case(r)
    tl.add("resolution", "ResolutionAgent", res["interpretation"], outcome=r["decision"],
           meta={"recommended_action": res["recommended_action"], "owner": res["owner"],
                 "action_label": res["action_label"], "source": res["source"],
                 "criteria": crit, "metrics": m})
    return {"case_id": case_id, "confirmed": False, "l1": r, "l2": None,
            "resolution": res, "timeline": tl.to_list()}
