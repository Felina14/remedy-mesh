"""Unified demo timeline — one ordered feed the UI renders.

Merges everything the judge should see in order: Layer-1 agent actions, the
Layer-2 negotiation, the supplier CALL (agent's spoken line + the supplier's
transcribed reply), and commitment-ledger updates. The call-transcript lines are
filled by the simulated outcome offline, or by the real Twilio webhook callback
when deployed (server.append_transcript).
"""
from datetime import datetime, timezone


class Timeline:
    def __init__(self, case_id: str, on_event=None):
        self.case_id = case_id
        self.events: list[dict] = []
        # optional sync callback(ev) fired the INSTANT each event is added — this is
        # what lets the API stream the run live instead of buffering it to one
        # response at the end (see server.api_run_stream).
        self.on_event = on_event

    def add(self, phase: str, actor: str, text: str, outcome: str = "", meta: dict | None = None) -> dict:
        ev = {
            "seq": len(self.events) + 1,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "phase": phase,          # layer1 | gate | layer2 | call | ledger
            "actor": actor,          # agent / role name
            "text": text,
            "outcome": outcome,
            "meta": meta or {},
        }
        self.events.append(ev)
        if self.on_event:
            self.on_event(ev)
        return ev

    def to_list(self) -> list[dict]:
        return self.events
