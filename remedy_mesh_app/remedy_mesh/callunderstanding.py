"""Tier 2 — conversational call understanding (runs on Vertex Gemini).

The supplier speaks a full, natural sentence on the phone ("I can do 20k now,
the rest in three weeks"). This agent interprets the nuance, decides the agreed
terms, and produces what the agent should SAY back — enabling a multi-turn,
context-aware phone conversation without real-time streaming (Tier 3).
"""
from __future__ import annotations

import asyncio
import json

from .config import MODEL_FLASH
from .agent_runner import run_agent_text

# A phone turn must answer inside Twilio's ~15s webhook timeout or the caller hears
# "an application error has occurred." The default path (Gemini Pro, or Flash with
# thinking ON, via a fresh ADK Runner each call) took 6–9s and sometimes tipped over
# once tunnel + Twilio latency was added. So the in-call agent uses FLASH with THINKING
# DISABLED (≈3s, predictable) and we still cap it at TURN_BUDGET seconds, falling back
# to a safe keyword read if a turn is ever slow — Twilio always gets a timely reply.
TURN_BUDGET_SECONDS = 8.0

try:
    from google.adk.agents import LlmAgent
    from google.genai import types

    call_understanding_agent = LlmAgent(
        name="CallUnderstandingAgent",
        model=MODEL_FLASH,
        instruction=(
            "You are the OEM's negotiator on a LIVE phone call with a parts supplier. "
            "You are given the ORDER (quantity, unit price, deadline), the conversation so far, "
            "and the supplier's latest spoken reply in natural language — which may be partial, "
            "conditional, or a counter, not a one-word yes/no. Interpret it like a human would.\n"
            "Decide:\n"
            "- outcome: EXACTLY ONE of 'confirmed' (full order), 'partial' (can do some now), "
            "'conditional' (yes, with conditions/timing), 'counter' (wants different price/terms), "
            "'declined'. Never any other word.\n"
            "- agreed_quantity: integer units they commit now (0 if declined; never null).\n"
            "- agreed_price: number unit price they accept (the order price if unchanged; never null).\n"
            "- reply_text: what YOU say back on the call — brief, natural, acknowledging their "
            "answer, and EITHER closing the call OR asking exactly one clarifying follow-up.\n"
            "- done: true if the call can end now, false if you need one more turn.\n"
            "Return ONLY compact JSON with those five keys and no other text."
        ),
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2, max_output_tokens=400,
            # Disable Gemini 2.5 Flash "thinking": a phone turn needs speed, not deliberation,
            # and thinking tokens were the main source of the slow, variable latency.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
        output_key="interp",
    )
except ImportError:
    call_understanding_agent = None


def _num(v, cast, default):
    """Coerce a possibly-null / string / float JSON value; never raise."""
    try:
        if v is None or v == "":
            return default
        return cast(float(v)) if cast is int else cast(v)
    except (TypeError, ValueError):
        return default


def _parse(text: str) -> dict | None:
    try:
        t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        o = json.loads(t)
        return {
            "outcome": o.get("outcome") or "confirmed",
            "agreed_quantity": _num(o.get("agreed_quantity"), int, 0),
            "agreed_price": _num(o.get("agreed_price"), float, 0.0),
            "reply_text": o.get("reply_text") or "Thank you.",
            "done": bool(o.get("done", True)),
        }
    except Exception:
        return None


async def interpret_call_turn(order: dict, spoken_reply: str, history: list[dict]) -> dict:
    """Interpret the supplier's spoken reply in context; returns structured terms +
    what to say next. Falls back to a safe confirm/decline read if the agent is
    unavailable or misbehaves."""
    convo = "\n".join(f"{h['actor']}: {h['text']}" for h in history)
    prompt = (
        f"ORDER: {order['quantity']} units @ ${order['unit_price']} within {order['deadline_weeks']} weeks.\n"
        f"Conversation so far:\n{convo or '(start of call)'}\n"
        f"Supplier just said: \"{spoken_reply}\"\n"
        "Interpret and respond as instructed."
    )
    if call_understanding_agent is not None:
        try:
            text = await asyncio.wait_for(
                run_agent_text(call_understanding_agent, prompt), timeout=TURN_BUDGET_SECONDS)
            parsed = _parse(text)
            if parsed:
                return parsed
        except (asyncio.TimeoutError, Exception):
            pass  # too slow or errored — fall back so Twilio always gets a timely reply
    # fallback: crude keyword read
    low = spoken_reply.lower()
    if any(w in low for w in ("no", "can't", "cannot", "decline", "unable")):
        return {"outcome": "declined", "agreed_quantity": 0, "agreed_price": order["unit_price"],
                "reply_text": "Understood, thank you for your time. Goodbye.", "done": True}
    return {"outcome": "confirmed", "agreed_quantity": order["quantity"], "agreed_price": order["unit_price"],
            "reply_text": "Thank you, the order is confirmed. Goodbye.", "done": True}
