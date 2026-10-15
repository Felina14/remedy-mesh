"""Update deployed agents IN PLACE (same resource names) from this copy's code.

    cd remedy_mesh_app
    .venv/bin/python agent_engine_lab/update_agents.py remedy_investigator verification_agent call_understanding_agent
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)
sys.path.insert(0, str(APP_DIR / "agent_engine_lab"))
from deploy_all import agents_to_deploy  # noqa: E402


def main(keys: list[str]) -> None:
    import vertexai
    from vertexai import agent_engines
    from vertexai.preview.reasoning_engines import AdkApp

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    vertexai.init(project=project, location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
                  staging_bucket=f"gs://{project}-ae-staging")
    deployed = json.loads((APP_DIR / "agent_engine_lab" / "deployed_all.json").read_text())
    by_key = {k: (a, n, d) for k, a, n, d in agents_to_deploy()}
    for key in keys:
        agent, name, desc = by_key[key]
        print(f"=== updating [{key}] {deployed[key]}", flush=True)
        t0 = time.time()
        agent_engines.update(
            resource_name=deployed[key],
            agent_engine=AdkApp(agent=agent, enable_tracing=True),
            requirements=["google-cloud-aiplatform[adk,agent_engines]", "google-adk==2.11.0",
                          "google-cloud-firestore>=2.16.0"],
            extra_packages=["remedy_mesh"],
            gcs_dir_name=f"agent_engine_{key}",   # own staging folder, so updates can run in parallel
            display_name=name, description=desc,
            # REMEDY_BACKEND=firestore: the agent's tools read cases / complaints / suppliers /
            # policy live from Firestore instead of the bundled JSON.
            env_vars={"GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
                      "REMEDY_BACKEND": os.environ.get("REMEDY_BACKEND", "json")},
        )
        print(f"    -> updated ({time.time() - t0:.0f}s)", flush=True)
    print("DONE")


if __name__ == "__main__":
    main(sys.argv[1:])
