"""Layer 2 — Verified Negotiation Mesh ("CommitMesh").

Tier 2 isolation (see DESIGN_DECISIONS §3.3/§4.1):
  - ONE supplier-offer agent (role), run once per supplier in a SCOPED session whose
    state carries only {supplier_id, demand}. The agent reads its OWN profile via the
    session-scoped get_my_profile tool and returns a sanitized offer.
  - A deterministic orchestrator collects the sanitized offers, allocates, and records
    commitments. It never holds a supplier's raw cost/capacity.

The negotiation decision math (allocation, ledger, selective reneg) lives in
negotiation.py — deterministic, checkable. The agent only produces offers.
"""
from __future__ import annotations

import asyncio
import json
import os
import random

from .config import MODEL_FLASH, MODEL_PRO
from . import data, negotiation as neg, remote_agents, tools, voice

# We're staying on the free/trial Vertex quota on purpose -- no billing upgrade.
# That quota is small and SHARED across Flash and Pro alike, and it's a
# requests-PER-MINUTE ceiling: limiting concurrency (the old _FLASH_SEM/_PRO_SEM
# approach) caps how many calls are in flight at once, but says nothing about how
# fast new calls get DISPATCHED -- you can still fire a burst that blows the
# per-minute cap one call after another. So instead every Gemini call, Flash or
# Pro, is paced onto one shared schedule of dispatch slots at least
# REMEDY_LLM_PACE_SECONDS apart. That bounds the actual request rate to
# 60/REMEDY_LLM_PACE_SECONDS per minute, which is what the quota actually
# enforces. Tune the env var if you watch a run and find you have more (or less)
# headroom than this default assumes.
# All negotiation calls are now Flash (Pro removed from the hot path), whose
# per-minute quota is far higher, so calls can be dispatched much closer together.
PACE_SECONDS = float(os.environ.get("REMEDY_LLM_PACE_SECONDS", "1.5"))
_pace_lock = asyncio.Lock()
_next_slot = 0.0


async def _throttle():
    """Block until this call's turn in the shared dispatch schedule arrives."""
    global _next_slot
    loop = asyncio.get_event_loop()
    async with _pace_lock:
        now = loop.time()
        start = max(now, _next_slot)
        _next_slot = start + PACE_SECONDS
    wait = start - now
    if wait > 0:
        await asyncio.sleep(wait)


async def _with_retry(tl, actor: str, call, *, max_attempts: int = 5):
    for attempt in range(1, max_attempts + 1):
        await _throttle()
        if tl:
            tl.add("layer2", actor, "Thinking...", outcome="thinking")
        try:
            return await call()
        except Exception as e:
            msg = str(e)
            if "RESOURCE_EXHAUSTED" not in msg and "429" not in msg:
                raise
            if attempt == max_attempts:
                raise
            wait = min(2 ** attempt, 20) + random.random()
            if tl:
                tl.add("layer2", actor,
                       f"Gemini rate limit hit -- retrying in {wait:.1f}s (attempt {attempt}/{max_attempts}).",
                       outcome="rate_limited")
            await asyncio.sleep(wait)

# ------------------------------------------------------------------ the agent
try:
    from google.adk.agents import LlmAgent
    from google.genai import types

    # Negotiation agents run with thinking DISABLED: these are fast, templated
    # numeric decisions (offer / counter), and the heavy allocation math is
    # deterministic in negotiation.py — so thinking only adds latency and tokens.
    _FAST_CFG = types.GenerateContentConfig(
        temperature=0.3, max_output_tokens=500,
        thinking_config=types.ThinkingConfig(thinking_budget=0))

    # Flash, not Pro: this call is one scoped supplier doing templated arithmetic
    # against its own profile, run once per supplier (3+ calls/round) — exactly the
    # "high-volume" case config.py reserves Flash for. Pro is kept for the single
    # OEM counter-reasoning call per round, which actually needs the deeper model.
    supplier_offer_agent = LlmAgent(
        name="SupplierOfferAgent",
        model=MODEL_FLASH,
        tools=tools.SUPPLIER_TOOLS,
        instruction=(
            "You represent ONE automotive parts supplier in a sourcing negotiation. "
            "Call get_my_profile to see ONLY your own capacity and cost — you cannot see any "
            "other supplier. Call get_public_demand to see what the OEM needs (quantity + deadline). "
            "Decide: (1) how many units you can honestly commit — never more than "
            "capacity_units_per_week * deadline_weeks minus your existing backlog; "
            "(2) an opening unit price ABOVE your unit_cost that protects your margin. "
            "NEVER reveal your raw unit_cost or raw capacity. "
            "IF the message says the OEM has countered at a price, REVISE your offer: accept that "
            "price if it is at or above your unit_cost (you still make margin), otherwise counter with "
            "the lowest price you can accept — but NEVER below your unit_cost. Set stance to "
            "'accept' if you took the OEM's price, else 'counter'. "
            "Return ONLY compact JSON: "
            '{\"supplier_id\": str, \"quantity\": int, \"unit_price\": float, \"stance\": \"propose|counter|accept\"}.'
        ),
        generate_content_config=_FAST_CFG,
        output_key="offer",
    )

    oem_counter_agent = LlmAgent(
        name="OEMCounterAgent",
        model=MODEL_FLASH,
        generate_content_config=_FAST_CFG,
        tools=tools.OEM_TOOLS,
        instruction=(
            "You are the OEM's procurement negotiator. Call get_counter_context to see: the "
            "supplier's offered price and quantity, your PRIVATE ceiling, the public demand and "
            "deadline, and how many ALTERNATIVE suppliers you have (a COUNT only — you never see "
            "another supplier's offer). Decide the counter price to send THIS supplier. "
            "Reason about leverage: counter HARDER (at or below your ceiling) when the offer is well "
            "above your ceiling and you have several alternatives; counter SOFTER (at your ceiling) "
            "when you have few/no alternatives or the deadline is tight. Your counter is the price you "
            "OFFER, and your ceiling is the most you will pay: NEVER counter above your ceiling — if "
            "the supplier cannot meet it, its revised offer will say so and the allocation handles "
            "it. Never counter above their offered price either. "
            "Return ONLY compact JSON: {\"supplier_id\": str, \"counter_price\": float, \"rationale\": str}."
        ),
        output_key="counter",
    )

    # When a confirmed supplier DECLINES on the call, the gap is re-sourced
    # deterministically (break_commitment_and_resolve). This agent does NOT pick the
    # reserve — it reads the already-decided backfill and writes the human-facing
    # reasoning, in the first person, the way the Layer-1 ResolutionAgent narrates a
    # verdict. Same split: deterministic core, LLM narrates, safe fallback below.
    resource_agent = LlmAgent(
        name="ResolutionAgent",
        model=MODEL_FLASH,
        generate_content_config=_FAST_CFG,
        instruction=(
            "You are the OEM's sourcing resolution agent. A supplier that had committed just "
            "DECLINED the order on a confirmation call, and the system has ALREADY re-sourced the "
            "gap to a reserve supplier (chosen deterministically — you must NOT change or dispute "
            "the choice). You are given JSON facts. Write 1-2 short sentences, first person, telling "
            "the human operator WHY this reserve is the right backup: name the declined supplier and "
            "the reserve, cite the exact units it covers and the delivery deadline it holds, and if "
            "its price is above the ceiling, frame the premium as the cost of holding the window. End "
            "by recommending they confirm the reserve by phone. Ground every figure in the facts "
            "given — invent nothing. No preamble, no bullet points, no JSON — just the sentences."
        ),
    )
except ImportError:
    supplier_offer_agent = None
    oem_counter_agent = None
    resource_agent = None


def _fallback_resource_text(ctx: dict) -> str:
    """Deterministic re-sourcing narration — always correct, used if the LLM is
    unavailable or rate-limited. Grounded entirely in the computed backfill."""
    broken = ctx.get("broken_name", "The supplier")
    reserve = ctx.get("reserve_name", "the reserve supplier")
    covered = int(ctx.get("covered", 0))
    price = float(ctx.get("price", 0) or 0)
    ceiling = float(ctx.get("ceiling", 0) or 0)
    wk = int(ctx.get("deadline_weeks", 0) or 0)
    if price > ceiling > 0:
        terms = (f" at ${price:,.2f}/unit — a modest premium over the ${ceiling:,.0f} ceiling, "
                 f"but it holds the {wk}-week delivery window")
    else:
        terms = f" at ${price:,.2f}/unit, within the ${ceiling:,.0f} ceiling and the {wk}-week window"
    return (f"{broken} declined on the call, so I re-ran sourcing against the reserve pool. "
            f"{reserve} is the one uncommitted supplier with the capacity to absorb the full "
            f"{covered:,} units{terms}. Recommend confirming {reserve} by phone.")


async def narrate_resource(ctx: dict, use_llm: bool = True) -> str:
    """Agent narration for a re-source, with a deterministic fallback. Reliable for
    a live demo: a model timeout or rate-limit falls straight through to the
    computed text, so the reasoning always shows."""
    if use_llm and resource_agent is not None:
        try:
            from .agent_runner import run_agent_text
            txt = await asyncio.wait_for(
                run_agent_text(resource_agent, f"Facts: {json.dumps(ctx)}"),
                timeout=35.0 if remote_agents.ENABLED else 8.0)
            txt = (txt or "").strip()
            if len(txt) > 20:
                return txt
        except Exception:
            pass
    return _fallback_resource_text(ctx)


# ------------------------------------------------------------- offer parsing
def _parse_offer(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        o = json.loads(t)
        return {
            "supplier_id": o["supplier_id"],
            "quantity": int(o["quantity"]),
            "unit_price": float(o["unit_price"]),
            "stance": o.get("stance", "propose"),
        }
    except Exception:
        return None


# --------------------------------------------------- per-supplier scoped call
async def solicit_offer_llm(supplier_id: str, demand: dict, counter_price: float | None = None) -> dict:
    """Run the supplier agent in an ISOLATED session scoped to this supplier only.

    If counter_price is given, this is a counter-round: the agent is told the OEM's
    counter and revises toward (never below) its own cost.
    """
    from .agent_runner import run_agent_text

    text = ("Make your offer." if counter_price is None
            else f"The OEM has countered: it can pay at most ${counter_price:.2f}/unit. Revise your offer.")
    # The scoped state is the entire isolation boundary: only THIS supplier + public
    # demand. Same state whether the turn runs in-process or on the deployed agent.
    final = await run_agent_text(supplier_offer_agent, text,
                                 state={"supplier_id": supplier_id, "demand": demand})
    offer = _parse_offer(final)
    if not offer:                                   # agent misbehaved → safe deterministic stand-in
        offer = solicit_offer_fallback(supplier_id, demand, counter_price)
    return offer


async def oem_decide_counter(offer: dict, demand: dict, ceiling: float, num_alternatives: int) -> tuple[float, str]:
    """OEM agent reasons a counter price for ONE supplier — scoped to that supplier's
    offer only (never another supplier's bid). Falls back to the flat ceiling if the
    agent is unavailable or misbehaves. Returns (counter_price, rationale) — the
    rationale is the agent's own stated reasoning, straight from its JSON response,
    so the UI can show WHY it countered hard or soft instead of just the number."""
    if oem_counter_agent is None:
        return ceiling, ""
    from .agent_runner import run_agent_text

    ctx = {
        "supplier_id": offer["supplier_id"],
        "their_quantity": offer["quantity"],
        "their_price": offer["unit_price"],
        "oem_ceiling": ceiling,
        "demand_units": demand["quantity"],
        "deadline_weeks": demand["deadline_weeks"],
        "num_alternative_suppliers": num_alternatives,
    }
    final = await run_agent_text(oem_counter_agent, "Decide your counter.",
                                 state={"counter_ctx": ctx})
    try:
        t = final.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(t)
        cp = round(min(float(parsed["counter_price"]), offer["unit_price"]), 2)   # never counter above their price
        return cp, parsed.get("rationale", "")
    except Exception:
        return ceiling, ""


def solicit_offer_fallback(supplier_id: str, demand: dict, counter_price: float | None = None) -> dict:
    """Offline stand-in for the scoped agent (no LLM credential needed)."""
    profile = tools._supplier(supplier_id)
    offer = neg.fallback_offer(profile, demand["quantity"], demand["deadline_weeks"])
    if counter_price is not None:                   # revise toward the counter, never below cost
        if profile["unit_cost"] <= counter_price:
            offer["unit_price"], offer["stance"] = round(counter_price, 2), "accept"
        else:
            offer["unit_price"], offer["stance"] = round(profile["unit_cost"], 2), "counter"
    return offer


# ----------------------------------------------------- the orchestrator (cost-blind)
async def run_negotiation(case_id: str, tl, use_llm: bool = True, confirm: bool = True) -> dict:
    """Allocate the confirmed replacement-part demand across suppliers and record commitments.

    Deterministic + cost-blind: it only ever holds sanitized offers (quantity, price).

    `tl` is the run's Timeline — every step logs to it THE INSTANT it happens, which
    is what lets the UI stream a live feed. Suppliers are isolated from each other
    (Tier-2, see module docstring), so round 1 is a genuine sealed-bid auction: every
    supplier is asked AT ONCE (asyncio.gather), not queued one after another. Each
    offer logs the moment ITS OWN call resolves, so the feed fills in real completion
    order instead of making a judge watch one spinner for the sum of every call.
    """
    oem = next(s for s in data.suppliers() if s["role"] == "oem")
    supplier_ids = [s["party_id"] for s in data.suppliers() if s["role"] == "supplier"]
    demand = {"quantity": oem["target_units"], "deadline_weeks": oem["deadline_weeks"]}
    ceiling = oem["max_unit_cost"]

    live = use_llm and supplier_offer_agent is not None

    async def ask(sid, counter=None):
        return await solicit_offer_llm(sid, demand, counter) if live \
            else solicit_offer_fallback(sid, demand, counter)

    # Round 1 — every supplier proposes CONCURRENTLY (isolated, scoped call per
    # supplier) — a real sealed-bid auction, not a queue.
    async def round1_and_log(sid):
        o = await _with_retry(tl, sid, lambda: ask(sid))
        tl.add("layer2", o["supplier_id"], f"Opening offer: {o['quantity']} units @ ${o['unit_price']}.", meta=o)
        return o

    round1 = await asyncio.gather(*(round1_and_log(sid) for sid in supplier_ids))

    # Round 2 — the OEM AGENT reasons a counter for each over-ceiling offer (scoped to
    # that supplier only); that supplier then revises toward (never below) its own
    # cost. Over-ceiling suppliers are also negotiated with concurrently — one
    # supplier's counter-round never waits on another's.
    num_alt = len(supplier_ids) - 1

    async def counter_and_log(o):
        if live:
            counter, rationale = await _with_retry(
                tl, "OEMCounterAgent", lambda: oem_decide_counter(o, demand, ceiling, num_alt))
        else:
            counter, rationale = ceiling, ""
        tl.add("layer2", "OEMCounterAgent", f"Countered {o['supplier_id']} at ${counter} (reasoned, scoped).",
               meta={"rationale": rationale, "ceiling": ceiling, "num_alternatives": num_alt, "their_offer": o})
        revised = await _with_retry(tl, o["supplier_id"], lambda: ask(o["supplier_id"], counter=counter))
        tl.add("layer2", revised["supplier_id"],
               f"Revised to {revised['quantity']} @ ${revised['unit_price']} ({revised['stance']}).", meta=revised)
        return o["supplier_id"], counter, revised

    over = [o for o in round1 if o["unit_price"] > ceiling]
    counter_results = await asyncio.gather(*(counter_and_log(o) for o in over)) if over else []
    counters = {sid: c for sid, c, _ in counter_results}
    revised_by_sid = {sid: r for sid, _, r in counter_results}
    final_offers = [revised_by_sid.get(o["supplier_id"], o) for o in round1]

    result = neg.allocate(final_offers, demand["quantity"], ceiling)
    for a in result["allocation"]:
        tl.add("layer2", "OEM", f"Allocated {a['quantity']} units to {a['supplier_id']} @ ${a['unit_price']}"
                                 f"{' (over ceiling)' if a['over_ceiling'] else ''}.", meta=a)

    ledger = neg.CommitmentLedger(case_id)
    commitments = ledger.record_allocation(result["allocation"])
    for c in commitments:
        tl.add("ledger", "CommitmentLedger",
               f"{c.commitment_id} · {c.supplier_id} · {c.quantity} @ ${c.unit_price}", outcome=c.status,
               meta={"commitment_id": c.commitment_id, "supplier_id": c.supplier_id, "quantity": c.quantity,
                     "unit_price": c.unit_price, "status": c.status})

    # Confirmation call per allocated supplier (stub-safe Twilio), placed concurrently
    # too — these are independent suppliers, no reason to dial them one at a time.
    # voice.call_supplier is a blocking call (real Twilio mode does a network round
    # trip) so it runs off the event loop via asyncio.to_thread.
    calls = []
    if confirm and commitments:
        async def call_and_log(c):
            outcome = await asyncio.to_thread(
                voice.call_supplier, c.supplier_id, c.quantity, c.unit_price, demand["deadline_weeks"])
            tl.add("call", "Agent → " + c.supplier_id, outcome["spoken"], meta={"mode": outcome["mode"]})
            tl.add("call", c.supplier_id + " (supplier)",
                   f"[{outcome['mode']}] {outcome['outcome']}", outcome=outcome["outcome"])
            return outcome

        calls = await asyncio.gather(*(call_and_log(c) for c in commitments))

    return {
        "case_id": case_id,
        "demand": demand,
        "offers_round1": round1,                    # opening proposals
        "oem_counters": counters,                    # OEM agent's reasoned counter per over-ceiling supplier
        "offers": final_offers,                      # after OEM counter-round
        "allocation": result,
        "calls": calls,                             # supplier-confirmation call outcomes
        "commitments": ledger.to_list(),
        "ledger": ledger,                           # kept for selective-reneg in the live demo
    }


async def break_commitment_and_resolve(ledger: neg.CommitmentLedger, commitment_id: str,
                                        demand: dict, ceiling: float, supplier_ids: list[str],
                                        use_llm: bool = True) -> dict:
    """Selective re-negotiation: break ONE commitment, re-solicit from the OTHER
    suppliers, and repair only that row. Every other commitment stays untouched."""
    live = use_llm and supplier_offer_agent is not None

    async def ask(sid):
        return await solicit_offer_llm(sid, demand) if live else solicit_offer_fallback(sid, demand)

    broken = ledger.rows[commitment_id]
    broken_sid, need = broken.supplier_id, broken.quantity
    ledger.break_commitment(commitment_id)

    # Re-source to a RESERVE supplier — one that isn't already committed. The committed
    # suppliers are already supplying at their negotiated volume; pulling on their thin
    # leftover headroom would only fragment the backfill across rows and leave a phantom
    # shortfall. Selective re-negotiation (the product's promise) backfills the broken
    # commitment onto a single reserve, so the gap is covered cleanly or escalated honestly.
    committed_sids = {r.supplier_id for r in ledger.rows.values() if r.status == "Committed"}
    others = [s for s in supplier_ids if s != broken_sid and s not in committed_sids]
    if not others:                                   # no untapped reserve — fall back to any other supplier's headroom
        others = [s for s in supplier_ids if s != broken_sid]
    reoffers = list(await asyncio.gather(*(ask(s) for s in others))) if others else []
    opening = {o["supplier_id"]: o["unit_price"] for o in reoffers}

    # One counter round, exactly like the main negotiation: an over-ceiling reserve
    # quote is countered by the OEM agent and the supplier revises (never below its own
    # cost). Without this the backfill would pay a reserve's opening price even though
    # the same supplier accepted far less moments earlier in the main round.
    async def counter_round(o):
        if o["unit_price"] <= ceiling:
            return o
        cp, _ = (await oem_decide_counter(o, demand, ceiling, max(0, len(others) - 1))
                 if live else (ceiling, ""))
        revised = (await solicit_offer_llm(o["supplier_id"], demand, cp) if live
                   else solicit_offer_fallback(o["supplier_id"], demand, cp))
        return {**o, "unit_price": min(o["unit_price"], revised["unit_price"]),
                "stance": revised.get("stance", "counter")}
    reoffers = list(await asyncio.gather(*(counter_round(o) for o in reoffers)))

    already = {r.supplier_id: r.quantity for r in ledger.rows.values() if r.status == "Committed"}
    for o in reoffers:                               # only offer headroom beyond what they already committed
        o["quantity"] = max(0, o["quantity"] - already.get(o["supplier_id"], 0))

    backfill = neg.allocate(reoffers, need, ceiling)
    best = max(backfill["allocation"], key=lambda a: a["quantity"], default=None)
    covered = best["quantity"] if best else 0
    if best:
        ledger.repair(commitment_id, best["supplier_id"], best["unit_price"], quantity=covered)

    return {
        "broken_supplier": broken_sid, "need": need, "covered": covered,
        "shortfall": need - covered, "escalated": need - covered > 0,
        "reserve_supplier": best["supplier_id"] if best else None,
        "reserve_price": best["unit_price"] if best else None,
        "reserve_opening_price": opening.get(best["supplier_id"]) if best else None,
        "commitments": ledger.to_list(),
    }
