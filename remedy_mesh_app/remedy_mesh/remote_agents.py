"""Remote agents — run the Remedy Mesh agents that are deployed as separate managed
services on Vertex AI Agent Engine, instead of in this process.

Cloud Run hosts the app, the orchestrator, the UI and the Twilio webhook; each agent
is its own Agent Engine resource. The orchestrator still owns every decision: verdicts
stay deterministic (logic.evaluate_case) and every remote call keeps the same
deterministic fallback the in-process path has, so a slow or failed agent never
breaks a run.

Enabled with REMEDY_AGENT_ENGINE=1. The agent-key -> resource-name map comes from
REMEDY_AGENT_ENGINES (JSON, used on Cloud Run) or, locally, from
agent_engine_lab/deployed_all.json. Nothing project-identifying is hardcoded here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import random
import threading
import time

ENABLED = os.environ.get("REMEDY_AGENT_ENGINE") == "1"
_log = logging.getLogger("remedy_mesh.remote_agents")


def _load_map() -> dict:
    raw = os.environ.get("REMEDY_AGENT_ENGINES")
    if raw:
        return json.loads(raw)
    p = pathlib.Path(__file__).resolve().parents[1] / "agent_engine_lab" / "deployed_all.json"
    return json.loads(p.read_text()) if p.exists() else {}


ENGINES: dict[str, str] = _load_map() if ENABLED else {}

# Recent remote calls (agent, latency, ok) — exposed at /api/agent_engines so the
# demo can show which managed agent served each step and how long it took.
STATS: list[dict] = []
_engines: dict = {}
_init_lock = threading.Lock()


def available(key: str) -> bool:
    return ENABLED and key in ENGINES


def key_for(agent) -> str | None:
    """Map an in-process agent object to its deployed counterpart. By identity, not
    name: two agents share the name 'ResolutionAgent'."""
    if agent is None or not ENABLED:
        return None
    from . import callunderstanding, layer1, layer2, resolution
    table = {
        id(layer1.evidence_interpreter): "evidence_interpreter",
        id(layer1.remedy_investigator): "remedy_investigator",
        id(layer1.verification_agent): "verification_agent",
        id(resolution.resolution_agent): "resolution_agent",
        id(callunderstanding.call_understanding_agent): "call_understanding_agent",
        id(layer2.supplier_offer_agent): "supplier_offer_agent",
        id(layer2.oem_counter_agent): "oem_counter_agent",
        id(layer2.resource_agent): "resource_agent",
    }
    key = table.get(id(agent))
    return key if key and key in ENGINES else None


def _engine(key: str):
    with _init_lock:
        if key not in _engines:
            import vertexai
            from vertexai import agent_engines
            vertexai.init(project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                          location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
            _engines[key] = agent_engines.get(ENGINES[key])
        return _engines[key]


def _call_sync(key: str, message: str, state: dict | None, user_id: str) -> str:
    eng = _engine(key)
    kwargs = {"user_id": user_id, "message": message}
    if state is not None:
        # The session state IS the isolation boundary (e.g. one supplier_id per run),
        # exactly as the in-process InMemorySessionService path sets it.
        kwargs["session_id"] = eng.create_session(user_id=user_id, state=state)["id"]
    text, err = "", None
    for ev in eng.stream_query(**kwargs):
        if not isinstance(ev, dict):
            continue
        if ev.get("errorMessage"):
            err = ev["errorMessage"]
        for part in ((ev.get("content") or {}).get("parts") or []):
            if isinstance(part, dict) and part.get("text"):
                text = part["text"]
    if not text and err:
        raise RuntimeError(f"Agent Engine '{key}' returned an error: {err}")
    return text


def is_rate_limit(e: BaseException) -> bool:
    """Gemini on Vertex answers 429 RESOURCE_EXHAUSTED when shared model capacity is
    momentarily short; that is worth a retry, any other error is not."""
    msg = str(e)
    return "RESOURCE_EXHAUSTED" in msg or "429" in msg


_MAX_ATTEMPTS = 4


async def run(key: str, message: str, state: dict | None = None,
              user_id: str = "remedy-mesh") -> str:
    """Run one turn on a deployed agent and return its final text. A rate-limited turn
    is retried with backoff (2s, 4s, 8s + jitter); each attempt is a fresh session with
    the same scoped state. Callers keep their own time budget around this."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        t0 = time.monotonic()
        ok = False
        try:
            out = await asyncio.to_thread(_call_sync, key, message, state, user_id)
            ok = True
            return out
        except Exception as e:
            if attempt == _MAX_ATTEMPTS or not is_rate_limit(e):
                raise
            wait = 2 ** attempt + random.random()
            _log.warning("agent-engine %s rate-limited (attempt %d/%d) — retrying in %.1fs",
                         key, attempt, _MAX_ATTEMPTS, wait)
            await asyncio.sleep(wait)
        finally:
            dt = round(time.monotonic() - t0, 2)
            STATS.append({"agent": key, "seconds": dt, "ok": ok, "at": time.time(),
                          "attempt": attempt})
            del STATS[:-200]
            _log.warning("agent-engine call %s %.2fs ok=%s attempt=%d", key, dt, ok, attempt)


def status() -> dict:
    from . import data
    return {"enabled": ENABLED, "agents": sorted(ENGINES), "data_backend": data.BACKEND,
            "recent_calls": STATS[-40:]}
