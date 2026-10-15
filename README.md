<div align="center">

# Remedy Mesh

### Catch recall scope drift from the evidence — then negotiate the fix, with a human approving every move.

*A multi-agent system built on Google ADK + Gemini (Vertex AI) for the Google AI Builder Cup 2026.*

**🔗 Live demo:** https://remedymesh-tl6pmvrlbq-uc.a.run.app
**Sign in:** Analyst `eleanor` / `analyst123` · Approver `james` / `approve123`

</div>

---

## The problem

A car recall is "closed" once the fix is in the market. But vehicles built **after** the recall's
cutoff can carry the **same defect** — and because they were never in the recall, their owners are
never told. The recall's scope has **drifted**, and nobody notices until field failures pile up.

This is real. Remedy Mesh is grounded on the actual NHTSA pattern behind campaign **24V-104** and
its **26V-422 "fix-the-fix"** follow-up. (Names, VINs and the OEM here are fictional — the pattern
and dates are real.)

Catching this today is slow and manual: someone has to notice the post-remedy failures, prove the
new vehicles are genuinely out-of-scope, line up replacement parts across suppliers, and amend the
federal recall filing. By the time that happens, more defective cars have shipped.

## The idea

**Remedy Mesh watches closed recalls against live field evidence, proves when scope has drifted —
never guesses — and then does the two things a human would otherwise do by hand:**

1. **Procures the fix** — negotiates a multi-supplier split for the replacement parts, confirms each
   deal on a **real phone call**, and drafts the purchase orders.
2. **Amends the recall** — drafts the **NHTSA Part 573** recall-amendment memo that adds the
   newly-implicated vehicles to the campaign.

Both outputs are **drafts**. A human reviews and approves every one before anything is sent or
filed. That human-in-the-loop checkpoint is the whole point: the agents do the work, people keep
authority.

---

## How it works

```
  Field evidence (complaints · campaigns · production lots · policy)
                               │
      ┌─────────────────────────▼─────────────────────────┐
      │  LAYER 1 — Evidence Verification ("Remedy Watchdog")│
      │  Deterministic core (never guesses) + Gemini        │
      │  narration + a Resolution Loop that chases missing  │
      │  records. 7-way decision taxonomy.                  │
      └─────────────────────────┬─────────────────────────┘
                                │  scope expansion CONFIRMED?
                       ┌─────────┴─────────┐
                      NO                   YES ──► drafts the Part 573 amendment
                       │                    │
             Resolution Agent        ┌──────▼──────────────────────────────┐
             proposes the next       │  LAYER 2 — Negotiation Mesh           │
             action (monitor,        │  Each supplier negotiates in its OWN  │
             chase, flag…)           │  isolated Gemini session, in parallel │
                                     │  (sealed-bid). An OEM agent counters  │
                                     │  over-ceiling offers, then allocates. │
                                     │  Real Twilio calls confirm each deal. │
                                     └──────┬───────────────────────────────┘
                                            │
              Purchase Orders + Part 573 amendment  →  NOTIFICATIONS queue
                        (a human reviews, edits, approves / files)
                                            │
                              RESOLVED  +  full audit trail
```

Two human roles wrap the whole thing (a real **maker-checker** separation of duties):

- **Analyst** — investigates cases, runs the negotiation mesh, places the confirmation calls.
- **Approver** — reviews and sends purchase orders, files Part 573 amendments. Cannot do the
  Analyst's job, and vice-versa (enforced server-side). Once a case is run, the Approver sees an
  exact **replay** of the Analyst's run — nothing re-runs.

## What makes it distinctive

**The negotiation mesh.** The core agentic pattern — each supplier bargaining in an *isolated*
Gemini session, concurrently, converging into a single OEM decision — is rendered as a live
**mesh** in the UI: parallel supplier lanes showing *opening offer → OEM counter (with the model's
reasoning) → revised*, with connectors flowing into an OEM decision node. You *see* the session
isolation happen, you don't have to be told it.

**Deterministic core, LLM on top.** Every verdict and allocation is rule-based and reproducible;
the Gemini agents interpret, narrate and negotiate *within bounds*. If the model is unavailable,
the system still produces a correct, explained result. Trust is a feature, not an afterthought.

**It closes the real-world loop.** A confirmed defect produces **both** the supplier-side purchase
orders **and** the regulator-side Part 573 recall amendment — each through the same human approval.

## Key features

- Evidence verification with a 7-way decision taxonomy (confirmed / scope-drift / remedy-quality /
  monitor / … ) and a Resolution Loop that chases missing lot records.
- Live **negotiation mesh** across 3 suppliers against a private price ceiling and delivery deadline.
- **Real Twilio phone calls** — fully conversational (no IVR), understood live by Gemini.
- **Selective re-negotiation** — if a supplier declines on the call, only that commitment is
  re-sourced to a reserve supplier, which must *also* be confirmed by call before a PO is drafted.
- **Purchase-order PDFs** (with the call transcript as provenance) and **NHTSA Part 573** amendment
  memos — drafted, reviewed, approved/filed, and emailed for real.
- **Maker-checker** auth with deterministic replay.
- **Rate-limit resilience** — a transient Gemini limit degrades gracefully (the confirmed verdict
  and the Part 573 are preserved; the run is cleanly retry-able) instead of crashing.
- Light/dark UI, live SSE run feed, a Resolved queue and a full per-case audit trail.

## Tech stack

| | |
|---|---|
| **AI** | Google ADK (`LlmAgent` + FunctionTools), Gemini 2.5 Flash on **Vertex AI** (ADC — no API key) |
| **Backend** | Python, FastAPI, Uvicorn, ReportLab (PDFs), Server-Sent Events |
| **Telephony / email** | Twilio Programmable Voice · Gmail SMTP |
| **Frontend** | Vanilla HTML/CSS/JS single-page app (no build step) |
| **Deploy** | Google Cloud Run (single container: SPA + API + webhook, one origin) |

## Running it locally

```bash
# 1) Backend (from remedy_mesh_app/, with a virtualenv that has requirements.txt)
#    Vertex AI via Application Default Credentials:
export GOOGLE_GENAI_USE_VERTEXAI=TRUE
export GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
export GOOGLE_CLOUD_LOCATION=us-central1
gcloud auth application-default login
python -m uvicorn remedy_mesh.server:app --host 127.0.0.1 --port 8080

# 2) Frontend (from frontend/)
python -m http.server 5500          # → http://localhost:5500

# Optional, for real supplier calls: Twilio creds + a public webhook (ngrok or Cloud Run).
# Optional, for real PO/573 email: PO_SMTP_* env vars (Gmail app password).
```

Sign in as **eleanor / analyst123** (Analyst) or **james / approve123** (Approver), open
**CASE-001** (the confirmed hero case), and watch the mesh negotiate.

## Repository layout

```
remedy_mesh_app/            FastAPI backend + the agent pipeline
  remedy_mesh/
    logic.py                deterministic "never guesses" core + decision taxonomy
    layer1.py / layer2.py   evidence verification · negotiation mesh
    negotiation.py          allocation + commitment ledger + selective re-negotiation
    pipeline.py             the gate (Layer 1 → Layer 2) + graceful degradation
    resolution.py           ResolutionAgent (LLM interpretation of the verdict)
    voice.py / callunderstanding.py   Twilio calls + in-call Gemini understanding
    purchase_order.py / part573.py    PO + Part 573 PDF builders
    email_po.py / auth.py / server.py other services + the API/webhook
frontend/index.html         the single-page app (dashboard, case view, mesh, notifications)
remedy_mesh_synthetic_data/ the synthetic (but realistically-shaped) corpus
Dockerfile                  single-container Cloud Run build
REMEDY_MESH_OVERVIEW.md     full system overview · DESIGN_DECISIONS.md  design log
```

---

<div align="center">

*Evidence verification → scope-drift confirmation → multi-supplier negotiation → real calls →
purchase orders + Part 573 amendment → human approval → real send/file — under a maker-checker
split with a full audit trail.*

</div>
