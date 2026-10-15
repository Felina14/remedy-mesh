"""ADK FunctionTools — the deterministic layer the Gemini agents call.

Each tool is a plain typed function with a docstring; ADK exposes the docstring
and signature to the model. The model decides WHEN to call them; the functions
decide WHAT the verdict is. Importing ADK is optional for unit tests — the raw
functions live in logic.py and are tested directly.
"""
from . import data, logic


def list_cases() -> dict:
    """Return the case dashboard: every case with its id, OEM, component, status and complaint count."""
    out = []
    for c in data.cases():
        out.append({
            "case_id": c["case_id"],
            "oem": c["oem"],
            "component": c["component"],
            "status": c["status"],
            "days_open": c["days_open"],
            "complaint_count": len(data.complaints_for(c["case_id"])),
            "is_hero_case": c.get("is_hero_case", False),
        })
    return {"cases": out}


def evaluate_case(case_id: str) -> dict:
    """Deterministically evaluate whether a closed/remedied case needs reopening.

    Computes the evidence-sufficiency criteria (vin_match, lot_match,
    scope_drift_shown, install_date_confirmed, duplicate_cleared,
    threshold_crossed) and returns a decision. Never guesses — pure record math.
    """
    return logic.evaluate_case(case_id)


def obtain_lot_record(case_id: str) -> dict:
    """Chase and obtain the one missing production-lot confirmation for a case by
    looking up the service center that worked the lot. Flips install_date_confirmed
    to True so the case can be re-evaluated."""
    return logic.obtain_lot_record(case_id)


def get_case_complaints(case_id: str, limit: int = 15) -> dict:
    """Return raw complaint narratives for a case so the Evidence Interpreter can
    structure them. Returns a capped sample plus the total count."""
    allc = data.complaints_for(case_id)
    sample = [{
        "vin": x["vin"], "date": x["date"], "state": x["state"],
        "repair_completed": x["repair_completed"], "repair_date": x["repair_date"],
        "narrative": x["narrative"],
    } for x in allc[:limit]]
    return {"case_id": case_id, "total_complaints": len(allc), "sample": sample}


def get_policy() -> dict:
    """Return the evidence-sufficiency rules and thresholds (the referee) the
    verification step must apply."""
    return data.policy_rules()


def _supplier(party_id: str):
    return next((s for s in data.suppliers() if s["party_id"] == party_id), None)


# ADK tool objects. Guarded so logic.py stays importable without google-adk installed.
try:
    from google.adk.tools import FunctionTool, ToolContext

    # ---- Layer 1 ----
    def _evaluate_case_scoped(case_id: str, tool_context: ToolContext) -> dict:
        """Deterministically evaluate whether a closed/remedied case needs reopening.

        Computes the evidence-sufficiency criteria (vin_match, lot_match,
        scope_drift_shown, install_date_confirmed, duplicate_cleared,
        threshold_crossed) and returns a decision. Never guesses — pure record math.
        """
        # When the agent runs as its own service (Agent Engine), it has its own memory,
        # so a lot record the orchestrator already obtained would be invisible to it.
        # The orchestrator is the source of truth and passes its obtained records in the
        # session state; sync to them first so every agent audits the same records.
        records = tool_context.state.get("lot_records")
        if isinstance(records, dict):
            if case_id in records:
                logic._CONFIRMED[case_id] = records[case_id]
            else:
                logic._CONFIRMED.pop(case_id, None)
        return logic.evaluate_case(case_id)
    _evaluate_case_scoped.__name__ = "evaluate_case"   # the name the agents' instructions use

    list_cases_tool = FunctionTool(list_cases)
    evaluate_tool = FunctionTool(_evaluate_case_scoped)
    obtain_record_tool = FunctionTool(obtain_lot_record)
    policy_tool = FunctionTool(get_policy)
    complaints_tool = FunctionTool(get_case_complaints)
    INTERPRETER_TOOLS = [list_cases_tool, complaints_tool]
    INVESTIGATOR_TOOLS = [evaluate_tool, obtain_record_tool, policy_tool]
    VERIFIER_TOOLS = [evaluate_tool, policy_tool]
    ALL_LAYER1_TOOLS = [list_cases_tool, complaints_tool, evaluate_tool, obtain_record_tool, policy_tool]

    # ---- Layer 2 (Tier 2 isolation: scoped by session state, never cross-supplier) ----
    def get_my_profile(tool_context: ToolContext) -> dict:
        """Return ONLY the calling supplier's own capacity and cost — scoped to this
        session's supplier. Never returns another supplier's data."""
        sid = tool_context.state.get("supplier_id")
        s = _supplier(sid)
        if not s:
            return {"error": f"no profile for supplier '{sid}'"}
        return {
            "supplier_id": s["party_id"],
            "capacity_units_per_week": s["capacity_units_per_week"],
            "unit_cost": s["unit_cost"],
            "existing_backlog_units": s.get("existing_backlog_units", 0),
            "lead_time_weeks": s.get("lead_time_weeks"),
            "min_order_units": s.get("min_order_units"),
        }

    def get_public_demand(tool_context: ToolContext) -> dict:
        """Return the OEM's PUBLIC ask (quantity + deadline only). Never the OEM's price ceiling."""
        d = tool_context.state.get("demand", {})
        return {"quantity": d.get("quantity"), "deadline_weeks": d.get("deadline_weeks")}

    supplier_profile_tool = FunctionTool(get_my_profile)
    demand_tool = FunctionTool(get_public_demand)
    SUPPLIER_TOOLS = [supplier_profile_tool, demand_tool]

    def get_counter_context(tool_context: ToolContext) -> dict:
        """Context for the OEM's counter decision about ONE supplier: that supplier's
        offer (quantity + price), the OEM's private ceiling, the public demand/deadline,
        and how many ALTERNATIVE suppliers exist (a COUNT only — never another
        supplier's offer). Scoped per supplier; no cross-supplier bid data."""
        return dict(tool_context.state.get("counter_ctx", {}))

    counter_context_tool = FunctionTool(get_counter_context)
    OEM_TOOLS = [counter_context_tool]
except ImportError:  # unit tests / data-only environments
    INTERPRETER_TOOLS = INVESTIGATOR_TOOLS = VERIFIER_TOOLS = ALL_LAYER1_TOOLS = []
    SUPPLIER_TOOLS = OEM_TOOLS = []
