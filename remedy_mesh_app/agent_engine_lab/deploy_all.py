"""Deploy EVERY distinct Remedy Mesh agent to Vertex AI Agent Engine as its own
managed resource — using the REAL agents from the codebase (not rewritten copies).

Key architecture fidelity:
  * The SUPPLIER agent is deployed ONCE. The three suppliers are three SCOPED RUNS of
    this one agent (session state {supplier_id, demand}), exactly as the live
    orchestrator does it in layer2.solicit_offer_llm. We do NOT make per-supplier
    agents — that would break the Tier-2 single-agent isolation design.
  * Layer 1's three agents (interpreter / investigator / verifier) are genuinely
    distinct roles (Flash vs Pro), so each is deployed as its own agent.

The whole remedy_mesh package (incl. the bundled _corpus) ships via extra_packages, so
each agent's tools read the real cases / complaints / suppliers / policy on the engine.

    cd remedy_mesh_app
    .venv/bin/python agent_engine_lab/deploy_all.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]          # .../remedy_mesh_app
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)                                       # so extra_packages=["remedy_mesh"] resolves


def agents_to_deploy():
    from remedy_mesh.layer1 import (evidence_interpreter, remedy_investigator,
                                    verification_agent)
    from remedy_mesh.resolution import resolution_agent
    from remedy_mesh.callunderstanding import call_understanding_agent
    from remedy_mesh.layer2 import (supplier_offer_agent, oem_counter_agent,
                                    resource_agent)
    # (key, agent, display_name, description)
    return [
        ("evidence_interpreter", evidence_interpreter,
         "Remedy Mesh — Evidence Interpreter (L1)",
         "Layer 1 / Flash. Structures raw complaint narratives into claims."),
        ("remedy_investigator", remedy_investigator,
         "Remedy Mesh — Remedy Investigator (L1)",
         "Layer 1 / Pro. Runs the deterministic evaluation; chases the lot record if needed."),
        ("verification_agent", verification_agent,
         "Remedy Mesh — Verification Agent (L1)",
         "Layer 1 / Pro. Independent audit against policy; confirms or declines reopening."),
        ("resolution_agent", resolution_agent,
         "Remedy Mesh — Resolution Agent (L1)",
         "Layer 1 / Flash. Narrates the human-facing interpretation + recommended action."),
        ("call_understanding_agent", call_understanding_agent,
         "Remedy Mesh — Call Understanding Agent",
         "Flash. Interprets a supplier's natural phone reply into agreed terms + what to say back."),
        ("supplier_offer_agent", supplier_offer_agent,
         "Remedy Mesh — Supplier Offer Agent (L2)",
         "Layer 2 / Flash. ONE agent; each supplier is a scoped run (state.supplier_id). "
         "Holds only its own cost/capacity via get_my_profile."),
        ("oem_counter_agent", oem_counter_agent,
         "Remedy Mesh — OEM Counter Agent (L2)",
         "Layer 2 / Flash. Buyer-side counter; sees one supplier's offer + a COUNT of alternatives."),
        ("resource_agent", resource_agent,
         "Remedy Mesh — Re-source Narration Agent (L2)",
         "Layer 2 / Flash. Narrates why the deterministic backfill reserve is the right backup."),
    ]


def main() -> None:
    import vertexai
    from vertexai import agent_engines
    from vertexai.preview.reasoning_engines import AdkApp

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    bucket = os.environ.get("REMEDY_AE_BUCKET", f"gs://{project}-ae-staging")
    if not project:
        print("ERROR: set GOOGLE_CLOUD_PROJECT"); sys.exit(1)

    print(f"project={project}  location={location}  staging={bucket}")
    vertexai.init(project=project, location=location, staging_bucket=bucket)

    # GOOGLE_CLOUD_PROJECT / _LOCATION are RESERVED on Agent Engine (runtime injects them).
    env_vars = {"GOOGLE_GENAI_USE_VERTEXAI": "TRUE"}
    requirements = ["google-cloud-aiplatform[adk,agent_engines]", "google-adk==2.11.0"]
    extra_packages = ["remedy_mesh"]      # ships the package + bundled _corpus

    out = APP_DIR / "agent_engine_lab" / "deployed_all.json"
    deployed = json.loads(out.read_text()) if out.exists() else {}

    for key, agent, display_name, desc in agents_to_deploy():
        if key in deployed:
            print(f"[skip] {key} already deployed -> {deployed[key]}"); continue
        print(f"\n=== deploying [{key}] {display_name} ...", flush=True)
        t0 = time.time()
        remote = agent_engines.create(
            agent_engine=AdkApp(agent=agent, enable_tracing=True),
            requirements=requirements,
            extra_packages=extra_packages,
            display_name=display_name,
            description=desc,
            env_vars=env_vars,
        )
        deployed[key] = remote.resource_name
        out.write_text(json.dumps(deployed, indent=2))
        print(f"    -> {remote.resource_name}  ({time.time()-t0:.0f}s)", flush=True)

    print("\nALL DEPLOYED:")
    print(json.dumps(deployed, indent=2))
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
