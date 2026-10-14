# Remedy Mesh — Design Decisions & Change Log

> Living document. Records what we decided, why, and what's open.
> Last updated: **2026-10-12**

> **2026-10-12 revision:** §3.3/§4/§4.1 record the chosen isolation design — **Tier 2 (in-process cost-blind)**:
> each supplier's private data is read only inside that supplier's own scoped Gemini session, so no reasoning
> step holds two suppliers' numbers AND the orchestrator never holds raw cost — without separate services.
> §2 GCP project/account identifiers redacted (public repo). §3.4 activity-feed reconciled — `activity_log.json`
> now a data-lookup sequence (phone tension lives only in the Layer-2 supplier call). Diagrams scrubbed of the
> real project id.

---

## 1. What Remedy Mesh is (one paragraph)
Two chained agent layers for automotive recalls. **Layer 1 (Evidence Verification / "Remedy Watchdog")** decides, from real records only, whether a closed/remedied case needs reopening. **Layer 2 (Verified Negotiation Mesh / "CommitMesh")** fires only on a confirmed signal and secures the replacement-part capacity the expanded recall now needs — by **calling suppliers, confirming/bargaining the order on the call, and recording verifiable commitments.** Design principle: **never confirm or commit without a real, checkable record.**

---

## 2. Stack & platform decisions

| Decision | Choice | Rationale |
|---|---|---|
| Agent framework | **Google ADK only** (`google-adk` 2.11.0) | Contest-eligible agentic platform; native multi-agent + one-command Cloud Run deploy |
| Models | **Gemini 2.5 Flash** (high-volume) + **Pro** (reasoning/negotiation) | Both support function calling + streaming; Flash/Pro split = cost-aware |
| Compute | **Cloud Run** (one service) | Required deploy target; serverless |
| Database | **Firestore** (pluggable backend, `REMEDY_BACKEND=json\|firestore`) | Document DB matches our data; "agent queries a live DB" = technicality |
| Frontend | **Firebase Hosting** (4 screens) | Eligible host; calls Cloud Run |
| Resolution lookup | **Maps Platform – Places API** | Find the service center that did the repair |
| Voice | **Twilio** (external) — webhook = Cloud Run URL (no ngrok in prod) | Supplier confirmation calls (see §4) |
| Ops | Cloud Logging/Trace (activity feed), Secret Manager (keys), Cloud Build + Artifact Registry (deploy) | |

**GCP project:** `<REDACTED — see local .env, not committed>` (account `<REDACTED>`), region `asia-south1`.
**Billing:** ENABLED ✓. **Budget alert:** ₹400 (~$5) at 50/90/100% ✓.
**Pending:** enable APIs (`firestore`, `aiplatform`, `firebasehosting`, `places`), `gcloud auth application-default login` (ADC), optional Firestore seed.

> ⚠️ The project ID and account email must stay out of the **public** submission repo. Keep them in a git-ignored `.env` / `.env.local` and reference `os.environ[...]` in code. (Diagrams have already been scrubbed to `<your-gcp-project-id>`.)

---

## 3. Key architecture decisions (with rationale)

### 3.1 No hardcoding — agents reason over a real corpus
Input data is the **pre-run state**; agents PRODUCE the verdict/commitments. Output files (`activity_log`, `negotiation_log`, `commitment_ledger`) ship empty. Golden finished-state copies kept in `expected_outputs/` to validate against. Verified: `CASE-001` genuinely computes to confirm; sparse cases → `no_signal`; CASE-009 (spike but in-scope) → remedy-quality flag, not scope expansion.

### 3.2 Layer 1 = 3 agents in ONE service (not distributed)
`EvidenceInterpreter` → `RemedyInvestigator` → `VerificationAgent` as an ADK `SequentialAgent`, in-process. Tight sequential pipeline on the same case — distributing it adds latency/fragility for no gain. Still genuinely multi-agent.

### 3.3 Supplier agent — ONE role, Tier 2 isolation (suppliers isolated from each other AND the orchestrator is cost-blind)  ← decided 2026-10-12
No separate A2A services per supplier (overkill for the time budget — that's Tier 3, see §4.1). **We build Tier 2 (in-process cost-blind):** a deterministic orchestrator sequences the work but passes only a supplier's **identity + the public demand** (quantity + deadline) to each per-supplier Gemini invocation. Each supplier's private document (`capacity_units_per_week`, `unit_cost`) is read **only inside that supplier's own scoped agent session**, via a `get_my_profile()` tool keyed to the session's `supplier_id`. The agent returns a **sanitized offer** (quantity + price + accept/counter); raw cost/capacity never leaves that agent's context. Result: (a) no single reasoning step holds two suppliers' numbers — supplier-to-supplier isolation; and (b) the orchestrator has no code path to any supplier's raw cost/capacity — the broker is cost-blind. Both enforced by code structure, not infra. Same prompt/model reused for every supplier → scales to 100+ with zero new code. The stronger *infra-level* guarantee (orchestrator credentials cannot read raw data) is Tier 3 (§4.1) — not built given six days.

### 3.4 Voice call REFRAMED — call the supplier, not an internal human  ← big change
- **Old:** Resolution Loop escalates to an internal human (quality director) by phone.
- **Problem:** that human already sees every case + output in the portal → calling them is redundant and makes the agent look less autonomous.
- **New:** the agent **calls the SUPPLIER** to confirm/bargain the order and records it. External party, real stakes, memorable "deal closed on the phone" moment.
- Voice therefore moves **from Layer-1 escalation → Layer-2 supplier confirmation.**
- Layer 1's Resolution Loop now just **obtains the lot record from data** (dealer lookup / Places); no human call.
- **Reconciled with the UI ✓:** `activity_log.json` has been rewritten to a data-lookup sequence ("Queried Places API for service center…" → "Cross-referenced dealer record…" → "Record obtained: Lot #4471, installed 2024-03-12"). All "is anyone picking up" tension now lives in the Layer-2 supplier-confirmation call — the only live telephony moment in the demo. (Screen-2 copy in the brief/deck still needs the same edit.)

### 3.5 Dropped for simplicity (updated)
- ❌ Per-supplier **isolated services/sessions / A2A** (Tier 3 — unnecessary infra for the time budget)
- ❌ Internal-human escalation phone call (moved to supplier confirmation, §3.4)
- ✅ ~~Treating "isolation" as a core mechanism~~ — **kept, via Tier 2.** The "privacy holds because we don't say it on the call" version was the flagged overclaim; Tier 2 (§3.3/§4.1) is real data-level isolation at the cost of one tool, so the claim and the UI's lock-icon panel stay true.

---

## 4. NEW Layer 2 workflow (CommitMesh with live supplier confirmation)

```
1. Demand        confirmed scope → "need 60,000 units in 12 weeks" (OEM private ceiling $128,
                  never exposed to any supplier agent or call)
2. Allocate      Orchestrator (code) runs ONE scoped Gemini session PER supplier, passing only the
                  supplier's identity + the public demand (quantity + deadline). Inside each session a
                  get_my_profile() tool reads ONLY that supplier's own Firestore doc; the agent returns
                  a sanitized offer. The orchestrator never reads raw cost/capacity — it has no code path to it:
                   SUP_A (Haldane)   agent (reads its own doc) → offers 33,000 units @ $124
                   SUP_B (Correns)   agent (reads its own doc) → offers 27,000 units @ $127
                   SUP_C (Ridgeline) agent (reads its own doc) → too expensive ($131 > ceiling), backup
                  Orchestrator allocates using these sanitized offers only.
3. CALL          agent phones each allocated supplier via Twilio:
                   "Can you fulfill 33,000 units by week 12 at $124? Confirm or counter."
4. Confirm/bargain ON THE CALL:
                   confirm → record order
                   counter price → accept if under ceiling, else reallocate
5. Record        confirmed orders + call outcome → commitment ledger (Firestore)
6. Selective reneg  if a supplier later can't deliver → call the backup; other commitments untouched
```

### 4.1 Per-supplier isolation — implementation pattern (Tier 2: in-process cost-blind)

Property that holds: **(a) no single prompt/agent step/output ever contains two suppliers' private data (supplier-to-supplier isolation), and (b) the orchestrator process never holds any supplier's raw cost/capacity (broker cost-blind)** — both enforced by code structure, without separate services.

```python
# orchestrator.py — deterministic, no LLM call here, and NO code path to raw supplier data
offers = {}
for supplier_id in eligible_supplier_ids:
    offer = run_supplier_agent(            # one scoped Gemini session per supplier
        supplier_id=supplier_id,           # identity only
        demand=public_demand,              # quantity + deadline only — never the OEM ceiling
    )
    offers[supplier_id] = offer            # {quantity, price, accept/counter} — no raw cost/capacity

# the AGENT (not the orchestrator) reads the private doc, via a session-scoped tool:
#   def get_my_profile() -> dict:  return firestore.get(f"suppliers/{session.supplier_id}")
# raw unit_cost / capacity stay inside that agent's context; only the sanitized offer returns.

allocation = allocate(offers, oem_ceiling=128.0)   # uses sanitized offers only
```

`supplier_offer_agent` is the same Gemini prompt/model reused for every supplier — that's still "one role, data-driven, scales to 100+ with zero new code." It's invoked once per supplier with an isolated context. `visible_to` in `suppliers.json` documents the supplier-to-supplier boundary; the session-scoped `get_my_profile()` tool enforces it.

**Isolation tiers (for reference):**
| Tier | How | Claim | Cost |
|---|---|---|---|
| 1 Sealed-bid | orchestrator reads each doc, passes to scoped call | suppliers isolated from each other | trivial |
| **2 In-process cost-blind (BUILDING)** | agent reads its own doc via scoped tool; orchestrator sees only sanitized offers | + orchestrator never holds raw cost | +1 tool |
| 3 Infra-isolated (not building) | per-supplier Cloud Run service + scoped service account | orchestrator *can't* read raw data (credentials) | heavy topology change |

**Tier 3 note:** only if Day 5 has spare time; it changes compute topology, not one function. Tier 2 is the honest, buildable match to the "structurally isolated" pitch.

### Call implementation — decided (2026-10-13)
- **Tier 2 (BUILT, on Vertex):** multi-turn conversational call. Twilio `<Gather input="speech">` captures the supplier's full natural-language reply → `CallUnderstandingAgent` (Gemini Pro) interprets nuance (partial / conditional / counter / confirm), carries context across turns, and returns what to say next → webhook loops (`gather_say_twiml`) or closes (`closing_twiml`). Every line logged to the timeline as a transcript. **Verified live:** "20k now, rest in 3 weeks" → agent computes the 13k remainder, confirms timing, closes. Files: `callunderstanding.py`, `agent_runner.py`, `server.py` voice routes.
- **Tier 3 (NOT feasible — abandoned):** Gemini Live API + Twilio Media Streams (real-time interruptible). Probed and blocked **three ways**: Vertex Live models not found (1008) on this project; AI Studio key #1 invalid; AI Studio key #2 authenticates but Live models not found + base models restricted on the new account. Per the "aim for 3, else 2" plan, shipped Tier 2. Revisit only if a working Live setup appears and everything else is locked.
- Twilio: real outbound **US→India call proven** (SID CA8b7d46…, completed). Trial blocks inline TwiML → we use the **hosted webhook URL** (works on trial). **Before finale: upgrade to pay-as-you-go (~$20, no expiry)**; store creds in Secret Manager; **rotate the exposed Auth Token**; keep a recorded backup.
- **End-to-end LIVE call verified locally (2026-10-13):** backend on `127.0.0.1:8080` tunneled via **ngrok** (`PUBLIC_BASE_URL`), Twilio env inline (`TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM=<twilio-number>`, `DEMO_SUPPLIER_PHONE`). Ran CASE-001 (`use_llm=false`) → commitment CMT-001 (SUP_A 33k @ $121) → `POST /api/call` → real phone rang (SID CA3317…). Human spoke naturally ("I can confirm in 12 weeks, maybe finish before 10") → Twilio transcribed → `CallUnderstandingAgent` (Vertex Pro) interpreted as confirm + early → replied, closed, logged `confirmed · 33000 @ $121.0` to timeline.
- **Bug fixed (`voice.py`):** inline auto-confirm in `run_pipeline` was placing real Twilio calls with no webhook → HTTP 400 → 500. Invariant now: **no `webhook_url` → simulate**; real interactive calls only via `/api/call` (which supplies the webhook). The pipeline's inline confirmations always simulate.
- **Call latency bug — FOUND & FIXED (2026-10-13):** first live test errored mid-call ("an application error has occurred") after the human spoke. Root cause via ngrok inspector: the `/voice/{id}/result` turn returned **status 0 / empty** to Twilio because the handler took **9s+** — the in-call interpret ran on a fresh ADK Runner with Gemini's default *thinking* ON (6–9s, variable), tipping over Twilio's ~15s webhook timeout once tunnel latency was added. Also removed the **IVR** ("press 1/2") per user — it's a pure speak-naturally call now (`<Gather input="speech">`). Fix: in-call agent uses **Flash with `thinking_budget=0`** (≈3s, predictable, still ADK `LlmAgent`), an 8s `asyncio.wait_for` cap + keyword fallback, and a hardened JSON parser (null-safe `agreed_quantity/price`). Re-verified live: full **multi-turn** call, both `/result` turns `200` in ~3s, agent confirmed + asked a follow-up + closed. Files: `callunderstanding.py`, `voice.py`.
### Human-in-the-loop: PO drafts + Notifications + decline→re-source (BUILT backend 2026-10-13)
Two additions decided with the user, both verified end-to-end (TestClient, live Vertex Flash):
- **Agent drafts, human sends.** When a supplier confirms on the call, the agent generates a real **PO PDF** (`purchase_order.py`, reportlab — light-console styled: buyer/supplier, line item, total, and the **call transcript** embedded as provenance, stamped "DRAFT — pending human approval"). It becomes a **PO-draft notification**; a human clicks **Approve & Send** → the PDF is emailed (stub-safe `email_po.py`: SMTP when `PO_SMTP_*`/`PO_EMAIL_TO` set, else simulated) and the row flips to **Sent**. Decision: email the PDF to the user's **own demo inbox** (not real supplier addresses).
- **Decline → auto re-source.** Decision: the human **declines live on the call**; `CallUnderstandingAgent` reads `outcome=declined`, which fires the existing `break_commitment_and_resolve()` → **rejected** notification + reopen ONLY that commitment + backfill from the reserve supplier → a **backfill PO-draft** for the new supplier, and if it can't fully cover, an **escalation** notification for the uncovered units. Verified: SUP_B declines 27k → Ridgeline backfills 17,200 @ $133.50 → 9,800 escalated to a human.
- Endpoints: `GET /api/notifications` (+counts), `GET /api/notifications/{id}`, `GET /api/notifications/{id}/pdf` (FileResponse), `POST /api/notifications/{id}/approve`. In-memory `NOTIFICATIONS`; PDFs in a temp dir. Call-close hook in `voice_result` → `_finalize_call_outcome()` (deterministic/fast, awaited before the closing TwiML).
- **Still needed from user for the real email:** a demo inbox (`PO_EMAIL_TO`) + SMTP creds (Gmail host/port/user + **app password**). Until then it simulates.
- **Frontend Notifications tab — BUILT & verified (2026-10-13):** top-nav entry with a live pending-count badge; notifications page with type-coded cards (po_draft/rejected/escalation) in the clean-light-console world; slide-in drawer with a PO **HTML document preview** (reliable, mirrors the PDF — embedded cross-origin PDF iframe was dropped as a demo risk), "Open the full PDF" link to the real file, the call transcript, and **Approve & Send** (→ Sent state + badge decrement). Verified end-to-end with headless Chrome/puppeteer screenshots (page, PO drawer, sent state, escalation drawer, dashboard). Dev helper `POST /api/dev/seed_notifications` (idempotent, env-guarded `REMEDY_ALLOW_SEED`) populates a realistic demo state without dialing. Notification payload enriched with quantity/unit_price/deadline/component/oem/campaign for the preview.
- **PO drawer upgraded (2026-10-13):** (1) the **real PDF renders inline** in the drawer (PDF endpoint now `Content-Disposition: inline`; verified rendering in Chrome), not just an HTML facsimile; (2) each PO notification carries a **drafted email** (`email_draft`: to/subject/human-written body) built by `_draft_email()`; (3) the drawer shows **editable** To / Subject / Message fields; (4) **Approve & send** POSTs the human's edited `{to,subject,body}` → `email_po.send_po()` emails it **with the PO PDF attached**. Default recipient comes from `PO_EMAIL_TO` (`DEFAULT_PO_RECIPIENT`). **Real send needs the Gmail App Password** (`PO_SMTP_PASS`) for that account — host/user/from already wired (`smtp.gmail.com:587`); until the password is set it simulates and the UI says so.
- **Frontend TODO (next):** case screen — per-commitment **"Call to confirm"** button that places the real Twilio call and surfaces the resulting PO draft / decline into the notifications. (Case screen already runs Layer1→gate→Layer2 live via SSE with verdict + allocation.)

### Capable for ALL tags: ResolutionAgent + Resolved tab + audit trail (2026-10-14)
Diagnosis (verified in code): Layer 1 had **zero LLM** — `logic.evaluate_case()` is pure Python and the "EvidenceInterpreter/DeterministicMatch/VerificationAgent" lines were just `tl.add` templates; `use_llm` only reached Layer 2 (Confirmed path). So non-Confirmed cases dead-ended at a templated "Not a scope expansion → no_mesh". The engine was single-path.
Fix (one agent, not six — same split as Layer 2: deterministic core authoritative, LLM narrates on top, deterministic fallback):
- **`resolution.py` → `ResolutionAgent`** (Flash, thinking-off, JSON out, 8s cap + `_fallback`). Reads the already-decided verdict + real metrics/criteria and writes a grounded `interpretation` + `recommended_action`. **Never changes `decision`** — the verdict stays the checkable deterministic part. `RESOLUTION_ACTIONS` maps each tag → resolve button label + owner (remedy_quality→Escalate to engineering/Engineering; monitor→Set watch/Monitoring; keep_open/insufficient→Request records/Records; no_signal→Dismiss).
- **`pipeline.py`** restructured: Confirmed → VerificationAgent + gate + Layer 2 (unchanged). Non-Confirmed → gate "handing to the resolution agent" + a live **`resolution` phase** streaming the ResolutionAgent's real output, and returns `resolution` in the result. Verified live: CASE-009 → "post-remedy failures 43, ratio 3.53× over the 2.5 threshold… remedy ineffective… escalate to engineering" (~4s, real agent).
- **Lifecycle:** `CASE_STATUS` (active→resolved). `POST /api/resolve/{case_id}` (in-case button for non-sourcing tags) + auto-resolve in the PO-approve endpoint once all a case's PO drafts are sent. `_mark_resolved` stamps a `resolved` event onto the case timeline (the audit trail). `/api/overview` rows carry a `resolved` flag (filtered out of the active list); `GET /api/resolved` lists them.
- **Frontend:** in-case **resolve-card** with the tag's labelled button → marks resolved → Resolved state. New **Resolved** nav tab + `renderResolved()` (cards) + `renderAudit(case_id)` (static timeline via `renderStaticFeed`) at `#/audit/{id}`. New `resolution`/`resolved` entries in the `PHASE` map + CSS. Resolved cases leave the dashboard. Verified with headless screenshots: case verdict+resolve, Resolved list, full audit trail (evidence → agent verdict → operator resolution).
- **Design principle honored:** deterministic where it must be defensible (the verdict a judge can re-check), live LLM where there's judgment (interpretation, negotiation, call) — one system, responsible for all tags, every case genuinely runs an agent on open, nothing hardcoded.
- **TODO #4 (cosmetic, not built):** dashboard triage **reveal animation** — cases flip from "incoming" to their tag on load (deterministic logic shown as a step; NOT a live 30-case LLM burst, which would reopen the 429).

### Branding, OEM name & the quota fix (2026-10-14)
- **OEM renamed `Veltrix Motors` → `Isar Automobile`** across all data (`cases.json`, `suppliers.json`, `production_lots.json`, `remedy_campaigns.json`, both top-level and `input/`). Chosen with the user: a fictional, Bavarian-flavoured name (Isar = Munich's river) that evokes the real BMW case **without impersonating BMW** — the user explicitly rejected "Bavaria Motor Works" as too close (it's literally BMW's translation). Do NOT use the literal "BMW" on generated POs/emails (would be forging a real company's documents).
- **PO de-branded → it's the carmaker's own PO.** Removed all "Remedy Mesh" branding from `purchase_order.py`: header is now `{OEM}` + "Purchase Order — recall remedy procurement", buyer line "Recall procurement" (was "via Remedy Mesh"), footer "authorized only after an operator at {OEM} reviews and approves it". Remedy Mesh is the behind-the-scenes platform; the OEM issues the PO.
- **Call identifies on behalf of the OEM.** `voice.offer_script` now says "this is the procurement desk calling **on behalf of {OEM}**…" (was "This is Remedy Mesh calling"). OEM name threaded through `call_supplier`/`confirmation_twiml` and `PENDING_CALLS["oem_name"]` in `server.py`.
- **Confirmation call will be AUTONOMOUS (no button) — decided, not yet wired in UI.** The agent places the call itself as the final step; the human-clicked button was dropped (it contradicted the autonomy pitch). Timing is controlled by when the case is opened.
- **Quota fix — Pro removed from the hot path.** `OEMCounterAgent` switched `gemini-2.5-pro` → `gemini-2.5-flash` (supplier agent was already Flash); both now run with **thinking disabled** (`_FAST_CFG`, `thinking_budget=0`). A full `use_llm=true` run is now all-Flash, whose per-minute quota is far higher, so the 429 that crashed live runs is gone. Verified: live CASE-001 run completed with **0 rate-limit retries** (was crashing). Shared dispatch pacing (`_throttle`, `REMEDY_LLM_PACE_SECONDS`) kept as a safety net, loosened 6.5→1.5s now that it's Flash-only. This replaces the earlier "stagger" idea — switching models was the cleaner fix.

- **Vertex Pro quota ceiling (RESOLVED — see above; was):** a full `use_llm=true` run bursts ~8 concurrent `gemini-2.5-pro` calls (2 L1-labels are deterministic; 3 supplier offers + 3 OEM counters are Pro) → `429 RESOURCE_EXHAUSTED` on this new project. Deterministic path (`use_llm=false`) is unaffected. **Fix before finale:** stagger Layer-2 agent calls (sequential / small delay) and/or request a Vertex quota increase. Backoff retry exists but can't help within the same exhausted minute.

---

## 5. Layer 1 — what's necessary, what we can add

### 5.1 Necessary (core — must have)
| Component | Role | Status |
|---|---|---|
| Evidence Interpreter (Gemini Flash) | Raw complaint narratives → structured claims | Built |
| Deterministic Match (Python tools) | VIN/lot range, before/after-repair date logic, dedup, post-remedy rate vs baseline, geo cluster | Built + verified |
| Resolution Loop | Obtain the missing lot record (dealer lookup / Places) → flips pending→confirmed | Built (logic) |
| Verification Agent (Gemini Pro) | Independent audit vs `policy_rules` evidence bar | Built |
| Decision gate | confirm → trigger Layer 2 / else monitor / no-signal / keep-open | Built (logic) |

### 5.2 Can add — ranked by payoff vs effort
| Addition | What it buys | Effort | Verdict |
|---|---|---|---|
| **Streaming activity feed** (SSE from Cloud Run) | The "agent visibly working" moment — timestamps prove it | Low | **Do** |
| **Structured output** (Gemini controlled generation / JSON schema) | Agents return schema-valid JSON every time → reliability | Low | **Do** |
| **Part 573 memo auto-draft** | On confirm, generate the regulatory amendment memo | Low | **Do** (strong, cheap) |
| **Cross-OEM signature match** (embeddings + Vertex Vector Search) | Find the same defect across manufacturers — novel, hard to fake | Med | **Strong flex** — add if time |
| **Multimodal intake** (Gemini reads a supplier recall-notice PDF/image) | "It reads the actual recall letter" demo beat | Med | Nice flex |
| **Geo-cluster independent trigger** | A geographic cluster alone can open a case (2nd trigger) | Low | Already in logic — surface in UI |
| **BigQuery threshold analytics** | Run the rate/cluster math as SQL at scale | Med | Optional |
| Real NHTSA data grounding | Swap synthetic hero for live VOQ feed | Med-High | Risky for demo; optional |

### 5.3 Explicitly NOT changing in Layer 1
- No human escalation call (moved out).
- Keep it one in-process SequentialAgent (not distributed).

---

## 6. Build status (one-by-one)
- [x] Step 0 — Input corpus (no hardcoding) + validator + golden outputs
- [x] Step 1 — `remedy_mesh/` package: data layer, deterministic logic, ADK tools (verified)
- [x] Step 2 — Layer 1 agent tree (`layer1.py`), `agent.py` entrypoint, `run_layer1.py` runner (constructs cleanly on ADK 2.11.0)
- [x] Database — Firestore pluggable backend (`data.py`) + `scripts/seed_firestore.py`
- [x] Architecture diagrams — `architecture.drawio` (logic), `architecture_gcp.drawio` / `_icons.drawio` (services, project id scrubbed)
- [x] Reconciliation — `activity_log.json` rewritten to data-lookup; diagrams scrubbed; isolation decided (Tier 2)
- [~] **Step 3 — Layer 2 (CommitMesh, Tier 2)** — in progress:
  - [x] 3a — `negotiation.py`: deterministic allocation + append-only commitment ledger + selective re-negotiation (verified via `validate_layer2.py`: A+B meet 60k; break SUP_A → only that row moves → SUP_C backfill 17,200 + shortfall 15,800 escalated)
  - [x] 3b — scoped Tier-2 tools (`get_my_profile` / `get_public_demand`, session-scoped) + `SupplierOfferAgent` (Pro); constructs on ADK 2.11.0
  - [x] 3c — cost-blind orchestrator `run_negotiation()` (one isolated scoped session per supplier) + **OEM counter-round as a real `OEMCounterAgent` (LlmAgent)**: reasons a DIFFERENT counter per supplier (scoped — never sees another supplier's bid), suppliers revise toward (never below) their own cost. Live: OEM countered A→$127.5, B→$125, C→$122; A & B accept, C holds at its $131 floor; final $7.58M (beats the flat-ceiling rule by ~$103K). Two-sided agent negotiation.
  - [x] 3e — **first LIVE Gemini run on Vertex AI** (us-central1, ADC). Layer 1: Interpreter→Investigator (chased record)→Verifier → `confirmed_trigger_mesh`. Layer 2 live: suppliers opened $142/$147/$157 → after $128 counter → A & B accept $128, SUP_C holds at its $131 cost floor (left out) → 60k met under ceiling. **Proves scoped isolation produces real per-supplier bargaining.**
  - [x] 3d — `voice.py` Twilio supplier-confirmation call (stub-safe: simulates w/o creds, real outbound call w/ `TWILIO_*` env + `confirmation_twiml` webhook helper) wired into the orchestrator; `break_commitment_and_resolve()` selective re-negotiation (break one → re-solicit others → repair only that row → SUP_C backfill 17,200 + shortfall 15,800 escalated; SUP_B row untouched). Verified end-to-end.
- [x] **Step 3 — Layer 2 (CommitMesh) COMPLETE** (3a–3e)
- [x] Step 4 — Gate wired (`pipeline.py`): Layer 1 → on confirm → Layer 2, emitting a **unified timeline** (`timeline.py`) — agent actions + negotiation + call transcript + ledger, one ordered feed for the UI
- [x] Backend + voice webhook (`server.py`): demo API (`/api/cases`, `/api/run`, `/api/timeline`, `/api/call`, `/api/break`) + Twilio webhook (`/voice/{id}` TwiML with speech+dtmf capture → supplier's **SpeechResult transcript appended to the timeline**). Verified via TestClient. Real live transcript needs the public Cloud Run URL (Step 6).
- [ ] (prev Step 4) — superseded by the above
- [ ] Step 5 — Enable APIs + seed Firestore + first live run (needs AI Studio key OR ADC)
- [ ] Step 6 — Deploy to Cloud Run
- [ ] Step 7 — Frontend (4 screens) on Firebase Hosting

---

## 7. Open questions / risks
- **Biggest risk:** live demo execution (phone call + agent flow on stage) — mitigate with robust (keypad/speech) call + recorded backup.
- Credential for first live run: **AI Studio free key** (fast, no billing) vs **Vertex** (billing on; needs ADC). Decide before Step 5.
- Twilio trial can only call **verified** numbers — fine for demo.
- Free-trial $300 may not apply (billing account was previously closed) — budget alert set as protection.
- Screen-2 copy in the brief/deck still narrates a Layer-1 phone call — update to the data-lookup sequence to match `activity_log.json`.

---

## 8. Submission requirements (AI Builder Cup) — checklist
- [ ] Working **deployed link** (Cloud Run / Firebase)
- [ ] **Video demo < 3 min**
- [ ] **Public GitHub repo** (no project id / personal email committed)
- [ ] **Deck**
- Must be on **Google Cloud stack** (✓ ADK + Gemini + Firestore + Cloud Run). Gen-AI Implementation = 40% of score.
