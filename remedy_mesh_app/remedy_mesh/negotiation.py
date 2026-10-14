"""Layer 2 deterministic backbone — allocation + commitment ledger + selective re-negotiation.

This is the 'checkable, not plausible' core of CommitMesh. The Gemini supplier
agents produce *offers* (quantity + price) from their own private data; THIS code
turns offers into an allocation and records verifiable commitments. No LLM here.

Tier 2 isolation: this module only ever sees **sanitized offers** (quantity,
unit_price) — never a supplier's raw cost/capacity. `fallback_offer()` is the only
function that touches a raw profile, and it stands in for the scoped supplier agent
when no LLM is available (tests / offline); the orchestrator never calls it with
more than one supplier's data at a time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


# --------------------------------------------------------------------- offers
def fallback_offer(profile: dict, demand_units: int, deadline_weeks: int) -> dict:
    """Stand-in for the scoped supplier agent (used offline / in tests).

    Computes what this ONE supplier can honestly commit and an opening price from
    its own cost. Returns a SANITIZED offer — no raw cost/capacity leaks out.
    """
    available = profile["capacity_units_per_week"] * deadline_weeks - profile.get("existing_backlog_units", 0)
    qty = max(0, min(available, demand_units))
    opening_price = round(profile["unit_cost"] + 2.5, 2)   # thin opening margin over its own cost
    return {
        "supplier_id": profile["party_id"],
        "quantity": qty,
        "unit_price": opening_price,
        "stance": "propose",
    }


# ----------------------------------------------------------------- allocation
def allocate(offers: list[dict], demand_units: int, oem_ceiling: float) -> dict:
    """Meet demand as cheaply as possible from sanitized offers.

    Prefers offers at/under the OEM ceiling, cheapest first; only dips into
    over-ceiling offers if demand can't otherwise be met (flagged).
    """
    ranked = sorted(offers, key=lambda o: o["unit_price"])
    under = [o for o in ranked if o["unit_price"] <= oem_ceiling]
    over = [o for o in ranked if o["unit_price"] > oem_ceiling]

    allocation, remaining, over_ceiling_used = [], demand_units, False
    for pool, is_over in ((under, False), (over, True)):
        for o in pool:
            if remaining <= 0:
                break
            take = min(o["quantity"], remaining)
            if take <= 0:
                continue
            allocation.append({
                "supplier_id": o["supplier_id"],
                "quantity": take,
                "unit_price": o["unit_price"],
                "over_ceiling": is_over,
            })
            remaining -= take
            over_ceiling_used = over_ceiling_used or is_over

    allocated = demand_units - remaining
    total_cost = sum(a["quantity"] * a["unit_price"] for a in allocation)
    return {
        "allocation": allocation,
        "allocated_units": allocated,
        "unmet_units": max(0, remaining),
        "fully_met": remaining <= 0,
        "total_cost": round(total_cost, 2),
        "over_ceiling_used": over_ceiling_used,
    }


# ----------------------------------------------------- commitment ledger (append-only)
@dataclass
class Commitment:
    commitment_id: str
    case_id: str
    supplier_id: str
    quantity: int
    unit_price: float
    status: str = "Committed"                     # Committed | Broken | Repairing | Re-committed
    shortfall: int = 0                            # units still uncovered after a repair
    status_history: list = field(default_factory=list)

    def _stamp(self, status):
        self.status = status
        self.status_history.append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": status,
        })


class CommitmentLedger:
    """In-memory append-only ledger (swaps to Firestore later, same interface).

    Append-only: every status change is appended to the row's status_history; a
    break touches ONLY the affected row — the proof of 'selective'.
    """

    def __init__(self, case_id: str):
        self.case_id = case_id
        self.rows: dict[str, Commitment] = {}
        self._seq = 0

    def record(self, supplier_id: str, quantity: int, unit_price: float) -> Commitment:
        self._seq += 1
        c = Commitment(f"CMT-{self._seq:03d}", self.case_id, supplier_id, quantity, unit_price)
        c._stamp("Committed")
        self.rows[c.commitment_id] = c
        return c

    def record_allocation(self, allocation: list[dict]) -> list[Commitment]:
        return [self.record(a["supplier_id"], a["quantity"], a["unit_price"]) for a in allocation]

    def break_commitment(self, commitment_id: str) -> Commitment:
        c = self.rows[commitment_id]
        c._stamp("Broken")
        return c

    def repair(self, commitment_id: str, new_supplier_id: str, unit_price: float,
               quantity: int | None = None) -> Commitment:
        """Re-commit the broken quantity to a new supplier. ONLY this row moves.

        `quantity` is what the backup actually covers; if it is less than the broken
        amount, `shortfall` is set on the row so the gap is surfaced (escalated),
        never silently hidden behind the original number.
        """
        c = self.rows[commitment_id]
        c._stamp("Repairing")
        original = c.quantity
        c.supplier_id = new_supplier_id
        c.unit_price = unit_price
        if quantity is not None:
            c.quantity = quantity
            c.shortfall = max(0, original - quantity)
        c._stamp("Re-committed")
        return c

    def to_list(self) -> list[dict]:
        return [{
            "commitment_id": c.commitment_id, "case_id": c.case_id,
            "supplier_id": c.supplier_id, "quantity": c.quantity,
            "unit_price": c.unit_price, "status": c.status,
            "shortfall": c.shortfall,
            "status_history": c.status_history,
        } for c in self.rows.values()]
