"""Model + runtime config.

Dev (no billing): set GOOGLE_API_KEY to an AI Studio key.
Deploy (billing on): set GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_PROJECT,
GOOGLE_CLOUD_LOCATION — ADK then routes Gemini through Vertex on your project.
"""
import os

MODEL_FLASH = os.environ.get("REMEDY_MODEL_FLASH", "gemini-2.5-flash")  # high-volume interpretation
MODEL_PRO = os.environ.get("REMEDY_MODEL_PRO", "gemini-2.5-pro")        # reasoning / negotiation
