"""Send an approved purchase order by email — stub-safe.

Human-in-the-loop: this runs ONLY after a human clicks Approve & Send. If SMTP creds
are configured it emails the PO PDF to the demo inbox; otherwise it SIMULATES the send
(returns a 'simulated' outcome) so the flow never breaks in local/demo without creds.

Configure (all env, nothing on disk):
  PO_SMTP_HOST, PO_SMTP_PORT (default 587), PO_SMTP_USER, PO_SMTP_PASS,
  PO_EMAIL_FROM (defaults to PO_SMTP_USER), PO_EMAIL_TO (demo inbox).
Gmail: host=smtp.gmail.com, port=587, user=<addr>, pass=<app password>.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

_HOST = os.environ.get("PO_SMTP_HOST")
_PORT = int(os.environ.get("PO_SMTP_PORT", "587"))
_USER = os.environ.get("PO_SMTP_USER")
_PASS = os.environ.get("PO_SMTP_PASS")
_FROM = os.environ.get("PO_EMAIL_FROM") or _USER
_TO = os.environ.get("PO_EMAIL_TO")
EMAIL_READY = bool(_HOST and _USER and _PASS and _TO)


def send_po(pdf_path: str, subject: str, body: str, to: str | None = None) -> dict:
    """Email the PO PDF. Returns an outcome record; never raises on a send failure."""
    recipient = to or _TO
    if not EMAIL_READY or not recipient:
        return {"mode": "simulated", "sent": True, "to": recipient or "(no inbox configured)",
                "note": "SMTP not configured — send simulated."}
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, _FROM, recipient
        msg.set_content(body)
        with open(pdf_path, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                               filename=os.path.basename(pdf_path))
        with smtplib.SMTP(_HOST, _PORT, timeout=20) as s:
            s.starttls()
            s.login(_USER, _PASS)
            s.send_message(msg)
        return {"mode": "smtp", "sent": True, "to": recipient}
    except Exception as e:  # never break the human's approve action
        return {"mode": "smtp", "sent": False, "to": recipient, "error": str(e)[:200]}
