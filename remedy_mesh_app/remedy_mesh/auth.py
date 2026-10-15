"""Minimal demo auth — two named operators enforcing the maker-checker split:
an Analyst investigates and resolves cases, an Approver reviews and sends or
declines purchase orders / escalations. Hardcoded demo users — appropriate for a
local hackathon demo, not a production auth system.

Tokens are STATELESS: each operator's token is a stable HMAC of their username, so
a token minted before a server restart or a redeploy still validates afterward.
(The old in-memory session table meant every restart silently invalidated everyone's
login and bounced them to the sign-in screen the next time they acted — exactly the
bug this replaces.) Demo-grade: the two accounts and their passwords are public.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import Header, HTTPException

USERS = {
    "eleanor": {"password": "analyst123", "name": "Eleanor Ashworth", "role": "Analyst"},
    "james": {"password": "approve123", "name": "James Cole", "role": "Approver"},
}

# Fixed signing key so tokens are reproducible across process restarts / instances.
_SECRET = os.environ.get("REMEDY_AUTH_SECRET", "remedy-mesh-demo-signing-key-v1").encode()


def _token_for(username: str) -> str:
    """A stable per-user token — the same before and after any restart."""
    return hmac.new(_SECRET, username.strip().lower().encode(), hashlib.sha256).hexdigest()[:32]


def _session_for(username: str) -> dict:
    u = USERS[username.strip().lower()]
    return {"username": username.strip().lower(), "name": u["name"], "role": u["role"]}


def login(username: str, password: str) -> dict | None:
    key = (username or "").strip().lower()
    u = USERS.get(key)
    if not u or u["password"] != password:
        return None
    return {"token": _token_for(key), **_session_for(key)}


def _token(authorization: str | None, token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return token


def current_session(authorization: str | None = Header(default=None), token: str | None = None) -> dict | None:
    t = _token(authorization, token)
    if not t:
        return None
    # Stateless validation: match the presented token against each operator's stable token.
    for uname in USERS:
        if hmac.compare_digest(t, _token_for(uname)):
            return _session_for(uname)
    return None


def require_role(role: str):
    """FastAPI dependency: 401 if not logged in, 403 if logged in as the wrong role."""
    def dep(authorization: str | None = Header(default=None), token: str | None = None) -> dict:
        session = current_session(authorization, token)
        if not session:
            raise HTTPException(status_code=401, detail="sign in required")
        if session["role"] != role:
            raise HTTPException(status_code=403,
                                detail=f"requires the {role} role — signed in as {session['role']}")
        return session
    return dep
