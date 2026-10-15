"""Prove the REAL deployed agents work, scoped exactly like the live orchestrator:
the supplier agent is ONE engine, run per-supplier via session state {supplier_id, demand};
the OEM agent is run with state {counter_ctx}. The orchestrator relays only public facts.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMAND = {"quantity": 27000, "deadline_weeks": 12}
CEILING = 128.0
ALT = 2


def _json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m.group(0)) if m else {}


def _final_text(remote, user_id, session_id, message):
    out = ""
    for ev in remote.stream_query(user_id=user_id, session_id=session_id, message=message):
        parts = (ev.get("content") or {}).get("parts") if isinstance(ev, dict) else None
        if parts:
            for p in parts:
                if isinstance(p, dict) and p.get("text"):
                    out = p["text"]
    return out


def main():
    import vertexai
    from vertexai import agent_engines
    vertexai.init(project=os.environ["GOOGLE_CLOUD_PROJECT"], location="us-central1")
    d = json.loads((HERE / "deployed_all.json").read_text())
    supplier = agent_engines.get(d["supplier_offer_agent"])
    oem = agent_engines.get(d["oem_counter_agent"])

    sid = "SUP_B"
    print(f"Scoped run: supplier={sid}, PUBLIC demand={DEMAND}, alternatives={ALT}\n")

    counter_price = None
    for rnd in range(1, 4):
        print(f"--- round {rnd} ---")
        # Supplier: session scoped to THIS supplier only (its cost lives in its own tool)
        s = supplier.create_session(user_id="orchestrator",
                                    state={"supplier_id": sid, "demand": DEMAND})
        msg = ("Make your offer." if counter_price is None
               else f"The OEM has countered: it can pay at most ${counter_price:.2f}/unit. Revise your offer.")
        offer = _json(_final_text(supplier, "orchestrator", s["id"], msg))
        print(f"  SUPPLIER({sid}) -> {offer}")
        if offer.get("stance") == "accept":
            print(f"\nSETTLED: ${offer.get('unit_price')} x {offer.get('quantity')} units.")
            return

        # OEM: scoped to this supplier's offer + a COUNT of alternatives (never a rival bid)
        ctx = {"supplier_id": offer.get("supplier_id"), "their_quantity": offer.get("quantity"),
               "their_price": offer.get("unit_price"), "oem_ceiling": CEILING,
               "demand_units": DEMAND["quantity"], "deadline_weeks": DEMAND["deadline_weeks"],
               "num_alternative_suppliers": ALT}
        o = oem.create_session(user_id="oem", state={"counter_ctx": ctx})
        counter = _json(_final_text(oem, "oem", o["id"], "Decide your counter."))
        print(f"  OEM            -> {counter}")
        counter_price = counter.get("counter_price")
        if counter_price is None:
            print("  (no counter) stopping"); return
    print("\nRound cap reached.")


if __name__ == "__main__":
    main()
