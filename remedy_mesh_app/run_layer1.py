#!/usr/bin/env python3
"""Run the Layer 1 (RemedyWatchdog) pipeline on one case and stream the agents' work.

Credentials (pick ONE before running):
  Free dev:   export GOOGLE_GENAI_USE_VERTEXAI=FALSE
              export GOOGLE_API_KEY=<AI Studio key from aistudio.google.com/apikey>
  Vertex AI:  export GOOGLE_GENAI_USE_VERTEXAI=TRUE
              export GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
              export GOOGLE_CLOUD_LOCATION=us-central1
              gcloud auth application-default login

Usage:  python run_layer1.py CASE-001
"""
import asyncio
import sys

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from remedy_mesh.agent import root_agent

APP = "remedy_mesh"
USER = "demo-user"


async def run(case_id: str):
    session_service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name=APP, session_service=session_service)
    session = await session_service.create_session(app_name=APP, user_id=USER)

    prompt = (
        f"Review case {case_id}. Decide whether this closed/remedied case needs "
        f"reopening, chasing the missing record if required, and return the final "
        f"verdict with the evidence scorecard."
    )
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    print(f"\n=== RemedyWatchdog on {case_id} ===\n")
    async for event in runner.run_async(user_id=USER, session_id=session.id, new_message=message):
        author = getattr(event, "author", "?")
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if getattr(part, "function_call", None):
                fc = part.function_call
                print(f"  [{author}] tool-call → {fc.name}({dict(fc.args)})")
            elif getattr(part, "function_response", None):
                print(f"  [{author}] tool-result ← {fc_short(part.function_response)}")
            elif getattr(part, "text", None):
                tag = " (final)" if event.is_final_response() else ""
                print(f"  [{author}]{tag} {part.text.strip()}")
    print("\n=== done ===")


def fc_short(resp):
    try:
        d = dict(resp.response)
        return str(d)[:160]
    except Exception:
        return str(resp)[:160]


if __name__ == "__main__":
    case = sys.argv[1] if len(sys.argv) > 1 else "CASE-001"
    asyncio.run(run(case))
