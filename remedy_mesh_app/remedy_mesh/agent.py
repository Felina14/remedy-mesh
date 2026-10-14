"""ADK entrypoint. `adk web` / `adk run` discover `root_agent` here.

Today root_agent is Layer 1 (RemedyWatchdog). In Step 3 we wrap Layer 1 + Layer 2
(CommitMesh) so a confirmed signal flows straight into the negotiation mesh.
"""
from .layer1 import remedy_watchdog

root_agent = remedy_watchdog
