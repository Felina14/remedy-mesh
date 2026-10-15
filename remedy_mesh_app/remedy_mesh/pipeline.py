"""Step 4 — the gate: run Layer 1, and on a confirmed signal run Layer 2, emitting
a unified timeline the UI renders. This is the end-to-end demo spine.
"""
import asyncio
import json

from . import data, logic, layer2, remote_agents, resolution
from .timeline import Timeline

_L1_KEYS = ("evidence_interpreter", "remedy_investigator", "verification_agent")
_L1_TURN_SECONDS = 60.0


def _parse_json(text: str) -> dict | None:
    try:
        t = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        o = json.loads(t)
        return o if isinstance(o, dict) else None
    except Exception:
        return None


def _readable(v) -> str:
    """Render an agent's free-form field as plain text: the Interpreter sometimes
    returns its summary as a nested object, which must never reach the UI as raw
    {'key': ...} text."""
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            x = _readable(x)
            if x:
                parts.append(f"{str(k).replace('_', ' ').capitalize()}: {x.rstrip('.')}.")
        return " ".join(parts)
    if isinstance(v, (list, tuple)):
        return "; ".join(_readable(x) for x in v if _readable(x))
    return "" if v is None else str(v).strip()


async def _l1_turn(tl, actor: str, key: str, case_id: str, state: dict | None = None) -> str | None:
    """One Layer-1 agent turn on its deployed Agent Engine service. Returns the agent's
    raw output, or None if it was slow / failed — the run then carries on on the
    deterministic path, it never stalls."""
    tl.add("layer1", actor, "Thinking...", outcome="thinking")
    try:
        return await asyncio.wait_for(remote_agents.run(key, case_id, state),
                                      timeout=_L1_TURN_SECONDS)
    except Exception:
        return None


async def run_pipeline(case_id: str, use_llm: bool = False, confirm: bool = True, on_event=None) -> dict:
    """`on_event`, when given, is called the instant each timeline event is added
    (see Timeline.add) — this is what server.api_run_stream uses to turn the run
    into a live Server-Sent-Events feed instead of one long silent wait."""
    tl = Timeline(case_id, on_event=on_event)

    # ---- Layer 1 ----
    # With the agents deployed on Agent Engine, the three Layer-1 agents run for real,
    # chained the way the RemedyWatchdog SequentialAgent chains them: the Interpreter's
    # claims feed the Investigator, whose investigation feeds the independent Verifier.
    # The VERDICT still comes from the deterministic evaluate_case; the agents structure,
    # investigate and audit it, and a disagreement is surfaced, never silently resolved.
    n = len(data.complaints_for(case_id))
    l1_live = use_llm and all(remote_agents.available(k) for k in _L1_KEYS)
    claims = await _l1_turn(tl, "EvidenceInterpreter", "evidence_interpreter", case_id) if l1_live else None
    c = _parse_json(claims) if claims else None
    if c:
        tl.add("layer1", "EvidenceInterpreter",
               _readable(c.get("summary")) or f"Structured {n} raw complaints for {case_id}.",
               meta={"raw_complaints": n, "agent_output": c, "served_by": "agent_engine"})
    else:
        tl.add("layer1", "EvidenceInterpreter", f"Structured {n} raw complaints for {case_id}.",
               meta={"raw_complaints": n})
    r = logic.evaluate_case(case_id)
    m, crit = r["metrics"], r["criteria"]
    tl.add("layer1", "DeterministicMatch",
           f"VIN/lot match · dedup · post-remedy rate · cluster. "
           f"ratio={m['post_pre_rate_ratio']} cluster={m['cluster_crossed']} scope_drift={crit['scope_drift_shown']}",
           outcome=r["decision"], meta={"criteria": crit, "metrics": m})

    chased = r["decision"] == "chase_record"
    if chased:
        rec = logic.obtain_lot_record(case_id)
        tl.add("layer1", "ResolutionLoop",
               f"Chased the missing lot record via {rec.get('obtained_from', 'contact chain')}.",
               outcome=f"Lot {rec.get('lot_id')} · installed {rec.get('production_date')}", meta=rec)
        r = logic.evaluate_case(case_id)        # re-evaluate with the obtained record
        m, crit = r["metrics"], r["criteria"]

    if c:
        # The orchestrator's obtained lot records travel with the request, so the
        # separately-deployed Investigator and Verifier audit exactly the records the
        # app holds (each Agent Engine service has its own memory otherwise).
        lot_records = {case_id: logic._CONFIRMED[case_id]} if case_id in logic._CONFIRMED else {}
        inv_raw = await _l1_turn(tl, "RemedyInvestigator", "remedy_investigator", case_id,
                                 {"claims": claims, "lot_records": lot_records})
        inv = _parse_json(inv_raw) if inv_raw else None
        if inv:
            # Wording follows what the pipeline actually did (chase or not), never the
            # agent's free-form field — a record already on file is not a "chase".
            how = " after chasing the missing lot record" if chased else ""
            tl.add("layer1", "RemedyInvestigator",
                   f"Ran the deterministic evaluation{how} — reports "
                   f"{str(inv.get('decision', '?')).replace('_', ' ')}.",
                   outcome=inv.get("decision"), meta={"agent_output": inv, "served_by": "agent_engine"})
            ver_raw = await _l1_turn(tl, "VerificationAgent", "verification_agent", case_id,
                                     {"investigation": inv_raw, "lot_records": lot_records})
            ver = _parse_json(ver_raw) if ver_raw else None
            if ver:
                missing = ", ".join(ver.get("missing_criteria") or []) or "none"
                agrees = ver.get("decision") == r["decision"]
                text = (f"Independent audit against policy: "
                        f"{'confirmed' if ver.get('confirmed') else 'not confirmed'} "
                        f"({str(ver.get('decision', '?')).replace('_', ' ')}). Missing criteria: {missing}.")
                if not agrees:
                    text += (" The audit disagrees with the deterministic verdict — the deterministic "
                             "verdict stands and the case is flagged for human review.")
                tl.add("layer1", "VerificationAgent", text,
                       outcome=r["decision"] if agrees else "audit_disagrees",
                       meta={"agent_output": ver, "served_by": "agent_engine",
                             "agrees_with_deterministic": agrees, "criteria": crit, "metrics": m})

    confirmed = r["decision"] == "confirmed_trigger_mesh"

    if confirmed:
        # Real agent narration here too — explains WHICH criterion actually triggered
        # (rate-ratio spike vs geographic cluster vs both), so a low ratio sitting next
        # to a confirmed verdict is explained, not just displayed as raw numbers.
        # Labelled as the agent that actually wrote it (the Resolution Agent).
        ver = await resolution.resolve_case(r)
        tl.add("layer1", "ResolutionAgent", ver["interpretation"],
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
