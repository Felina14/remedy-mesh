"""Layer 1 — Evidence Verification ("Remedy Watchdog").

Three distinct cognitive roles chained as an ADK SequentialAgent:

  EvidenceInterpreter (Flash)  raw complaint narratives -> structured claims
  RemedyInvestigator  (Pro)    runs the deterministic match; if the only missing
                               piece is the lot confirmation, chases it, re-evaluates
  VerificationAgent   (Pro)    INDEPENDENT audit vs policy; confirms or declines

The LLMs interpret, orchestrate and audit. The *decision* is always the return
value of the deterministic evaluate_case tool — the agents never invent a verdict.
"""
from google.adk.agents import LlmAgent, SequentialAgent

from .config import MODEL_FLASH, MODEL_PRO
from . import tools

evidence_interpreter = LlmAgent(
    name="EvidenceInterpreter",
    model=MODEL_FLASH,
    tools=tools.INTERPRETER_TOOLS,
    instruction=(
        "You are the Evidence Interpreter. The user names a case id (e.g. CASE-001). "
        "ALWAYS call get_case_complaints for that case FIRST — never ask the user, never skip it, "
        "never reply that you 'can' retrieve them; just retrieve them. From the raw narratives, "
        "produce a concise STRUCTURED summary: suspected failure, component, whether vehicles had "
        "the recall repair done (and roughly when), and any sign the failure happened AFTER the "
        "repair. Do NOT decide whether to reopen the case — only structure the evidence. "
        "Output compact JSON: {case_id, component, post_repair_failures_noted, summary}."
    ),
    output_key="claims",
)

remedy_investigator = LlmAgent(
    name="RemedyInvestigator",
    model=MODEL_PRO,
    tools=tools.INVESTIGATOR_TOOLS,
    instruction=(
        "You are the Remedy Watchdog investigator. Structured claims so far: {claims}\n"
        "For the case id in the request:\n"
        "1. Call evaluate_case(case_id).\n"
        "2. If decision == 'chase_record', the evidence is sufficient EXCEPT the production-"
        "lot confirmation is missing. Call obtain_lot_record(case_id) to obtain it via the "
        "service-center contact chain, then call evaluate_case(case_id) AGAIN.\n"
        "3. Report the final decision verbatim from the tool. Never assert a confirmation the "
        "tool did not return.\n"
        "Output JSON: {case_id, decision, criteria, obtained_record}."
    ),
    output_key="investigation",
)

verification_agent = LlmAgent(
    name="VerificationAgent",
    model=MODEL_PRO,
    tools=tools.VERIFIER_TOOLS,
    instruction=(
        "You are the INDEPENDENT Verification agent. Do not trust the investigator's "
        "narrative ({investigation}). Independently call evaluate_case(case_id) and get_policy(). "
        "Confirm the case ONLY if evaluate_case returns decision == 'confirmed_trigger_mesh' AND "
        "every item in policy.evidence_sufficiency_criteria is satisfied in the returned criteria. "
        "List any missing criteria explicitly. Choose recommended_action by WHY it did not confirm:\n"
        "- decision == 'remedy_quality_flag' or scope_drift_shown == false: the lot is WITHIN the "
        "original recall scope, so this is a remedy-QUALITY problem, NOT a scope expansion. Do NOT "
        "recommend chasing a record. Recommend: flag as a remedy-quality issue and monitor.\n"
        "- scope_drift_shown == true but install_date_confirmed == false: scope has drifted but the "
        "lot confirmation is missing — recommend the Resolution Loop chase that record.\n"
        "- too few complaints / no signal: recommend close as no-signal.\n"
        "Output the final verdict JSON: {case_id, confirmed, decision, missing_criteria, "
        "recommended_action, trigger_mesh}. Set trigger_mesh true only when confirmed is true."
    ),
    output_key="verdict",
)

remedy_watchdog = SequentialAgent(
    name="RemedyWatchdog",
    sub_agents=[evidence_interpreter, remedy_investigator, verification_agent],
)
