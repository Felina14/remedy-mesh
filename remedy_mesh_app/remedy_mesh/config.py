"""Model + runtime config.

Dev (no billing): set GOOGLE_API_KEY to an AI Studio key.
Deploy (billing on): set GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_PROJECT,
GOOGLE_CLOUD_LOCATION — ADK then routes Gemini through Vertex on your project.

On Vertex, Gemini answers 429 RESOURCE_EXHAUSTED when shared model capacity in one
region is momentarily short. Google's documented mitigations are the GLOBAL endpoint
(requests route to whichever region has capacity) and retrying with backoff, so every
agent's model is built with both. REMEDY_MODEL_LOCATION overrides the location
("" keeps the project's own region).
"""
import os

MODEL_FLASH_NAME = os.environ.get("REMEDY_MODEL_FLASH", "gemini-2.5-flash")  # high-volume interpretation
MODEL_PRO_NAME = os.environ.get("REMEDY_MODEL_PRO", "gemini-2.5-pro")        # reasoning / negotiation
MODEL_LOCATION = os.environ.get("REMEDY_MODEL_LOCATION", "global")


def _model(name: str):
    """An ADK Gemini model with retry-on-429/503 and, on Vertex, the global endpoint.
    Falls back to the plain model name where ADK is not installed (unit tests)."""
    try:
        from google.adk.models import Gemini
        from google.genai import types
    except ImportError:
        return name
    kwargs = {}
    if MODEL_LOCATION and os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE":
        kwargs["client_kwargs"] = {"location": MODEL_LOCATION}
    return Gemini(
        model=name,
        retry_options=types.HttpRetryOptions(
            attempts=5, initial_delay=1.0, max_delay=16.0, exp_base=2.0, jitter=1.0,
            http_status_codes=[429, 503]),
        **kwargs,
    )


MODEL_FLASH = _model(MODEL_FLASH_NAME)
MODEL_PRO = _model(MODEL_PRO_NAME)
