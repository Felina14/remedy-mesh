"""Minimal demo auth — two named operators enforcing the maker-checker split:
an Analyst investigates and resolves cases, an Approver reviews and sends or
declines purchase orders / escalations. In-memory sessions, hardcoded demo
users — appropriate for a local hackathon demo, not a production auth system.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

USERS = {
    "eleanor": {"password": "analyst123", "name": "Eleanor Ashworth", "role": "Analyst"},
    "james": {"password": "approve123", "name": "James Cole", "role": "Approver"},
}

SESSIONS: dict[str, dict] = {}  # token -> {username, name, role}


def login(username: str, password: str) -> dict | None:
    u = USERS.get((username or "").strip().lower())
    if not u or u["password"] != password:
        return None
    token = secrets.token_hex(16)
    session = {"username": username, "name": u["name"], "role": u["role"]}
    SESSIONS[token] = session
    return {"token": token, **session}


def _token(authorization: str | None, token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return token


def current_session(authorization: str | None = Header(default=None), token: str | None = None) -> dict | None:
    t = _token(authorization, token)
    return SESSIONS.get(t) if t else None


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
