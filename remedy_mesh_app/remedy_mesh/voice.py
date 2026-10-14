"""Supplier-confirmation call — Twilio, stub-safe.

The agent places an outbound call to confirm the negotiated order with each
supplier. If Twilio isn't configured (no creds), it SIMULATES the call so the
pipeline and demo never break — the simulated supplier confirms when the agreed
price covers its own cost (it 'knows' its cost on the phone, the orchestrator does not).

Production path: call_supplier() initiates a real Twilio call whose webhook (served
by the Cloud Run app) returns confirmation_twiml(); the supplier's keypad reply is
captured server-side. For local/demo, simulate mode is used.
"""
import os

from . import data

_SID = os.environ.get("TWILIO_ACCOUNT_SID")
_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
_FROM = os.environ.get("TWILIO_FROM")
TWILIO_READY = bool(_SID and _TOKEN and _FROM)


def offer_script(quantity: int, unit_price: float, deadline_weeks: int,
                 oem: str = "our company") -> str:
    # Conversational, NOT an IVR — the supplier just answers in their own words.
    # The agent identifies as calling ON BEHALF OF the carmaker (the supplier's customer),
    # not the platform.
    return (f"Hi, this is the procurement desk calling on behalf of {oem} about an urgent "
            f"recall parts order. We need {quantity:,} units within {deadline_weeks} weeks at "
            f"${unit_price:.2f} per unit. Can you take that on? "
            f"Just let me know in your own words.")


def confirmation_twiml(quantity: int, unit_price: float, deadline_weeks: int, action_url: str,
                       oem: str = "our company") -> str:
    """TwiML the Cloud Run webhook returns when the supplier answers (production path).

    Captures the supplier's reply by BOTH speech and keypad, so Twilio returns a
    transcribed SpeechResult (the supplier's transcript line) or a Digit to action_url.
    """
    script = offer_script(quantity, unit_price, deadline_weeks, oem)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Gather input="speech" speechTimeout="auto" '
        f'action="{action_url}" method="POST"><Say voice="alice">{script}</Say></Gather>'
        '<Say voice="alice">Sorry, I did not catch that. We will follow up by email. Goodbye.</Say></Response>'
    )


def gather_say_twiml(say_text: str, action_url: str) -> str:
    """Speak `say_text` then listen (speech+dtmf) — one conversational turn."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Gather input="speech" speechTimeout="auto" '
        f'action="{action_url}" method="POST"><Say voice="alice">{say_text}</Say></Gather>'
        '<Say voice="alice">Sorry, I did not catch that. Goodbye.</Say></Response>'
    )


def closing_twiml(say_text: str) -> str:
    """Speak a final line and hang up."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say voice="alice">{say_text}</Say><Hangup/></Response>'
    )


def _supplier_cost(supplier_id: str):
    s = next((x for x in data.suppliers() if x["party_id"] == supplier_id), None)
    return s["unit_cost"] if s else None


def call_supplier(supplier_id: str, quantity: int, unit_price: float, deadline_weeks: int,
                  phone: str | None = None, webhook_url: str | None = None,
                  oem: str = "our company") -> dict:
    """Place (or simulate) the confirmation call. Returns the outcome record."""
    script = offer_script(quantity, unit_price, deadline_weeks, oem)
    phone = phone or os.environ.get("DEMO_SUPPLIER_PHONE")

    if not TWILIO_READY or not phone or not webhook_url:
        # SIMULATE: no creds, no number, or no webhook to drive the conversation.
        # A real interactive Twilio call is impossible without a webhook URL, so the
        # pipeline's inline auto-confirm always simulates; real calls go via /api/call.
        # The simulated supplier confirms when the price covers its own cost
        cost = _supplier_cost(supplier_id)
        confirmed = cost is None or unit_price >= cost
        return {
            "supplier_id": supplier_id, "mode": "simulated",
            "outcome": "confirmed" if confirmed else "declined",
            "quantity": quantity, "unit_price": unit_price, "spoken": script,
        }

    # REAL Twilio: initiate the call; the keypad reply is captured by the webhook
    from twilio.rest import Client
    client = Client(_SID, _TOKEN)
    call = client.calls.create(to=phone, from_=_FROM, url=webhook_url)
    return {
        "supplier_id": supplier_id, "mode": "twilio", "outcome": "ringing",
        "call_sid": call.sid, "quantity": quantity, "unit_price": unit_price, "spoken": script,
    }
