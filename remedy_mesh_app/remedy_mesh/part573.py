"""Part 573 recall-amendment memo for a CONFIRMED scope expansion.

When Layer 1 confirms that a closed recall's remedied population has drifted — live
complaints on vehicles built AFTER the original campaign's cutoff — the regulatory
consequence is a Part 573 amendment: the manufacturer must add the newly-implicated
vehicles to the existing recall. This module drafts that amendment memo as a PDF.

Same human-in-the-loop pattern as the purchase order: the agent DRAFTS it (with the
Layer-1 evidence that triggered it, in the record), a person reviews it, and an
Approver approves it for filing. It is never filed automatically.

build_573_pdf() renders a real PDF to `out_path`, mirroring purchase_order.py.
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
RED = colors.HexColor("#d7263d")
LINE = colors.HexColor("#e5e7eb")


def _styles():
    ss = getSampleStyleSheet()
    base = ss["Normal"]
    base.fontName, base.fontSize, base.textColor, base.leading = "Helvetica", 9.5, INK, 14
    return {
        "body": base,
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=19, leading=23),
        "kicker": ParagraphStyle("kicker", parent=base, fontName="Helvetica-Bold",
                                  fontSize=8, textColor=ACCENT, leading=12, spaceAfter=2),
        "label": ParagraphStyle("label", parent=base, fontSize=7.5, textColor=MUTE, leading=10),
        "val": ParagraphStyle("val", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=14),
        "mute": ParagraphStyle("mute", parent=base, fontSize=8.5, textColor=MUTE, leading=13),
        "sec": ParagraphStyle("sec", parent=base, fontName="Helvetica-Bold", fontSize=10.5,
                              textColor=INK, leading=15, spaceBefore=2, spaceAfter=3),
        "red": ParagraphStyle("red", parent=base, fontName="Helvetica-Bold", fontSize=9.5,
                              textColor=RED, leading=14),
    }


def nhtsa_number(campaign_id: str) -> str:
    """CAMP-24V104 -> 24V-104 (the public NHTSA campaign number)."""
    n = (campaign_id or "").replace("CAMP-", "").strip()
    if len(n) >= 4 and n[:2].isdigit():
        return f"{n[:3]}-{n[3:]}"   # 24V104 -> 24V-104
    return n or "—"


def memo_number(case_id: str, campaign_id: str) -> str:
    return f"573A-{(campaign_id or '').replace('CAMP-', '')}-{case_id.replace('CASE-', '')}"


def _d(s: str) -> str:
    """ISO date -> '29 Feb 2024'. Tolerant of None / already-short values."""
    try:
        return datetime.fromisoformat(str(s)[:10]).strftime("%d %b %Y")
    except Exception:
        return str(s or "—")


def build_573_pdf(case: dict, campaign: dict, lot: dict, l1: dict, out_path: str) -> str:
    """Render the Part 573 amendment memo. `l1` is the logic.evaluate_case() result
    (criteria + metrics + confirmed_record) that triggered the expansion. Returns out_path."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    S = _styles()
    campaign = campaign or {}
    lot = lot or {}
    m = (l1 or {}).get("metrics", {})
    rec = (l1 or {}).get("confirmed_record") or {}

    memo = memo_number(case["case_id"], campaign.get("campaign_id", ""))
    nhtsa = nhtsa_number(campaign.get("campaign_id", ""))
    issued = datetime.now(timezone.utc).strftime("%d %b %Y")
    oem = case.get("oem", campaign.get("oem", ""))
    component = case.get("component", campaign.get("component", "the subject component"))
    model = case.get("model", "")

    pop_orig = int(campaign.get("population_original") or 0)
    added = int(lot.get("units_in_lot") or 0)
    pop_new = pop_orig + added

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title=memo)
    flow = []

    flow.append(Paragraph(f"{oem.upper()} — OFFICE OF SAFETY & REGULATORY COMPLIANCE", S["kicker"]))
    flow.append(Paragraph("Part 573 Safety Recall Report — Amendment", S["h1"]))
    flow.append(Paragraph("Expansion of recall population · 49 CFR Part 573", S["mute"]))
    flow.append(Spacer(1, 4))
    flow.append(HRFlowable(width="100%", thickness=1, color=LINE))
    flow.append(Spacer(1, 10))

    # Meta grid
    meta = [
        [Paragraph("AMENDMENT REF", S["label"]), Paragraph("ISSUED", S["label"]),
         Paragraph("NHTSA CAMPAIGN", S["label"]), Paragraph("CASE", S["label"])],
        [Paragraph(memo, S["val"]), Paragraph(issued, S["val"]),
         Paragraph(nhtsa, S["val"]), Paragraph(case["case_id"], S["val"])],
    ]
    t = Table(meta, colWidths=[44 * mm, 36 * mm, 42 * mm, 38 * mm])
    t.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, 0), 1), ("TOPPADDING", (0, 1), (-1, 1), 0)]))
    flow.append(t)
    flow.append(Spacer(1, 12))

    # Why
    flow.append(Paragraph("Reason for amendment", S["sec"]))
    flow.append(Paragraph(
        f"Campaign {nhtsa} (remedy in market since {_d(campaign.get('remedy_available_date'))}) is being "
        f"<b>expanded</b>. Monitoring of live field evidence identified a confirmed defect trend in "
        f"{model} vehicles equipped with the {component} that were produced <b>after</b> the original "
        f"campaign's production cutoff and therefore fall outside the originally reported population. "
        f"The post-remedy failure pattern meets the manufacturer's threshold for scope expansion.", S["body"]))
    flow.append(Spacer(1, 10))

    # Population delta
    flow.append(Paragraph("Affected population", S["sec"]))
    rows = [
        [Paragraph("", S["label"]), Paragraph("ORIGINAL CAMPAIGN", S["label"]),
         Paragraph("ADDED BY THIS AMENDMENT", S["label"])],
        [Paragraph("Vehicles", S["body"]),
         Paragraph(f"{pop_orig:,}", S["body"]),
         Paragraph(f"+{added:,}", S["red"])],
        [Paragraph("VIN range", S["body"]),
         Paragraph(f"{campaign.get('original_vin_range_start','—')} – {campaign.get('original_vin_range_end','—')}", S["body"]),
         Paragraph(f"{lot.get('vin_range_start','—')} – {lot.get('vin_range_end','—')}", S["body"])],
        [Paragraph("Production dates", S["body"]),
         Paragraph(f"{_d(campaign.get('original_production_date_start'))} – {_d(campaign.get('original_production_date_end'))}", S["body"]),
         Paragraph(f"through {_d(lot.get('production_date'))}", S["body"])],
        [Paragraph("Build location", S["body"]),
         Paragraph("—", S["body"]),
         Paragraph(f"{lot.get('plant','—')} · lot {lot.get('lot_id','—')}", S["body"])],
    ]
    t = Table(rows, colWidths=[34 * mm, 63 * mm, 63 * mm])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 1), (-1, -1), 6), ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(
        f"Revised total recall population: <b>{pop_new:,} vehicles</b> "
        f"(+{added:,} added by this amendment).", S["mute"]))
    flow.append(Spacer(1, 12))

    # Basis / evidence
    flow.append(Paragraph("Basis for the determination", S["sec"]))
    cluster = "a geographic complaint cluster was also observed; " if m.get("cluster_crossed") else ""
    flow.append(Paragraph(
        f"Deduplicated field complaints on the added population show a post-remedy failure rate "
        f"<b>{m.get('post_pre_rate_ratio','—')}×</b> the pre-remedy baseline across "
        f"{m.get('post_remedy_failures','—')} confirmed post-remedy failures "
        f"(threshold {m.get('rate_threshold','—')}×); {cluster}"
        f"the implicated production lot was independently confirmed by {rec.get('confirmed_by','regional quality')}. "
        f"VIN/lot matching places these vehicles beyond the original VIN and production-date boundaries, "
        f"establishing scope drift rather than in-scope remedy failure.", S["body"]))
    flow.append(Spacer(1, 10))

    # Remedy
    flow.append(Paragraph("Remedy", S["sec"]))
    flow.append(Paragraph(
        f"{campaign.get('remedy_description','The established campaign remedy will be applied.')} "
        f"The remedy for the added population is identical to the original campaign; owners of the "
        f"newly-included vehicles will be notified per 49 CFR Part 577.", S["body"]))
    flow.append(Spacer(1, 16))

    # Draft footer
    flow.append(HRFlowable(width="100%", thickness=1, color=LINE))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(
        f"<b>DRAFT — pending approval.</b> This amendment is prepared for review by {oem} "
        f"regulatory affairs. It is filed with NHTSA only after an authorized approver signs off.", S["mute"]))
    doc.build(flow)
    return out_path
