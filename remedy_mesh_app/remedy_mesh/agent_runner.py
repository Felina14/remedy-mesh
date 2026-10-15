"""Tiny helper: run an ADK LlmAgent for a single turn and return its text.

When REMEDY_AGENT_ENGINE=1 and the agent is deployed on Vertex AI Agent Engine, the
turn runs on that managed agent instead of in this process (see remote_agents.py).
"""
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from . import remote_agents


async def run_agent_text(agent, text: str, state: dict | None = None) -> str:
    key = remote_agents.key_for(agent)
    if key:
        return await remote_agents.run(key, text, state)
    svc = InMemorySessionService()
    runner = Runner(agent=agent, app_name="remedy_mesh", session_service=svc)
    session = await svc.create_session(app_name="remedy_mesh", user_id="u", state=state or {})
    msg = types.Content(role="user", parts=[types.Part(text=text)])
    final = ""
    async for ev in runner.run_async(user_id="u", session_id=session.id, new_message=msg):
        if ev.is_final_response() and ev.content and ev.content.parts:
            final = ev.content.parts[0].text or ""
    return final
