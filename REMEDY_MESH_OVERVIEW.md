# Remedy Mesh — Complete System Overview

*A multi-agent system that catches recall scope drift from live evidence, then
procures the fix — with a human approving every real-world action.*

Built for the **Google AI Builder Cup 2026** (JAPAC). Google ADK + Gemini on Vertex AI,
FastAPI backend, a vanilla-JS single-page app, real Twilio phone calls, and real Gmail email.

---

## 1. What it is, in one breath

A car recall gets closed when the fix is in the market. But vehicles built *after* the
recall's cutoff can carry the **same defect** and never get recalled — the recall's scope
has "drifted" and nobody notices until failures pile up. **Remedy Mesh watches closed
recalls against live field evidence, proves when scope has drifted (never guesses), and
when it's confirmed it does two things a human would otherwise do by hand:**

1. **Procures the replacement parts** — negotiates a split across suppliers, confirms each
   deal on a **real phone call**, and drafts the purchase orders.
2. **Amends the recall** — drafts the **NHTSA Part 573** recall-amendment memo that adds the
   newly-implicated vehicles to the campaign.

Both outputs are **drafts**. A human reviews and approves every one before anything is sent
or filed. That human-in-the-loop checkpoint is the whole point.

---

## 2. The problem, grounded in reality

- Shaped on real NHTSA campaigns **24V-104** and the **26V-422 "fix-the-fix"** pattern. Names,
  VINs, and the OEM are fictional (**Isar Automobile**, a BMW-inspired but non-impersonating
  brand); the *pattern and dates are real*.
- **Scope drift** = a closed/remedied campaign starts getting new complaints on vehicles
  outside its original VIN range or production-date window. Those cars were never in the
  recall, so their owners were never told.
- The cost of missing it: continued field failures on a known defect, and regulatory /
  safety exposure for the manufacturer.

---

## 3. Headline capabilities (everything it can do)

| # | Capability | What happens |
|---|-----------|--------------|
| 1 | **Evidence verification** | Ingests raw complaints, dedups, matches VIN/lot, computes post-remedy failure rate and geographic clusters, and reaches one of 7 deterministic decisions. **Never guesses** — every verdict is rule-based. |
| 2 | **Scope-drift confirmation** | Proves a case is real scope expansion only when VIN/lot boundaries are crossed *and* the failure threshold is met *and* the production lot is independently confirmed. |
| 3 | **Resolution Loop** | When the only thing missing is a lot-confirmation record, the agent *chases it* (locates the servicing dealer / regional QA contact) and re-evaluates. |
| 4 | **AI-narrated reasoning** | A Gemini agent explains *which* criterion actually triggered the verdict, over the deterministic numbers — reasoning, not just a number dump. |
| 5 | **Negotiation mesh** | On a confirmed case, negotiates a multi-supplier **split allocation** against a private price ceiling and a delivery deadline, keeping a live **commitment ledger**. |
| 6 | **Real supplier phone calls** | The analyst clicks "Call to confirm" → a **real Twilio call** goes out, fully conversational (no "press 1"), understood live by Gemini. |
| 7 | **Selective re-negotiation** | If a supplier **declines on the call**, only *that* commitment is reopened and **re-sourced to a reserve supplier** — which must *also* be confirmed by call before anything is drafted. |
| 8 | **Purchase-order drafting** | A confirmed call drafts a real **PO PDF** (with the call transcript attached as provenance) into the approver's queue. |
| 9 | **Part 573 recall amendment** | A confirmed scope-drift case auto-drafts the **NHTSA Part 573 amendment memo** (population expansion, VIN/date boundaries, evidence basis, remedy). |
| 10 | **Human-in-the-loop approvals** | PO drafts and Part 573 memos land in a **Notifications** queue. A human reviews/edits the cover email and approves. Nothing sends or files itself. |
| 11 | **Real email / filing** | Approving a PO **emails it** (Gmail SMTP) to the supplier; approving a Part 573 **files it** (emails the memo as the filing record). |
| 12 | **Maker-checker roles** | Two operators: an **Analyst** does the work, an **Approver** only approves/replays. Roles are enforced server-side (403 on violation). |
| 13 | **Deterministic replay** | Once a case has been run, re-opening it **replays** the recorded run — it never re-negotiates or re-prices. The Approver always sees exactly what the Analyst produced. |
| 14 | **Resolved queue + audit trail** | Every closed case keeps a full, ordered audit timeline (who did what, when, how). |
| 15 | **Live run feed** | The browser streams every agent/timeline line as it happens (Server-Sent Events) — you watch it think, not a spinner. |

---

## 4. Architecture at a glance

```
  Raw field evidence (complaints, campaigns, production lots, policy)
                              │
      ┌───────────────────────▼───────────────────────┐
      │  LAYER 1 — Evidence Verification                │   "Remedy Watchdog"
      │  • Deterministic core (logic.py) — never guesses│
      │  • ResolutionAgent (Gemini) — interprets verdict│
      │  • Resolution Loop — chases missing records     │
      └───────────────────────┬───────────────────────┘
                              │  decision = confirmed_trigger_mesh ?
                    ┌──────────┴──────────┐
                   NO                     YES
                    │                      │
        ResolutionAgent proposes     ┌─────▼──────────────────────────────┐
        the tag-appropriate action   │  THE GATE — scope expansion CONFIRMED│
        (monitor / chase / etc.)     └─────┬──────────────────────────────┘
                                           │
      ┌────────────────────────────────────▼───────────────────────────────┐
      │  LAYER 2 — Verified Negotiation Mesh                 "CommitMesh"     │
      │  • Deterministic allocation + commitment ledger (negotiation.py)     │
      │  • Gemini narration of each offer/counter/allocation                 │
      │  • Real Twilio calls per supplier (voice.py + callunderstanding.py)  │
      │  • Selective re-negotiation on decline → reserve supplier            │
      └────────┬──────────────────────────────────────┬─────────────────────┘
               │                                       │
        PURCHASE ORDER draft                    PART 573 amendment draft
        (purchase_order.py)                     (part573.py)
               │                                       │
               └───────────────┬───────────────────────┘
                               ▼
              NOTIFICATIONS queue — human reviews & approves
              • Approve PO  → emails it (Gmail SMTP)
              • Approve 573 → files it (emails the memo)
                               ▼
                 RESOLVED queue + full audit trail
```

**Two human roles wrap the whole thing:** the **Analyst** runs Layer 1→2 and places calls;
the **Approver** reviews drafts and sends/files. Neither can do the other's job.

---

## 5. The end-to-end journey (what actually happens, step by step)

This is the full flow for the hero case, **CASE-001** (Isar Automobile A-Hatch2, Integrated
Brake Module, campaign 24V-104):

1. **Sign in** as the Analyst — **Eleanor Ashworth** (`eleanor` / `analyst123`).
2. **Dashboard** lists 9 cases, ranked by criticality. CASE-001 shows a red **CRITICAL** chip
   and the verdict **Confirmed**.
3. **Open CASE-001.** The agents run live, streamed line-by-line:
   - *EvidenceInterpreter* structures the raw complaints.
   - *DeterministicMatch* computes VIN/lot match, dedup, post-remedy rate ratio, cluster, and
     the scope-drift flag — reaching `confirmed_trigger_mesh`.
   - *VerificationAgent* (Gemini) explains **why** it confirmed (rate-ratio spike vs. cluster).
   - *RemedyWatchdog* fires **the gate**: "Scope expansion CONFIRMED — triggering the mesh."
   - **Part 573 amendment is auto-drafted** the instant the verdict is confirmed.
   - *Layer 2* negotiates: demand **60,000 units** within **12 weeks** under a private **$128**
     ceiling. No single supplier can cover it in the window, so it **splits**:
     - **Haldane Components (SUP_A)** @ **$118.50**
     - **Correns Manufacturing (SUP_B)** @ **$124.75**
     - *(Ridgeline Precision (SUP_C) @ $131 is over ceiling → held in reserve.)*
4. **The allocation table appears** with a **"Call to confirm"** button per supplier.
5. **Analyst clicks "Call to confirm" on Haldane.** A **real phone call** is placed. The agent
   speaks the terms; the supplier replies in natural speech; Gemini understands the reply live.
   - **If confirmed** → a **PO draft** is created for Haldane (transcript attached) and lands in
     Notifications. The button becomes **"✓ Confirmed by call"** (and stays that way across
     page reloads — a confirmed supplier can never be called/charged twice).
6. **Analyst clicks "Call to confirm" on Correns.** Say Correns **declines** on the call:
   - A **"Declined"** notice is logged, and the agent **re-sources only that commitment** to the
     reserve supplier, **Ridgeline**, as a *backfill* commitment.
   - **No PO is drafted for the backfill yet** — Ridgeline must *also* be confirmed by call first
     (same rule as the primaries). If the gap can't be covered, an **escalation** is raised for a
     human decision.
7. **Notifications tab** now holds: Haldane's **PO draft**, Correns' **decline**, the Ridgeline
   **backfill** (awaiting its call), the **Part 573 amendment** — and any **escalation**.
8. **Sign in** as the Approver — **James Cole** (`james` / `approve123`).
   - Opening CASE-001 **replays** Eleanor's exact run — nothing re-runs.
   - **Approve & send** the PO → the (editable) email goes out via Gmail with the PO PDF attached.
   - **Approve & file** the Part 573 → the memo is emailed as the filing record and stamped to
     the audit trail.
9. **Case resolves** once its POs are sent; it moves to the **Resolved** tab with a complete
   **audit trail** of every step and who performed it.

For **non-confirmed** cases (the other 8), there's no negotiation. The *ResolutionAgent*
interprets the deterministic verdict and proposes the right next action (monitor, chase the
record, flag remedy quality, etc.), which the Analyst resolves — also captured in the audit trail.

---

## 6. Feature detail

### 6.1 Layer 1 — Evidence Verification ("Remedy Watchdog")

**Deterministic core (`logic.py`) — the "never guesses" rule engine.** For each case it computes:
- **Dedup** of complaints within a duplicate window.
- **Post-remedy failures**: complaints dated *after* the vehicle's repair.
- **Rate ratio**: post-remedy failure rate ÷ pre-remedy baseline (per quarter).
- **Geographic cluster**: any state crossing the cluster threshold.
- **VIN match / lot match / scope-drift**: does the implicated lot fall *outside* the original
  campaign's VIN range or production-date window?
- **Install-date confirmation**: is the production lot independently confirmed?

**The 7-decision taxonomy** (every case lands in exactly one; the UI shows a label + criticality):

| Decision tag | UI label | Criticality | Meaning |
|---|---|---|---|
| `confirmed_trigger_mesh` | **Confirmed** | 🔴 Critical | Scope drift proven — trigger negotiation + Part 573 |
| `chase_record` | **Scope drift** | 🔵 High | Evidence ready, only the lot-confirmation record is missing → Resolution Loop |
| `remedy_quality_flag` | **Remedy-quality** | 🔵 High | Failures, but *within* original scope — a fix-quality issue, not scope drift |
| `monitor` | **Monitoring** | ⚪ Medium | Scope boundary crossed but threshold not yet met — watch it |
| `keep_open` | **Open** | ⚪ Medium | Ambiguous — keep the case open |
| `insufficient_evidence` | **Insufficient** | — | Too few complaints to open |
| `no_signal` | **No signal** | — | No complaints / screened out at triage |

> Note on phrasing: screened-out cases are **"no signal / screened out"** (a triage outcome),
> *not* "cleared" — nothing was human-resolved.

**Resolution Loop (`chase_record`).** When evidence is ready but the lot isn't confirmed, the
system *obtains the record*: it finds the dealer that serviced the lot and the regional quality
contact, "confirms" the install, and **re-evaluates** — which can flip the case to Confirmed.

**ResolutionAgent (`resolution.py`) — real LLM interpretation on every case.** A Gemini agent
reads the deterministic verdict + metrics and writes a human explanation of *why* this verdict,
and (for non-confirmed cases) the recommended action, owner, and button label. **It never changes
the decision** — the determinism is preserved; the AI only interprets it. Falls back to a
deterministic explanation if the model is unavailable.

### 6.2 The Gate

A single, explicit decision point: `decision == confirmed_trigger_mesh`. Only a confirmed scope
expansion crosses into Layer 2 and triggers the Part 573 draft. Everything else is handed to the
ResolutionAgent.

### 6.3 Layer 2 — Verified Negotiation Mesh ("CommitMesh")

**Deterministic allocation + commitment ledger (`negotiation.py`).** Given demand (60,000 units,
12 weeks) and a private ceiling ($128/unit), it builds an allocation across suppliers whose
offers are under the ceiling, tracking each as a **commitment** in a ledger (supplier, quantity,
unit price, status). No single supplier can cover the demand in the window, so a **split is
forced** — which is the realistic case.

**The suppliers:**

| ID | Name | Role | Offer | Outcome |
|---|---|---|---|---|
| OEM | Isar Automobile | Buyer | ceiling $128 · target 60,000 · 12 wks | — |
| SUP_A | Haldane Components | Supplier | **$118.50** | wins (under ceiling) |
| SUP_B | Correns Manufacturing | Supplier | **$124.75** | wins (under ceiling) |
| SUP_C | Ridgeline Precision | Supplier | **$131.00** | reserve (over ceiling) |

**Gemini narration** rides on top of the deterministic offers/counters/allocation, so a judge
sees readable negotiation reasoning, not raw rows — with a deterministic fallback.

**Selective re-negotiation (`break_commitment_and_resolve`).** If a confirmed commitment later
breaks — e.g. a supplier **declines on the call** — *only that one commitment* is reopened and
back-filled from a reserve supplier, leaving the others untouched. If the gap can't be covered,
it **escalates** for a human decision. Shortfalls and backfills are shown live in the allocation.

### 6.4 Real supplier phone calls (Twilio + Gemini)

- **Proven live** US→India (a real phone rang, a real conversation happened).
- **Fully conversational** — no IVR, no "press 1 / press 2." The agent speaks the terms; the
  supplier talks back in natural speech (`<Gather input="speech">`).
- **Live understanding (`callunderstanding.py`)** runs on Vertex Gemini Flash with an **8-second
  cap** and a **keyword fallback**, so every reply is interpreted inside Twilio's ~15-second
  webhook window — the "application error / hang" problem is solved.
- **Webhook flow:** `/api/call` places the call → `/voice/{id}` speaks + gathers → `/voice/{id}/result`
  interprets the reply → the outcome (confirmed / declined) finalizes the commitment.
- **Stub-safe:** without Twilio credentials the call path simulates, so the app always runs.

### 6.5 Human-in-the-loop: Call → PO draft → approve

- A **confirmed call** drafts a **PO PDF** (`purchase_order.py`): OEM header, PO number, supplier
  + buyer, line item (part, qty, unit price, deadline, total), and — crucially — the **call
  transcript** as provenance ("confirmed by {supplier} on a recorded call"), plus a
  "DRAFT — pending approval" footer.
- It lands in **Notifications** with a pre-drafted, **editable email**. The Approver reviews, can
  edit recipient/subject/body, and clicks **Approve & send** → Gmail SMTP sends it with the PO
  attached. Or **Decline** → logged, no email.

### 6.6 Part 573 recall amendment (the regulatory half)

When a case is **Confirmed**, the system auto-drafts the **NHTSA Part 573 amendment memo**
(`part573.py`) — independent of the supplier flow. For CASE-001 it states:

- **NHTSA campaign** 24V-104, **amendment ref** 573A-24V104-001.
- **Affected population table** — original **156,000** vs. **+110,716** added by the amendment →
  revised total **266,716 vehicles**.
- **VIN & production-date boundaries** that prove the drift (lot VXM200001–VXM310716, built
  2024-03-12, *after* the original 2024-02-29 cutoff; plant Spartanburg Assembly, LOT-4471).
- **Basis for determination** — post-remedy rate ratio, confirmed post-remedy failures,
  geographic cluster, independent QA confirmation.
- **Remedy** (identical to the original campaign) + owner notification under Part 577.
- **"DRAFT — pending approval"** footer.

It queues as a **Part 573 amendment** notification. Only the **Approver** can act (Analyst gets
a 403):
- **Approve & file** → the memo is emailed as the filing record and a **"Filed Part 573
  amendment… +110,716 vehicles added"** line is stamped into the case audit trail.
- **Return** → sends the amendment back to the Analyst for revision (not filed); a **"Returned
  Part 573 amendment… for revision"** line is stamped to the audit trail, and a returned memo is
  blocked from being filed. This mirrors the PO's Approve/Decline split, so the regulatory
  artifact has a real send-back path, not just a rubber stamp.

> This closes the loop the product is actually about: a confirmed defect produces **both** the
> supplier-side PO **and** the regulator-side recall amendment, each through the same approval gate.

### 6.7 Maker-checker authentication & roles (`auth.py`)

- Two demo operators, token-based sessions, role enforced via a FastAPI dependency:
  - **Analyst — Eleanor Ashworth** (`eleanor` / `analyst123`): runs cases, places calls, resolves.
  - **Approver — James Cole** (`james` / `approve123`): approves/sends POs, files Part 573s.
- **Server-side enforcement:** approve/file/resolve endpoints return **403** for the wrong role.
- **Replay guarantee:** a case runs the agents **once**. After that, *any* re-open (Analyst or
  Approver) **replays the recorded run** — it never re-negotiates or re-prices. The Approver
  therefore always sees exactly what the Analyst produced; a fresh backend restart is the only
  thing that clears it.

### 6.8 Notifications, Resolved queue & audit trail

- **Notifications** = the human queue: PO drafts, declines, escalations, and Part 573 amendments,
  newest first, with counts. Each opens a drawer with the PDF preview, the editable cover note,
  and the role-gated action.
- **Resolved** = every closed-out case with how it was resolved and by whom.
- **Audit trail** = each case's full ordered timeline (every agent step, call turn, approval,
  filing), reused for both the live feed and the static resolved view.

---

## 7. The AI / agent layer

- **Framework:** Google **ADK** (`LlmAgent`), discoverable via `adk web` / `adk run` (`root_agent`
  in `agent.py`). Agents call **deterministic FunctionTools** (`tools.py`) — the model *reasons*,
  the tools *decide*.
- **Models:** **Gemini 2.5 Flash** on **Vertex AI** via **Application Default Credentials**
  (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`) — *not* an AI Studio API key (verified inert). Thinking is
  disabled (`thinking_budget=0`) for latency. A shared dispatch **throttle** paces LLM calls
  (`REMEDY_LLM_PACE_SECONDS`), so a full confirmed run completes in ~37s with **0** rate-limit
  errors.
- **Pattern everywhere:** *deterministic core + LLM narration on top + deterministic fallback.*
  The decision is always rule-based and reproducible; the model makes it legible. If the model is
  unavailable, the app still produces a correct, explained result.
- **Agents in play:** EvidenceInterpreter, DeterministicMatch, VerificationAgent, RemedyWatchdog
  (the gate), the Layer-2 negotiation agents, the in-call understanding agent, and the
  ResolutionAgent.

---

## 8. Data model (`remedy_mesh_synthetic_data/input/`)

All synthetic, all **Isar Automobile**, grounded on real NHTSA patterns. Served through a
pluggable data layer (`data.py`) — JSON by default, with a Firestore backend path available.

| File | Holds |
|---|---|
| `cases.json` | **9 cases** (1 Confirmed/Critical hero, 2 chase-record, 1 monitor, 1 remedy-quality, 1 insufficient, 1 keep-open, 2 no-signal) |
| `complaints.json` | Raw field complaints (VIN, date, state, repair info) |
| `remedy_campaigns.json` | Original closed recall campaigns (VIN range, production window, population, remedy, baseline rate) |
| `production_lots.json` | Production lots (plant, build date, VIN range, units, confirmation) |
| `suppliers.json` | OEM + 3 suppliers (offers, ceiling, target, deadline) |
| `dealers.json` | Dealers / regional quality contacts (used by the Resolution Loop) |
| `policy_rules.json` | Thresholds: duplicate window, min complaints, rate multiplier, cluster size |
| `commitment_ledger.json`, `negotiation_log.json`, `activity_log.json` | Seed/log scaffolding |

---

## 9. API surface (FastAPI, `server.py`)

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/cases` | List cases |
| GET | `/api/overview` | Dashboard metrics (deterministic decision per case) |
| POST | `/api/run/{case_id}` | Run a case (no simulated call) |
| GET | `/api/run_stream/{case_id}` | **SSE** live run; Analyst runs live, Approver replays |
| GET | `/api/timeline/{case_id}` | A case's timeline (audit trail) |
| GET | `/api/commitments/{case_id}` | Live ledger (reflects re-sourcing) |
| POST | `/api/break/{case_id}/{commitment_id}` | Force selective re-negotiation (demo) |
| POST | `/api/resolve/{case_id}` | Analyst resolves a non-sourcing case |
| POST | `/api/login` | Sign in (Analyst / Approver) |
| GET | `/api/resolved` | Resolved queue |
| GET | `/api/notifications` | Human queue + counts |
| GET | `/api/notifications/{id}` | One notification |
| GET | `/api/notifications/{id}/pdf` | The PO or Part 573 PDF (inline) |
| POST | `/api/notifications/{id}/approve` | **Approver** — approve & send a PO |
| POST | `/api/notifications/{id}/reject` | **Approver** — decline a PO draft / return a Part 573 memo |
| POST | `/api/notifications/{id}/file` | **Approver** — approve & file a Part 573 |
| POST | `/api/call/{case_id}/{commitment_id}` | Place a real Twilio call |
| POST | `/voice/{id}`, `/voice/{id}/result`, `/voice/{id}/transcript` | Twilio voice webhook |
| POST | `/api/dev/seed_notifications` | Demo seeding (guarded by `REMEDY_ALLOW_SEED`) |
| GET | `/healthz` | Health check |

---

## 10. Module map (`remedy_mesh/`)

| File | Responsibility |
|---|---|
| `server.py` | FastAPI app, all endpoints, Twilio webhook, notification/approval flow (Cloud Run entrypoint) |
| `logic.py` | Deterministic reasoning — the "never guesses" core + 7-decision taxonomy |
| `layer1.py` | Layer 1 orchestration — Evidence Verification ("Remedy Watchdog") |
| `layer2.py` | Layer 2 orchestration — Negotiation Mesh ("CommitMesh") |
| `negotiation.py` | Deterministic allocation + commitment ledger + selective re-negotiation |
| `pipeline.py` | The gate — runs Layer 1, and on confirm runs Layer 2, emitting one timeline |
| `resolution.py` | ResolutionAgent — LLM interpretation of the verdict (never changes it) |
| `agent.py` / `agent_runner.py` | ADK entrypoint + single-turn LLM runner |
| `tools.py` | ADK FunctionTools — the deterministic layer the agents call |
| `voice.py` | Twilio supplier-confirmation call (stub-safe) |
| `callunderstanding.py` | In-call conversational understanding on Vertex Gemini |
| `purchase_order.py` | PO PDF builder (transcript attached) |
| `part573.py` | Part 573 recall-amendment memo PDF builder |
| `email_po.py` | Send an approved PO / memo by email (Gmail SMTP, stub-safe) |
| `auth.py` | Maker-checker auth — Analyst / Approver sessions + role enforcement |
| `timeline.py` | Unified ordered event feed the UI renders |
| `data.py` | Data access layer (JSON or Firestore) |
| `config.py` | Model + runtime config |

---

## 11. Frontend (single-page app, `frontend/index.html`)

- **Vanilla JS SPA**, hash-routed: **Dashboard** (`#/`), **Case detail** (`#/case/{id}`),
  **Notifications** (`#/notifications`), **Resolved** (`#/resolved`), **Audit** (`#/audit/{id}`),
  plus a **login** overlay. Zero build step; served statically.
- **Live feed:** consumes the SSE run stream and renders each agent line as it arrives.
- **Negotiation-mesh visualization:** Layer 2 is drawn as a *mesh*, not a text log — each supplier
  is a **parallel lane** (its own isolated Gemini session) showing Opening offer → OEM counter
  (with the model's rationale) → Revised (accept/reject), and the lanes **converge via connectors
  into a central OEM decision node** that shows the counter round and the final allocation. A
  thinking lane's dot pulses, winning lanes get an accent "won" highlight, and a rate-limit retry
  shows an inline chip. This makes **session isolation self-evident** — a judge sees independent
  parallel negotiations without being told. Driven entirely by the existing SSE events (no backend
  change); works on both the live run and replay.
- **Allocation table:** state-aware "Call to confirm" buttons rendered from **server-side
  notifications**, so a confirmed supplier shows "✓ Confirmed by call" and **survives page
  reloads** (no accidental duplicate real call). Backfill commitments show a `backfill` badge;
  escalations surface inline.
- **Design system — 2-color palette:** **blue** (accent) + **red** (critical) + neutral **gray**,
  with full **light/dark** support. Criticality rails (Critical red / High blue / Medium gray),
  pill verdicts, and a clean document-preview drawer with embedded PDF.

---

## 12. Tech stack

- **AI:** Google ADK, Gemini 2.5 Flash on Vertex AI (ADC).
- **Backend:** Python, FastAPI, Uvicorn, ReportLab (PDFs), SSE.
- **Telephony:** Twilio Programmable Voice (speech gather + webhook).
- **Email:** Gmail SMTP (app password).
- **Frontend:** vanilla HTML/CSS/JS SPA.
- **Data:** JSON files (Firestore backend path available).
- **Local dev:** static server for the SPA + Uvicorn backend + ngrok tunnel for the Twilio webhook.

---

## 13. Trust & safety properties (why a judge should believe it)

- **Never guesses** — every verdict is deterministic and reproducible; the LLM interprets, it
  does not decide.
- **Human-in-the-loop** — no PO is sent and no recall is filed without a human approving it.
- **Maker-checker** — the person who does the work is not the person who approves it, enforced
  server-side.
- **No double-action** — a confirmed supplier's call button never re-arms across reloads; a
  real call (and a real charge) can't happen twice.
- **Full audit trail** — every step, call turn, approval, and filing is recorded against the case.
- **Graceful degradation** — Twilio, email, and the LLM are all stub-safe; the app always runs.
- **Rate-limit resilience** — Gemini calls are paced onto one shared dispatch schedule; if the
  negotiation still trips a transient rate limit, the run **degrades gracefully** instead of
  crashing: the confirmed Layer-1 verdict and the Part 573 amendment are preserved, the UI shows a
  clean "temporarily rate-limited — run again" card (never a raw exception), the Agents panel
  settles (never hangs on "Thinking…"), and the failed run is left **re-runnable** (not cached), so
  a retry picks up cleanly.

---

## 14. Live deployment (Google Cloud Run)

**Live URL:** https://remedymesh-tl6pmvrlbq-uc.a.run.app
*(Sign in as `eleanor` / `analyst123` or `james` / `approve123`.)*

- **One container, one origin** (`Dockerfile`): the SPA, the API, and the Twilio voice webhook
  are all served from the single Cloud Run URL — the SPA's API base is `""` (same origin), and
  `PUBLIC_BASE_URL` points the Twilio webhook at the Cloud Run URL (ngrok retired).
- **Gemini on Vertex via the runtime service account** (ADC) — the Compute SA was granted
  `roles/aiplatform.user`; a confirmed CASE-001 run on the live instance negotiates and drafts the
  Part 573 with **0** rate-limit retries.
- **In-memory state, pinned to one always-on instance** (`--min-instances 1 --max-instances 1`),
  so the fresh-on-restart behavior holds: a redeploy or a forced new revision resets to a pristine
  slate (all cases active, queue empty). Force a reset with:
  `gcloud run services update remedy-mesh --region us-central1 --update-env-vars _RESET=$(date +%s)`
- **Secrets** live as Cloud Run env vars (Twilio, Gmail SMTP, project); the demo-seed endpoint is
  **disabled in production** (`REMEDY_ALLOW_SEED` omitted).
- Redeploy after a code change: `gcloud run deploy remedy-mesh --source . --region us-central1`.

## 15. How to run it (local)

```bash
# Backend (from remedy_mesh_app/, with env for Vertex/Twilio/Gmail exported)
.venv/bin/python -m uvicorn remedy_mesh.server:app --host 127.0.0.1 --port 8080

# Frontend (from frontend/)
python3 -m http.server 5500        # → http://localhost:5500

# Twilio webhook (so real calls can reach the backend)
ngrok http 8080                    # set PUBLIC_BASE_URL to the ngrok URL
```

Sign in as **eleanor / analyst123** (Analyst) or **james / approve123** (Approver).

**Key environment variables:** `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`,
`DEMO_SUPPLIER_PHONE`, `PO_SMTP_HOST/PORT/USER/PASS`, `PO_EMAIL_FROM/TO`, `PUBLIC_BASE_URL`.
*(Secrets live in the process environment only — never committed.)*

---

## 16. What's next (roadmap)

- ✅ **Deployed to Google Cloud Run** (single-origin container, Vertex via service account, one
  always-on instance). See §14. *(Optional future: wire the Firestore backend for durable state.)*
- **Dress rehearsal** on the live URL including a deliberate call ring-out (the timeout/unresolved-call path).
- **Pitch deck + demo video** — remove the stale "Layer-1 does a phone call" line from any slides.
- **Architecture diagram** refresh to show login/roles, the ResolutionAgent, and the
  call → PO / Part 573 → approve flow.

---

*Last updated: 2026-10-14. Built end-to-end: evidence verification → scope-drift confirmation →
multi-supplier negotiation → real calls → PO + Part 573 drafts → human approval → real send/file,
all under a maker-checker split with a full audit trail.*
