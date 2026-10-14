"""Remedy Mesh backend — demo API + Twilio voice webhook (Cloud Run entrypoint).

Serves:
  GET  /api/cases                     dashboard rows
  POST /api/run/{case_id}             run Layer 1 → (gate) → Layer 2, return unified timeline
  GET  /api/run_stream/{case_id}      same run, but streamed live as Server-Sent Events
  GET  /api/timeline/{case_id}        the stored timeline (incl. live call transcript)
  POST /api/call/{case_id}/{cmt_id}   place a REAL Twilio confirmation call for a commitment
  POST /api/break/{case_id}/{cmt_id}  selective re-negotiation (break one, re-solicit)

  POST /voice/{cmt_id}               Twilio fetches this → TwiML (Say offer + Gather speech/dtmf)
  POST /voice/{cmt_id}/result        Twilio posts the supplier's SpeechResult/Digits here
  POST /voice/{cmt_id}/transcript    Twilio async transcription callback

State is in-memory for the demo (swap to Firestore later); call transcripts are
appended to the case timeline so the UI shows the agent's actions AND the call words.
"""
import asyncio
import json as _json
import os
import tempfile
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse

from . import data, pipeline, voice, layer2, auth
from . import callunderstanding as cu
from . import purchase_order as po
from . import part573
from . import email_po

app = FastAPI(title="Remedy Mesh")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

TIMELINES: dict[str, list] = {}       # case_id -> timeline events
LEDGERS: dict[str, object] = {}       # case_id -> CommitmentLedger (for reneg)
CASE_RESULTS: dict[str, dict] = {}    # case_id -> last full run result (l1/l2/resolution),
                                       # the Analyst's verdict — Approver views replay this,
                                       # nothing re-runs just because James opened the case.
PENDING_CALLS: dict[str, dict] = {}   # commitment_id -> {case_id, supplier_id, quantity, unit_price, deadline_weeks}
CALL_CONVOS: dict[str, dict] = {}     # commitment_id -> {order, history:[{actor,text}]}  (Tier-2 multi-turn)

# Human-in-the-loop queue. The agent DRAFTS (PO ready, declined, escalation); a human
# acts (Approve & Send). Newest first. PDFs live in _PO_DIR and are served on demand.
NOTIFICATIONS: list[dict] = []
_NOTIF_SEQ = 0
_PO_DIR = os.path.join(tempfile.gettempdir(), "remedy_pos")

# Case lifecycle: active (default) → resolved. Resolved cases leave the priority
# queue and appear in the Resolved tab with their audit trail (the case timeline).
CASE_STATUS: dict[str, dict] = {}   # case_id -> {status, resolved_at, action, owner, via, decision}


def _base_url(request: Request) -> str:
    return os.environ.get("PUBLIC_BASE_URL") or str(request.base_url).rstrip("/")


def _supplier(sid: str) -> dict:
    return next((s for s in data.suppliers() if s["party_id"] == sid), {"party_id": sid})


def _add_notification(**kw) -> dict:
    global _NOTIF_SEQ
    _NOTIF_SEQ += 1
    n = {"id": f"N-{_NOTIF_SEQ:03d}", "status": "pending",
         "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), **kw}
    NOTIFICATIONS.insert(0, n)
    return n


def _public_notif(n: dict) -> dict:
    """Notification without the server-only pdf_path (keeps the API JSON clean)."""
    return {k: v for k, v in n.items() if k != "pdf_path"}


def _mark_resolved(case_id: str, action: str, owner, via: str, decision: str, by: str = "Operator") -> dict:
    """Move a case to Resolved and stamp its audit trail (the case timeline), naming
    the signed-in person who resolved it."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    CASE_STATUS[case_id] = {"status": "resolved", "resolved_at": now, "action": action,
                            "owner": owner, "via": via, "decision": decision, "by": by}
    tl = TIMELINES.setdefault(case_id, [])
    tl.append({"seq": len(tl) + 1, "phase": "resolved", "actor": by,
               "text": f"Case resolved — {action}" + (f" ({owner})" if owner else ""),
               "outcome": "resolved", "meta": {"via": via, "at": now}})
    return CASE_STATUS[case_id]


DEFAULT_PO_RECIPIENT = os.environ.get("PO_EMAIL_TO", "recipient@example.com")  # real recipient comes from PO_EMAIL_TO env


def _draft_email(case: dict, sup_name: str, pon: str, qty: int, price: float,
                 deadline: int, component: str) -> dict:
    """Draft the email that goes out with the PO attached — a human reviews/edits it
    before it's sent, so it reads like a person wrote it, not a form."""
    total = qty * price
    subject = f"Purchase Order {pon} — {case['oem']} recall remedy"
    body = (
        f"Hi {sup_name},\n\n"
        f"Thank you for confirming on our call. Please find attached purchase order {pon}, "
        f"which authorizes the order we agreed:\n\n"
        f"  • Part: {component}\n"
        f"  • Quantity: {qty:,} units\n"
        f"  • Unit price: ${price:,.2f}\n"
        f"  • Delivery: within {deadline} weeks\n"
        f"  • Total: ${total:,.2f}\n\n"
        f"This order was confirmed verbally on a recorded call and is now approved on our side. "
        f"Please acknowledge receipt and confirm the delivery schedule at your earliest convenience.\n\n"
        f"Best regards,\n"
        f"Remedy Mesh — on behalf of {case['oem']}"
    )
    return {"to": DEFAULT_PO_RECIPIENT, "subject": subject, "body": body}


def _make_po_draft(case_id: str, commitment: dict, transcript: list[dict],
                   confirmed_at: str | None = None, note: str | None = None) -> dict:
    case = data.case(case_id)
    sup = _supplier(commitment["supplier_id"])
    os.makedirs(_PO_DIR, exist_ok=True)
    pon = po.po_number(case_id, commitment["commitment_id"])
    pdf_path = os.path.join(_PO_DIR, f"{pon}.pdf")
    po.build_po_pdf(case, sup, commitment, transcript, pdf_path, confirmed_at=confirmed_at)
    sup_name = sup.get("party_name", commitment["supplier_id"])
    qty, price = int(commitment["quantity"]), float(commitment["unit_price"])
    deadline = commitment.get("deadline_weeks", 12)
    return _add_notification(
        type="po_draft", case_id=case_id, commitment_id=commitment["commitment_id"],
        supplier_id=commitment["supplier_id"], supplier_name=sup_name,
        title=f"PO draft ready — {sup_name}",
        detail=f"{qty:,} units @ ${price:,.2f} · {pon}" + (f" · {note}" if note else ""),
        po_number=pon, pdf_path=pdf_path, transcript=transcript, amount=qty * price,
        # fields for the in-drawer HTML document preview (the real doc is the PDF)
        quantity=qty, unit_price=price, deadline_weeks=deadline, oem=case["oem"],
        component=case.get("component", ""), model=case.get("model", ""),
        campaign_id=case.get("campaign_id", ""), backfill_note=note or "",
        email_draft=_draft_email(case, sup_name, pon, qty, price, deadline,
                                 case.get("component", "replacement units")))


def _draft_573_filing(case: dict, memo: str, nhtsa: str, added: int) -> dict:
    """The cover note that accompanies the amendment when it's filed — a human
    reviews/edits it before the Approver files, same as the PO email."""
    oem = case.get("oem", "")
    subject = f"Part 573 Amendment {memo} — {oem} campaign {nhtsa} (population expansion)"
    body = (
        f"To: Recall Management Division, NHTSA\n\n"
        f"Please find attached amendment {memo} to safety recall campaign {nhtsa}.\n\n"
        f"This amendment expands the recall population by {added:,} vehicles produced after the "
        f"original campaign's cutoff, following a confirmed post-remedy defect trend in the "
        f"{case.get('component','subject component')}. The affected VIN range, production dates, "
        f"build location, and the evidence supporting the determination are set out in the attached "
        f"Part 573 report. The remedy is identical to the original campaign.\n\n"
        f"Owner notification under 49 CFR Part 577 will follow on the expanded population.\n\n"
        f"Regards,\n"
        f"{oem} — Office of Safety & Regulatory Compliance"
    )
    return {"to": DEFAULT_PO_RECIPIENT, "subject": subject, "body": body}


def _make_573_draft(case_id: str, l1: dict) -> dict | None:
    """Draft the Part 573 recall-amendment memo for a CONFIRMED scope expansion and
    queue it for human review — mirrors _make_po_draft, but the artifact is the
    regulatory filing, not a supplier PO. One per case (idempotent)."""
    if any(n["type"] == "part573_draft" and n.get("case_id") == case_id for n in NOTIFICATIONS):
        return None
    case = data.case(case_id)
    if not case:
        return None
    campaign = data.campaign_for(case_id) or {}
    lot = data.lot_for(case_id) or {}
    os.makedirs(_PO_DIR, exist_ok=True)
    memo = part573.memo_number(case_id, campaign.get("campaign_id", ""))
    nhtsa = part573.nhtsa_number(campaign.get("campaign_id", ""))
    pdf_path = os.path.join(_PO_DIR, f"{memo}.pdf")
    part573.build_573_pdf(case, campaign, lot, l1, pdf_path)
    added = int(lot.get("units_in_lot") or 0)
    pop_orig = int(campaign.get("population_original") or 0)
    return _add_notification(
        type="part573_draft", case_id=case_id,
        title=f"Part 573 amendment ready — campaign {nhtsa}",
        detail=f"Expands the recall by +{added:,} vehicles (confirmed scope drift) · {memo}",
        memo_number=memo, nhtsa_number=nhtsa, pdf_path=pdf_path,
        oem=case["oem"], component=case.get("component", ""), model=case.get("model", ""),
        campaign_id=campaign.get("campaign_id", ""),
        added_units=added, population_original=pop_orig, population_new=pop_orig + added,
        filing_draft=_draft_573_filing(case, memo, nhtsa, added))


async def _finalize_call_outcome(commitment_id: str, interp: dict) -> None:
    """Called when a call closes. Confirmed → PO draft for a human to approve.
    Declined → rejection notice + auto re-source (backfill PO draft, or escalation)."""
    pc = PENDING_CALLS.get(commitment_id, {})
    case_id = pc.get("case_id")
    ledger = LEDGERS.get(case_id)
    if not (case_id and ledger and commitment_id in getattr(ledger, "rows", {})):
        return
    row = ledger.rows[commitment_id]
    transcript = list(CALL_CONVOS.get(commitment_id, {}).get("history", []))
    when = datetime.now(timezone.utc).strftime("%d %b %Y")

    if interp.get("outcome") == "declined":
        sup = _supplier(row.supplier_id)
        _add_notification(
            type="rejected", case_id=case_id, commitment_id=commitment_id,
            supplier_id=row.supplier_id, supplier_name=sup.get("party_name", row.supplier_id),
            title=f"Declined — {sup.get('party_name', row.supplier_id)}",
            detail=f"Declined {row.quantity:,} units on the call. Agent is re-sourcing the gap.",
            transcript=transcript)
        oem = next(s for s in data.suppliers() if s["role"] == "oem")
        sids = [s["party_id"] for s in data.suppliers() if s["role"] == "supplier"]
        demand = {"quantity": oem["target_units"], "deadline_weeks": oem["deadline_weeks"]}
        res = await layer2.break_commitment_and_resolve(
            ledger, commitment_id, demand, oem["max_unit_cost"], sids, use_llm=False)
        tl = TIMELINES.setdefault(case_id, [])
        tl.append({"seq": len(tl) + 1, "phase": "ledger", "actor": "SelectiveReneg",
                   "text": f"{res['broken_supplier']} declined on the call — reopened ONLY that "
                           f"commitment; backfill {res['covered']}, shortfall {res['shortfall']}",
                   "outcome": "escalated" if res["escalated"] else "re-committed", "meta": res})
        # NOTE: we do NOT draft the backfill PO here. The re-source has repaired the
        # ledger row to the reserve supplier (Ridgeline), but — exactly like the primary
        # suppliers — no PO is drafted until that supplier is CONFIRMED by call. The row
        # now shows up in the case's allocation as a backfill commitment awaiting a call.
        if res["escalated"]:
            _add_notification(
                type="escalation", case_id=case_id, commitment_id=commitment_id,
                title=f"Escalation — {res['shortfall']:,} units uncovered",
                detail=f"After re-sourcing {res['broken_supplier']}'s decline, {res['shortfall']:,} "
                       f"units remain uncovered. Needs a human decision or another supplier.",
                transcript=[])
    else:
        qty = interp.get("agreed_quantity") or row.quantity
        price = interp.get("agreed_price") or row.unit_price
        _make_po_draft(case_id, {
            "commitment_id": commitment_id, "supplier_id": row.supplier_id,
            "quantity": qty, "unit_price": price,
            "deadline_weeks": pc.get("deadline_weeks", 12)},
            transcript=transcript, confirmed_at=when)


async def _notify_from_negotiation(case_id: str, l2: dict) -> None:
    """The live in-browser run (run_pipeline -> layer2.run_negotiation) places its
    confirmation calls inline and simulates each outcome right there, but — unlike
    the real Twilio webhook — never itself raises the human-in-the-loop notification.
    Without this, opening a Confirmed case in the browser (the judge's normal path)
    would negotiate and commit suppliers live, yet leave the Approver's queue
    permanently empty: the approve/decline review step only existed for the
    /api/dev/seed_notifications demo helper or a real phone call, not for what a
    judge actually sees. Wire the same _finalize_call_outcome path the webhook uses
    onto these simulated outcomes so every confirmed case surfaces a real PO draft
    (and every declined call still triggers re-sourcing / escalation) for James to
    review, exactly as it would off a real call."""
    ledger = l2.get("ledger")
    calls = l2.get("calls") or []
    if not ledger or not calls:
        return
    deadline = l2.get("demand", {}).get("deadline_weeks", 12)
    sid_to_cid = {row.supplier_id: cid for cid, row in ledger.rows.items()}
    for call in calls:
        cid = sid_to_cid.get(call.get("supplier_id"))
        if not cid or cid not in ledger.rows:
            continue
        row = ledger.rows[cid]
        PENDING_CALLS[cid] = {
            "case_id": case_id, "supplier_id": row.supplier_id,
            "quantity": row.quantity, "unit_price": row.unit_price, "deadline_weeks": deadline}
        CALL_CONVOS[cid] = {"order": {}, "history": [
            {"actor": "Agent", "text": call.get("spoken", "")},
            {"actor": call.get("supplier_id", ""),
             "text": "Yes, confirmed." if call.get("outcome") == "confirmed" else "No, we can't take this order."},
        ]}
        await _finalize_call_outcome(cid, {
            "outcome": call.get("outcome"), "agreed_quantity": call.get("quantity"),
            "agreed_price": call.get("unit_price"), "done": True})


# ----------------------------------------------------------------- demo API
@app.get("/api/cases")
def api_cases():
    return {"cases": [{
        "case_id": c["case_id"], "oem": c["oem"], "component": c["component"],
        "status": c["status"], "days_open": c["days_open"],
        "complaints": len(data.complaints_for(c["case_id"])),
        "is_hero_case": c.get("is_hero_case", False),
    } for c in data.cases()]}


@app.get("/api/overview")
def api_overview():
    """Dashboard metrics: the DERIVED decision for every case (deterministic, fast)."""
    from . import logic
    rows, counts = [], {}
    for c in data.cases():
        r = logic.evaluate_case(c["case_id"])
        dec = r["decision"]
        resolved = CASE_STATUS.get(c["case_id"], {}).get("status") == "resolved"
        # Resolved cases leave the active queue, so they must also leave the headline
        # tiles and the signal-distribution bar — otherwise "1 confirmed scope expansion"
        # would stay at 1 after CASE-001 is resolved and gone from the list below it.
        if not resolved:
            counts[dec] = counts.get(dec, 0) + 1
        rows.append({
            "case_id": c["case_id"], "oem": c["oem"], "component": c["component"],
            "model": c.get("model", ""),
            "is_hero_case": c.get("is_hero_case", False),
            "complaints": len(data.complaints_for(c["case_id"])),
            "decision": dec,
            "ratio": r["metrics"]["post_pre_rate_ratio"],
            "cluster": r["metrics"]["cluster_crossed"],
            "resolved": resolved,
        })
    order = ["confirmed_trigger_mesh", "chase_record", "remedy_quality_flag",
             "monitor", "keep_open", "insufficient_evidence", "no_signal"]
    rows.sort(key=lambda x: (order.index(x["decision"]) if x["decision"] in order else 99,
                             -x["complaints"]))
    active_total = sum(1 for x in rows if not x["resolved"])
    return {"rows": rows, "counts": counts, "total": active_total, "all_total": len(rows)}


@app.post("/api/run/{case_id}")
async def api_run(case_id: str, use_llm: bool = False):
    r = await pipeline.run_pipeline(case_id, use_llm=use_llm, confirm=False)  # no auto/simulated call; Analyst places the REAL call via the button
    TIMELINES[case_id] = r["timeline"]
    if r.get("confirmed"):
        _make_573_draft(case_id, r.get("l1"))   # confirmed scope drift -> draft the regulatory amendment
    if r.get("l2"):
        LEDGERS[case_id] = r["l2"]["ledger"]
        await _notify_from_negotiation(case_id, r["l2"])
        r = {**r, "l2": {k: v for k, v in r["l2"].items() if k != "ledger"}}  # drop the non-JSON object
    if not r.get("l2_error"):
        CASE_RESULTS[case_id] = r   # cache only a COMPLETE run; a rate-limited one stays re-runnable
    return r


@app.get("/api/run_stream/{case_id}")
async def api_run_stream(case_id: str, use_llm: bool = False, token: str | None = None,
                          authorization: str | None = Header(default=None)):
    """Server-Sent Events: push every agent/timeline line the instant it happens,
    instead of making the browser wait on one request for the whole negotiation.
    A run with 3 suppliers + counter-rounds is several sequential-feeling LLM calls
    even when they run concurrently — a judge watching this needs to SEE it
    thinking, not a spinner that looks hung for a minute. Ends with one 'done'
    event carrying the full result (verdict, allocation, commitments).

    Maker-checker split: only the Analyst's open actually runs the agents. The
    Approver's open of the SAME case replays the Analyst's stored result instead —
    nothing re-runs, re-negotiates, or re-prices just because James looked at it.
    EventSource can't send an Authorization header, so the session token travels
    as a query param here (auth.current_session already accepts either)."""
    session = auth.current_session(authorization, token)
    role = session["role"] if session else None
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(ev):
        queue.put_nowait({"type": "event", "data": ev})

    async def run_live():
        r = await pipeline.run_pipeline(case_id, use_llm=use_llm, confirm=False, on_event=on_event)  # no auto/simulated call
        TIMELINES[case_id] = r["timeline"]
        if r.get("confirmed"):
            _make_573_draft(case_id, r.get("l1"))   # confirmed scope drift -> draft the regulatory amendment
        if r.get("l2"):
            LEDGERS[case_id] = r["l2"]["ledger"]
            await _notify_from_negotiation(case_id, r["l2"])
            r = {**r, "l2": {k: v for k, v in r["l2"].items() if k != "ledger"}}
        if not r.get("l2_error"):
            CASE_RESULTS[case_id] = r   # cache only a COMPLETE run; a rate-limited one stays re-runnable
        await queue.put({"type": "done", "data": r})

    async def replay_stored():
        stored = CASE_RESULTS.get(case_id)
        if not stored:
            await queue.put({"type": "not_reviewed", "data": {"case_id": case_id}})
            return
        for ev in stored["timeline"]:
            await queue.put({"type": "event", "data": ev})
            await asyncio.sleep(0.02)
        await queue.put({"type": "done", "data": stored})

    async def run():
        try:
            # Run the agents only ONCE per case. If the case has already been run
            # (result stored), REPLAY it instead of re-negotiating from scratch —
            # for the Analyst revisiting it too, not only the Approver. Opening a case
            # that already produced a PO should show that run, not kick off a new one.
            # The Approver never runs live. A clean slate (backend restart) clears
            # CASE_RESULTS, so the next open runs fresh.
            if CASE_RESULTS.get(case_id) is None and role != "Approver":
                await run_live()
            else:
                await replay_stored()
        except Exception as e:
            await queue.put({"type": "error", "data": str(e)})

    task = asyncio.create_task(run())

    async def gen():
        try:
            while True:
                item = await queue.get()
                yield f"data: {_json.dumps(item)}\n\n"
                if item["type"] in ("done", "error", "not_reviewed"):
                    break
        finally:
            await task

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/timeline/{case_id}")
def api_timeline(case_id: str):
    return {"case_id": case_id, "timeline": TIMELINES.get(case_id, [])}


@app.get("/api/commitments/{case_id}")
def api_commitments(case_id: str):
    """The CURRENT ledger for a case — reflects re-sourcing (a declined supplier's
    commitment repaired to the reserve supplier), so the UI can re-render the
    allocation with a backfill commitment that still needs a confirmation call."""
    ledger = LEDGERS.get(case_id)
    return {"case_id": case_id, "commitments": ledger.to_list() if ledger else []}


@app.post("/api/break/{case_id}/{commitment_id}")
async def api_break(case_id: str, commitment_id: str):
    ledger = LEDGERS.get(case_id)
    if not ledger:
        return {"error": "no active negotiation for this case — run it first"}
    oem = next(s for s in data.suppliers() if s["role"] == "oem")
    sids = [s["party_id"] for s in data.suppliers() if s["role"] == "supplier"]
    demand = {"quantity": oem["target_units"], "deadline_weeks": oem["deadline_weeks"]}
    res = await layer2.break_commitment_and_resolve(
        ledger, commitment_id, demand, oem["max_unit_cost"], sids, use_llm=False)
    tl = TIMELINES.setdefault(case_id, [])
    tl.append({"seq": len(tl) + 1, "phase": "ledger", "actor": "SelectiveReneg",
               "text": f"{res['broken_supplier']} defaulted — reopened ONLY that commitment; "
                       f"backfill {res['covered']}, shortfall {res['shortfall']}",
               "outcome": "escalated" if res["escalated"] else "re-committed", "meta": res})
    return res


# ----------------------------------------------------------------- resolve + resolved queue
@app.post("/api/resolve/{case_id}")
async def api_resolve(case_id: str, request: Request,
                      session: dict = Depends(auth.require_role("Analyst"))):
    """An Analyst resolves a non-sourcing case (the in-case resolve button). Body
    carries the tag's action + owner + decision; the case moves to Resolved with
    an audit stamp naming who resolved it."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    case = data.case(case_id)
    if not case:
        return {"error": "case not found"}
    action = body.get("action") or "Resolved"
    owner = body.get("owner")
    decision = body.get("decision") or ""
    st = _mark_resolved(case_id, action, owner, via="resolution", decision=decision,
                        by=f"{session['name']} ({session['role']})")
    return {"ok": True, "case_id": case_id, "status": st}


@app.post("/api/login")
async def api_login(request: Request):
    """Sign in as one of the two demo operators (Analyst / Approver)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = auth.login(body.get("username", ""), body.get("password", ""))
    if not result:
        return Response(content='{"error":"invalid username or password"}',
                        media_type="application/json", status_code=401)
    return result


@app.get("/api/resolved")
def api_resolved():
    """The Resolved tab: every case that's been closed out, newest first, with how."""
    rows = []
    for case_id, st in CASE_STATUS.items():
        if st.get("status") != "resolved":
            continue
        c = data.case(case_id) or {"case_id": case_id, "oem": "", "component": ""}
        rows.append({
            "case_id": case_id, "oem": c.get("oem", ""), "component": c.get("component", ""),
            "decision": st.get("decision", ""), "action": st.get("action", ""),
            "owner": st.get("owner"), "via": st.get("via"), "resolved_at": st.get("resolved_at"),
            "steps": len(TIMELINES.get(case_id, [])),
        })
    rows.sort(key=lambda x: x.get("resolved_at") or "", reverse=True)
    return {"resolved": rows, "total": len(rows)}


# ----------------------------------------------------------------- notifications (human-in-the-loop)
@app.get("/api/notifications")
def api_notifications():
    """The human's queue: PO drafts to approve, declines, escalations. Newest first."""
    counts = {}
    for n in NOTIFICATIONS:
        key = "pending_po" if (n["type"] == "po_draft" and n["status"] == "pending") else n["type"]
        counts[key] = counts.get(key, 0) + 1
    return {"notifications": [_public_notif(n) for n in NOTIFICATIONS],
            "counts": counts,
            "pending": sum(1 for n in NOTIFICATIONS if n["status"] == "pending")}


@app.get("/api/notifications/{notif_id}")
def api_notification(notif_id: str):
    n = next((x for x in NOTIFICATIONS if x["id"] == notif_id), None)
    return _public_notif(n) if n else Response(content='{"error":"not found"}',
                                               media_type="application/json", status_code=404)


@app.get("/api/notifications/{notif_id}/pdf")
def api_notification_pdf(notif_id: str):
    n = next((x for x in NOTIFICATIONS if x["id"] == notif_id), None)
    if not n or not n.get("pdf_path") or not os.path.exists(n["pdf_path"]):
        return Response(content='{"error":"no pdf"}', media_type="application/json", status_code=404)
    return FileResponse(n["pdf_path"], media_type="application/pdf",
                        filename=f"{n.get('po_number', notif_id)}.pdf",
                        content_disposition_type="inline")  # inline → embeds in the drawer iframe


@app.post("/api/notifications/{notif_id}/approve")
async def api_notification_approve(notif_id: str, request: Request,
                                   session: dict = Depends(auth.require_role("Approver"))):
    """An Approver approves a PO draft → send the (possibly human-edited) email WITH
    the PO attached, and mark it Sent. Body may carry edited {to, subject, body}."""
    n = next((x for x in NOTIFICATIONS if x["id"] == notif_id), None)
    if not n:
        return {"error": "not found"}
    if n["type"] != "po_draft":
        return {"error": f"cannot approve a '{n['type']}' notification"}
    if n["status"] == "sent":
        return {"ok": True, "already": True, "notification": _public_notif(n)}
    try:
        edits = await request.json()
    except Exception:
        edits = {}
    draft = n.get("email_draft", {})
    to = (edits.get("to") or draft.get("to") or DEFAULT_PO_RECIPIENT).strip()
    subject = edits.get("subject") or draft.get("subject") or f"Purchase Order {n.get('po_number','')}"
    body = edits.get("body") or draft.get("body") or ""
    outcome = email_po.send_po(n["pdf_path"], subject, body, to=to)
    n["status"] = "sent"
    n["sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n["email"] = outcome
    n["email_draft"] = {"to": to, "subject": subject, "body": body}  # remember what was sent
    n["approved_by"] = f"{session['name']} ({session['role']})"

    # Auto-resolve the case once ALL its PO drafts have been sent (Confirmed path's
    # resolution = the POs are handed over). Escalations/declines don't block it.
    case_id = n.get("case_id")
    resolved = None
    if case_id and not any(
            x["type"] == "po_draft" and x["status"] == "pending" and x.get("case_id") == case_id
            for x in NOTIFICATIONS):
        resolved = _mark_resolved(case_id, "Purchase order(s) approved & sent", "Sourcing",
                                  via="po_sent", decision="confirmed_trigger_mesh",
                                  by=f"{session['name']} ({session['role']})")
    return {"ok": True, "email": outcome, "notification": _public_notif(n),
            "case_resolved": bool(resolved)}


@app.post("/api/notifications/{notif_id}/reject")
async def api_notification_reject(notif_id: str,
                                  session: dict = Depends(auth.require_role("Approver"))):
    """An Approver declines a PO draft (no email sent) or returns a Part 573 amendment
    to the Analyst for revision (not filed). Logged to the audit trail either way."""
    n = next((x for x in NOTIFICATIONS if x["id"] == notif_id), None)
    if not n:
        return {"error": "not found"}
    if n["type"] not in ("po_draft", "part573_draft"):
        return {"error": f"cannot decline a '{n['type']}' notification"}
    if n["status"] in ("sent", "filed"):
        return {"error": "already actioned — cannot decline"}
    n["status"] = "declined"
    n["declined_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n["declined_by"] = f"{session['name']} ({session['role']})"
    case_id = n.get("case_id")
    if case_id:
        tl = TIMELINES.setdefault(case_id, [])
        if n["type"] == "part573_draft":
            audit_text = (f"Returned Part 573 amendment {n.get('memo_number', notif_id)} "
                          f"(campaign {n.get('nhtsa_number', '')}) to the Analyst for revision.")
        else:
            audit_text = (f"Declined PO draft {n.get('po_number', notif_id)} "
                          f"for {n.get('supplier_name', n.get('supplier_id', ''))}.")
        tl.append({"seq": len(tl) + 1, "phase": "resolved", "actor": n["declined_by"],
                   "text": audit_text,
                   "outcome": "declined", "meta": {"notif_id": notif_id}})
    return {"ok": True, "notification": _public_notif(n)}


@app.post("/api/notifications/{notif_id}/file")
async def api_notification_file(notif_id: str, request: Request,
                                session: dict = Depends(auth.require_role("Approver"))):
    """An Approver approves a Part 573 amendment draft → files it (emails the memo as
    the filing record, same send path as a PO) and marks it Filed. Body may carry
    edited {to, subject, body}. Mirrors the PO approve flow for the regulatory artifact."""
    n = next((x for x in NOTIFICATIONS if x["id"] == notif_id), None)
    if not n:
        return {"error": "not found"}
    if n["type"] != "part573_draft":
        return {"error": f"cannot file a '{n['type']}' notification"}
    if n["status"] == "declined":
        return {"error": "returned for revision — cannot file"}
    if n["status"] == "filed":
        return {"ok": True, "already": True, "notification": _public_notif(n)}
    try:
        edits = await request.json()
    except Exception:
        edits = {}
    draft = n.get("filing_draft", {})
    to = (edits.get("to") or draft.get("to") or DEFAULT_PO_RECIPIENT).strip()
    subject = edits.get("subject") or draft.get("subject") or f"Part 573 Amendment {n.get('memo_number','')}"
    body = edits.get("body") or draft.get("body") or ""
    outcome = email_po.send_po(n["pdf_path"], subject, body, to=to)
    n["status"] = "filed"
    n["filed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n["email"] = outcome
    n["filing_draft"] = {"to": to, "subject": subject, "body": body}
    n["filed_by"] = f"{session['name']} ({session['role']})"
    case_id = n.get("case_id")
    if case_id:
        tl = TIMELINES.setdefault(case_id, [])
        tl.append({"seq": len(tl) + 1, "phase": "resolved", "actor": n["filed_by"],
                   "text": f"Filed Part 573 amendment {n.get('memo_number', notif_id)} "
                           f"(campaign {n.get('nhtsa_number','')}) — +{n.get('added_units',0):,} vehicles added.",
                   "outcome": "filed", "meta": {"notif_id": notif_id}})
    return {"ok": True, "email": outcome, "notification": _public_notif(n)}


@app.post("/api/dev/seed_notifications")
async def api_dev_seed(case_id: str = "CASE-001"):
    """Demo helper: populate the notifications queue with a realistic mix (one PO
    draft from a confirm, one decline → re-source → backfill + escalation) WITHOUT
    dialing a phone. Guarded by REMEDY_ALLOW_SEED so it can't run in production."""
    if not os.environ.get("REMEDY_ALLOW_SEED"):
        return {"error": "seeding disabled (set REMEDY_ALLOW_SEED=1)"}
    global _NOTIF_SEQ
    NOTIFICATIONS.clear(); _NOTIF_SEQ = 0; CASE_STATUS.clear()  # idempotent: clean demo state
    r = await pipeline.run_pipeline(case_id, use_llm=False, confirm=False)
    LEDGERS[case_id] = r["l2"]["ledger"] if r.get("l2") else None
    TIMELINES[case_id] = r["timeline"]
    ledger = LEDGERS.get(case_id)
    if not ledger:
        return {"error": f"{case_id} did not trigger a negotiation"}
    rows = list(ledger.rows.values())

    async def fake_close(row, outcome, hist):
        PENDING_CALLS[row.commitment_id] = {
            "case_id": case_id, "supplier_id": row.supplier_id, "quantity": row.quantity,
            "unit_price": row.unit_price, "deadline_weeks": 12}
        CALL_CONVOS[row.commitment_id] = {"order": {}, "history": hist}
        await _finalize_call_outcome(row.commitment_id, {
            "outcome": outcome, "agreed_quantity": row.quantity,
            "agreed_price": row.unit_price, "done": True})

    if rows:
        await fake_close(rows[0], "confirmed", [
            {"actor": "Agent", "text": f"We need {rows[0].quantity:,} units within 12 weeks at "
                                       f"${rows[0].unit_price:.2f} per unit. Can you take that on?"},
            {"actor": rows[0].supplier_id, "text": "Yes, we can do that — we'll confirm within the window."}])
    if len(rows) > 1:
        await fake_close(rows[1], "declined", [
            {"actor": "Agent", "text": f"Can you supply {rows[1].quantity:,} units within 12 weeks?"},
            {"actor": rows[1].supplier_id, "text": "No, I'm sorry — we can't take this order right now."}])
    return {"seeded": True, "notifications": len(NOTIFICATIONS)}


# ----------------------------------------------------------------- real call + webhook
@app.post("/api/call/{case_id}/{commitment_id}")
async def api_call(case_id: str, commitment_id: str, request: Request):
    """Place a REAL Twilio confirmation call for one commitment."""
    ledger = LEDGERS.get(case_id)
    row = ledger.rows.get(commitment_id) if ledger else None
    if not row:
        return {"error": "commitment not found — run the case first"}
    oem = next(s for s in data.suppliers() if s["role"] == "oem")
    oem_name = data.case(case_id)["oem"]
    PENDING_CALLS[commitment_id] = {
        "case_id": case_id, "supplier_id": row.supplier_id, "quantity": row.quantity,
        "unit_price": row.unit_price, "deadline_weeks": oem["deadline_weeks"], "oem_name": oem_name,
    }
    CALL_CONVOS[commitment_id] = {
        "order": {"quantity": row.quantity, "unit_price": row.unit_price, "deadline_weeks": oem["deadline_weeks"]},
        "history": [],
    }
    webhook = f"{_base_url(request)}/voice/{commitment_id}"
    outcome = voice.call_supplier(row.supplier_id, row.quantity, row.unit_price,
                                  oem["deadline_weeks"], webhook_url=webhook, oem=oem_name)
    tl = TIMELINES.setdefault(case_id, [])
    tl.append({"seq": len(tl) + 1, "phase": "call", "actor": "Agent → " + row.supplier_id,
               "text": outcome["spoken"], "outcome": outcome["outcome"], "meta": {"mode": outcome["mode"]}})
    return outcome


@app.post("/voice/{commitment_id}")
async def voice_twiml(commitment_id: str, request: Request):
    pc = PENDING_CALLS.get(commitment_id, {"quantity": 0, "unit_price": 0.0, "deadline_weeks": 12})
    xml = voice.confirmation_twiml(pc["quantity"], pc["unit_price"], pc["deadline_weeks"],
                                   action_url=f"{_base_url(request)}/voice/{commitment_id}/result",
                                   oem=pc.get("oem_name", "our company"))
    return Response(content=xml, media_type="application/xml")


@app.post("/voice/{commitment_id}/result")
async def voice_result(commitment_id: str, request: Request):
    """Tier-2 conversational turn: interpret the supplier's spoken reply, log the
    transcript, and either ask a follow-up or close the call."""
    form = await request.form()
    digits, speech = form.get("Digits"), form.get("SpeechResult")
    spoken = speech or {"1": "Yes, confirmed.", "2": "No, we decline."}.get(digits or "", "")
    pc = PENDING_CALLS.get(commitment_id, {})
    case_id = pc.get("case_id")
    convo = CALL_CONVOS.get(commitment_id, {"order": {"quantity": pc.get("quantity", 0),
                                                      "unit_price": pc.get("unit_price", 0),
                                                      "deadline_weeks": pc.get("deadline_weeks", 12)},
                                            "history": []})

    def log(actor, text, outcome=""):
        if case_id:
            tl = TIMELINES.setdefault(case_id, [])
            tl.append({"seq": len(tl) + 1, "phase": "call", "actor": actor, "text": text,
                       "outcome": outcome, "meta": {"mode": "twilio", "transcript": True}})

    sup = pc.get("supplier_id", "supplier")
    log(f"{sup} (supplier)", spoken or "(no response)")
    convo["history"].append({"actor": sup, "text": spoken})

    interp = await cu.interpret_call_turn(convo["order"], spoken, convo["history"][:-1])
    convo["history"].append({"actor": "Agent", "text": interp["reply_text"]})
    CALL_CONVOS[commitment_id] = convo
    log("Agent", interp["reply_text"],
        outcome=f"{interp['outcome']} · {interp['agreed_quantity']} @ ${interp['agreed_price']}")

    base = _base_url(request)
    if interp["done"]:
        # Call is closing — draft the human-in-the-loop artifact (PO draft, or a
        # rejection + auto re-source). Deterministic/fast, safe to await before the
        # closing TwiML. The human approves the send later from the Notifications tab.
        try:
            await _finalize_call_outcome(commitment_id, interp)
        except Exception as e:
            log("System", f"(could not finalize outcome: {e})")
        xml = voice.closing_twiml(interp["reply_text"])
    else:
        xml = voice.gather_say_twiml(interp["reply_text"], f"{base}/voice/{commitment_id}/result")
    return Response(content=xml, media_type="application/xml")


@app.post("/voice/{commitment_id}/transcript")
async def voice_transcript(commitment_id: str, request: Request):
    form = await request.form()
    text = form.get("TranscriptionText", "")
    pc = PENDING_CALLS.get(commitment_id, {})
    case_id = pc.get("case_id")
    if case_id and text:
        tl = TIMELINES.setdefault(case_id, [])
        tl.append({"seq": len(tl) + 1, "phase": "call",
                   "actor": pc.get("supplier_id", "supplier") + " (full transcript)",
                   "text": text, "outcome": "", "meta": {"mode": "twilio", "transcript": True}})
    return Response(status_code=204)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---- serve the single-page frontend from the same origin (so one Cloud Run URL
# serves the UI, the API, and the Twilio webhook; the SPA's API base is "" = same origin).
_FRONTEND_INDEX = os.environ.get("FRONTEND_INDEX") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "index.html")


_SPA_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}  # always revalidate the app shell, so a redeploy shows up on a normal reload


@app.get("/")
def _spa_root():
    if os.path.exists(_FRONTEND_INDEX):
        return FileResponse(_FRONTEND_INDEX, media_type="text/html", headers=_SPA_HEADERS)
    return Response(content='{"error":"frontend not found"}', media_type="application/json", status_code=404)


@app.get("/index.html")
def _spa_index():
    return _spa_root()
