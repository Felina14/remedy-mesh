"""ResolutionAgent — Layer 1's interpretation layer.

The DECISION stays deterministic (logic.evaluate_case — auditable, never guesses).
This agent does NOT decide: it reads the already-computed evidence + the tag and
writes the human-facing interpretation and the recommended next action, in that
tag's own terms. Same split proven in Layer 2 (deterministic core in negotiation.py,
LLM narrates on top) — including a safe deterministic fallback if the model fails.

So every case that opens runs a REAL agent over REAL numbers, not a template, while
the verdict a person has to defend stays the checkable deterministic part.
"""
from __future__ import annotations

import asyncio
import json

from datetime import date, timedelta

from .config import MODEL_FLASH
from .agent_runner import run_agent_text
from . import remote_agents

# A remote (Agent Engine) turn adds a network round-trip; give it room so the real
# agent answers instead of the run quietly falling back to the deterministic text.
TURN_BUDGET_SECONDS = 35.0 if remote_agents.ENABLED else 8.0

# Deterministic re-check interval per tag, in days — a concrete date gets computed
# and guaranteed into the resolution text, never left to the model to invent.
RECHECK_DAYS = {"monitor": 30, "keep_open": 21}

# The resolve action a human takes per tag — the button label + where it routes.
# Deterministic (not model-chosen) so the action is controlled and auditable.
RESOLUTION_ACTIONS: dict[str, dict] = {
    "remedy_quality_flag":  {"label": "Escalate to engineering", "owner": "Engineering"},
    "monitor":              {"label": "Set watch & resolve",     "owner": "Monitoring"},
    "insufficient_evidence":{"label": "Request more records",     "owner": "Records"},
    "keep_open":            {"label": "Request records",          "owner": "Records"},
    "chase_record":         {"label": "Request the lot record",   "owner": "Records"},
    "no_signal":            {"label": "Dismiss — cleared",        "owner": None},
}
DEFAULT_ACTION = {"label": "Mark resolved", "owner": None}

try:
    from google.adk.agents import LlmAgent
    from google.genai import types

    resolution_agent = LlmAgent(
        name="ResolutionAgent",
        model=MODEL_FLASH,
        instruction=(
            "You are the OEM's recall resolution analyst. You are given a case's VERDICT "
            "(already decided deterministically from the records — you must NEVER dispute or "
            "change it), plus the evidence METRICS and CRITERIA behind it. Grounded ONLY in "
            "these real numbers, write:\n"
            "- interpretation: 1-2 sentences on what this pattern means and WHY, citing the "
            "actual figures (the post/pre rate ratio, post-remedy failure count, whether a "
            "geographic cluster crossed, whether the drift is inside or outside the original "
            "campaign scope). Be concrete, not generic. IMPORTANT: the policy treats a rate-ratio "
            "spike and a geographic cluster as two INDEPENDENT ways to cross the evidence bar — a "
            "case can be confirmed on clustering alone even while the rate ratio is still below "
            "the policy multiplier. When that is what happened, SAY SO EXPLICITLY (e.g. 'driven by "
            "geographic clustering, not the rate ratio, which at 1.07x is still under the 2.5x "
            "bar') so a reader is never confused by a low ratio sitting next to a confirmed verdict.\n"
            "- recommended_action: ONE sentence on what should happen next, in this verdict's "
            "terms (e.g. a remedy-quality flag goes to engineering; a confirmed case triggers the "
            "negotiation mesh to source replacement parts; insufficient evidence needs more "
            "records). If a RECHECK_DATE is given in the input, state that exact date as when to "
            "re-check — never invent a different date.\n"
            "Return ONLY compact JSON with exactly the keys interpretation and recommended_action."
        ),
        generate_content_config=types.GenerateContentConfig(
            temperature=0.3, max_output_tokens=400,
            thinking_config=types.ThinkingConfig(thinking_budget=0)),
        output_key="resolution",
    )
except ImportError:
    resolution_agent = None


def _trigger_reason(m: dict) -> str:
    """Deterministic explanation of WHICH criterion satisfied threshold_crossed —
    the rate ratio, the geographic cluster, or both — so a low ratio next to a
    confirmed verdict (cluster-only trigger) is never left unexplained."""
    rate_ok, clus_ok = m.get("rate_crossed"), m.get("cluster_crossed")
    ratio, bar = m.get("post_pre_rate_ratio"), m.get("rate_threshold")
    if rate_ok and clus_ok:
        return f"both the post/pre rate ratio ({ratio}x, over the {bar}x bar) and a geographic complaint cluster"
    if clus_ok:
        return f"a geographic complaint cluster — not the rate ratio, which at {ratio}x is still under the {bar}x policy bar"
    return f"the post/pre rate ratio ({ratio}x, over the {bar}x bar)"


def _fallback(decision: str, m: dict) -> dict:
    ratio = m.get("post_pre_rate_ratio")
    nfail = m.get("post_remedy_failures")
    n = m.get("raw_complaints")
    interp = {
        "confirmed_trigger_mesh":
            f"Scope expansion confirmed, triggered by {_trigger_reason(m)}, with {nfail} post-remedy "
            f"failures recorded outside the original campaign scope.",
        "remedy_quality_flag":
            f"Post-remedy failures ({nfail}) are elevated at {ratio}x baseline, but on VINs "
            f"inside the original campaign scope — complaints are clustering on already-remediated "
            f"vehicles. This reads as the remedy itself under-performing (a fix-the-fix), not a new "
            f"scope expansion.",
        "monitor":
            f"Scope drift is showing in the records, but the failure rate ({ratio}x) is still under "
            f"the policy threshold. Not yet actionable — worth a scheduled re-check.",
        "insufficient_evidence":
            f"Only {n} complaints on record — below the bar to open a case. Not enough signal to act on yet.",
        "keep_open":
            "The evidence is mixed — some criteria are met but the pattern is inconclusive. Keep the "
            "case open pending more records.",
        "chase_record":
            "Evidence is strong, but the install/lot confirmation record is still missing and must be "
            "obtained before this can be confirmed.",
        "no_signal":
            "No complaints on record after de-duplication — nothing to act on.",
    }
    rec = {
        "confirmed_trigger_mesh": "Trigger the negotiation mesh to source replacement parts.",
        "remedy_quality_flag": "Escalate to engineering to review the remedy design, with the affected lot IDs.",
        "monitor": "Set a monitoring threshold and re-check in a few weeks.",
        "insufficient_evidence": "Request additional service and complaint records, then re-evaluate.",
        "keep_open": "Request the missing records and re-evaluate.",
        "chase_record": "Obtain the lot confirmation record from the servicing dealer.",
        "no_signal": "Close the case — no signal.",
    }
    return {"interpretation": interp.get(decision, "Verdict computed from the case records."),
            "recommended_action": rec.get(decision, "Review and resolve.")}


def _parse(text: str) -> dict | None:
    try:
        t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        o = json.loads(t)
        i, r = o.get("interpretation"), o.get("recommended_action")
        if not i or not r:
            return None
        return {"interpretation": str(i), "recommended_action": str(r)}
    except Exception:
        return None


async def resolve_case(evaluation: dict) -> dict:
    """Interpret an already-decided case. Returns the agent's interpretation +
    recommended action + the tag's resolve-button label/owner. Never changes the
    decision. Falls back to a deterministic reading if the model is slow or fails."""
    decision = evaluation["decision"]
    m = evaluation["metrics"]
    act = RESOLUTION_ACTIONS.get(decision, DEFAULT_ACTION)

    recheck_date = (date.today() + timedelta(days=RECHECK_DAYS[decision])).isoformat() \
        if decision in RECHECK_DAYS else None

    parsed, source = None, "fallback"
    if resolution_agent is not None:
        prompt = (
            f"VERDICT (fixed, do not change): {decision}\n"
            f"METRICS: {json.dumps(m)}\n"
            f"CRITERIA: {json.dumps(evaluation['criteria'])}\n"
            + (f"RECHECK_DATE: {recheck_date}\n" if recheck_date else "")
            + "Interpret as instructed and return the JSON."
        )
        try:
            text = await asyncio.wait_for(run_agent_text(resolution_agent, prompt),
                                          timeout=TURN_BUDGET_SECONDS)
            parsed = _parse(text)
            if parsed:
                source = "agent"
        except (asyncio.TimeoutError, Exception):
            parsed = None
    if not parsed:
        parsed = _fallback(decision, m)

    # Guarantee the date appears verbatim regardless of model compliance — a date
    # is a deterministic fact, never left to the model to invent or omit.
    if recheck_date and recheck_date not in parsed["recommended_action"]:
        parsed["recommended_action"] = parsed["recommended_action"].rstrip(".") + f". Re-check by {recheck_date}."

    return {
        "decision": decision,
        "interpretation": parsed["interpretation"],
        "recommended_action": parsed["recommended_action"],
        "action_label": act["label"],
        "owner": act["owner"],
        "source": source,
        "recheck_date": recheck_date,
    }
