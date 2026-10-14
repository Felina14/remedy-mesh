"""Tiny helper: run an ADK LlmAgent for a single turn and return its text."""
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


async def run_agent_text(agent, text: str, state: dict | None = None) -> str:
    svc = InMemorySessionService()
    runner = Runner(agent=agent, app_name="remedy_mesh", session_service=svc)
    session = await svc.create_session(app_name="remedy_mesh", user_id="u", state=state or {})
    msg = types.Content(role="user", parts=[types.Part(text=text)])
    final = ""
    async for ev in runner.run_async(user_id="u", session_id=session.id, new_message=msg):
        if ev.is_final_response() and ev.content and ev.content.parts:
            final = ev.content.parts[0].text or ""
    return final
