"""Purchase-order PDF for a confirmed supplier commitment.

After a supplier confirms on the call, the agent DRAFTS a purchase order — it does
not send it. A human reviews the draft (with the call transcript attached, so they
can see exactly what was agreed and in whose words) and clicks Approve & Send.
This is the human-in-the-loop checkpoint on the agent's autonomy.

build_po_pdf() renders a real PDF to `out_path`; the caller serves it and attaches
it to the notification the human acts on.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

INK = colors.HexColor("#14161c")
MUTE = colors.HexColor("#6b7280")
ACCENT = colors.HexColor("#2b5bff")
LINE = colors.HexColor("#e5e7eb")


def _styles():
    ss = getSampleStyleSheet()
    base = ss["Normal"]
    base.fontName, base.fontSize, base.textColor, base.leading = "Helvetica", 9.5, INK, 14
    return {
        "body": base,
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=24),
        "kicker": ParagraphStyle("kicker", parent=base, fontName="Helvetica-Bold",
                                  fontSize=8, textColor=ACCENT, leading=12, spaceAfter=2),
        "label": ParagraphStyle("label", parent=base, fontSize=7.5, textColor=MUTE, leading=10),
        "val": ParagraphStyle("val", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=14),
        "mute": ParagraphStyle("mute", parent=base, fontSize=8.5, textColor=MUTE, leading=13),
        "trans": ParagraphStyle("trans", parent=base, fontSize=8.5, leading=13),
    }


def po_number(case_id: str, commitment_id: str) -> str:
    return f"PO-{case_id.replace('CASE-', '')}-{commitment_id.replace('CMT-', '')}"


def build_po_pdf(case: dict, supplier: dict, commitment: dict, transcript: list[dict],
                 out_path: str, confirmed_at: str | None = None) -> str:
    """Render a purchase-order PDF. `commitment` is a dict with supplier_id, quantity,
    unit_price, commitment_id. `transcript` is [{actor, text}, ...]. Returns out_path."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    S = _styles()
    pon = po_number(case["case_id"], commitment["commitment_id"])
    issued = datetime.now(timezone.utc).strftime("%d %b %Y")
    qty = int(commitment["quantity"])
    price = float(commitment["unit_price"])
    total = qty * price
    deadline = commitment.get("deadline_weeks", 12)
    sup_name = supplier.get("party_name", commitment["supplier_id"])

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title=pon)
    flow = []

    flow.append(Paragraph(f"{case['oem'].upper()}", S["kicker"]))
    flow.append(Paragraph("Purchase Order — recall remedy procurement", S["h1"]))
    flow.append(Spacer(1, 4))
    flow.append(HRFlowable(width="100%", thickness=1, color=LINE))
    flow.append(Spacer(1, 10))

    # Meta grid: PO no / date / case / campaign
    meta = [
        [Paragraph("PURCHASE ORDER", S["label"]), Paragraph("ISSUED", S["label"]),
         Paragraph("CASE", S["label"]), Paragraph("CAMPAIGN", S["label"])],
        [Paragraph(pon, S["val"]), Paragraph(issued, S["val"]),
         Paragraph(case["case_id"], S["val"]), Paragraph(case.get("campaign_id", "—"), S["val"])],
    ]
    t = Table(meta, colWidths=[42 * mm, 38 * mm, 40 * mm, 40 * mm])
    t.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                           ("TOPPADDING", (0, 1), (-1, 1), 0)]))
    flow.append(t)
    flow.append(Spacer(1, 12))

    # Supplier + buyer
    party = [
        [Paragraph("SUPPLIER", S["label"]), Paragraph("BUYER", S["label"])],
        [Paragraph(f"{sup_name}<br/><font size=8 color='#6b7280'>{commitment['supplier_id']}</font>", S["val"]),
         Paragraph(f"{case['oem']}<br/><font size=8 color='#6b7280'>Recall procurement</font>", S["val"])],
    ]
    t = Table(party, colWidths=[80 * mm, 80 * mm])
    t.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, 0), 2)]))
    flow.append(t)
    flow.append(Spacer(1, 12))

    # Line item
    rows = [
        [Paragraph("DESCRIPTION", S["label"]), Paragraph("QTY", S["label"]),
         Paragraph("UNIT PRICE", S["label"]), Paragraph("DEADLINE", S["label"]),
         Paragraph("AMOUNT", S["label"])],
        [Paragraph(f"{case['component']} — replacement units<br/>"
                   f"<font size=8 color='#6b7280'>{case.get('model', '')} · recall remedy</font>", S["body"]),
         Paragraph(f"{qty:,}", S["body"]),
         Paragraph(f"${price:,.2f}", S["body"]),
         Paragraph(f"{deadline} weeks", S["body"]),
         Paragraph(f"${total:,.2f}", S["val"])],
    ]
    t = Table(rows, colWidths=[72 * mm, 22 * mm, 26 * mm, 22 * mm, 28 * mm])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, LINE),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, LINE),
        ("TOPPADDING", (0, 1), (-1, 1), 8), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 4))
    total_style = ParagraphStyle("total", parent=S["h1"], fontSize=17, leading=20, alignment=2)
    tot = Table([[Paragraph("TOTAL", S["label"]), Paragraph(f"${total:,.2f}", total_style)]],
                colWidths=[108 * mm, 62 * mm])
    tot.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    flow.append(tot)
    flow.append(Spacer(1, 14))

    # Confirmation provenance + transcript
    flow.append(HRFlowable(width="100%", thickness=1, color=LINE))
    flow.append(Spacer(1, 8))
    when = confirmed_at or issued
    flow.append(Paragraph(
        f"<b>Confirmed by {sup_name} on a recorded call</b> ({when}). "
        "Terms below were agreed verbally and transcribed by the negotiation agent.", S["mute"]))
    flow.append(Spacer(1, 6))
    for turn in transcript:
        actor = turn.get("actor", "")
        who = "Agent" if actor.lower().startswith("agent") else sup_name
        flow.append(Paragraph(f"<b>{who}:</b> {turn.get('text', '')}", S["trans"]))
    flow.append(Spacer(1, 16))

    # Approval footer
    flow.append(HRFlowable(width="100%", thickness=1, color=LINE))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(
        f"<b>DRAFT — pending approval.</b> This purchase order is authorized only after "
        f"an operator at {case['oem']} reviews and approves it.", S["mute"]))
    doc.build(flow)
    return out_path
