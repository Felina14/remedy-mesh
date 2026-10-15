# Remedy Mesh — Build Log & App Reference

> A single living log of **what the app is** and **every change we've made**, newest first.
> Companion to `README.md` (the pitch), `REMEDY_MESH_OVERVIEW.md` (the deep tour) and
> `DESIGN_DECISIONS.md` (the why). This file is the running record.
>
> Last updated: **2026-10-15**
> Built for the **Google AI Builder Cup 2026** (JAPAC).
> This file is secret-free by design — no tokens, keys, project IDs or personal emails ever go here.

---

## Part 1 — What Remedy Mesh is (the whole app in one place)

### 1.1 The idea
A car recall gets **closed** once the fix is on the market. But vehicles built *after* the
recall's cutoff date can carry the **same defect** and never get recalled — the recall's
scope has **drifted** and nobody notices until failures pile up in the field.

**Remedy Mesh is a multi-agent system that watches closed recalls against live field
evidence, proves when scope has drifted (it never guesses), and when a signal is confirmed
it does two things a human would otherwise do by hand:**

1. **Procures the replacement parts** — negotiates a split across competing suppliers,
   confirms each deal on a **real phone call**, and drafts the purchase orders.
2. **Amends the recall** — drafts the **NHTSA Part 573** recall-amendment memo that adds the
   newly-implicated vehicles to the campaign.

Both outputs are **drafts**. A human reviews and approves every real-world action before
anything is sent, called, or filed. **That human-in-the-loop checkpoint is the whole point.**

### 1.2 The two agent layers
- **Layer 1 — Evidence Verification ("Remedy Watchdog").** From real records only, decides
  whether a closed/remedied case needs reopening. Outputs one of a fixed set of decisions
  (see §1.6). Never confirms without a checkable record.
- **Layer 2 — Verified Negotiation Mesh ("CommitMesh").** Fires only on a confirmed signal.
  Secures the replacement-part capacity the expanded recall now needs by **negotiating with
  supplier agents, confirming the order on a call, and recording verifiable commitments.**

Design principle throughout: **never confirm or commit without a real, checkable record.**

### 1.3 Stack
| Concern | Choice |
|---|---|
| Agent framework | **Google ADK** (`google-adk` 2.11.0), `LlmAgent` |
| Model | **Gemini 2.5 Flash** on **Vertex AI** (ADC, `GOOGLE_GENAI_USE_VERTEXAI=TRUE`) |
| Backend | **FastAPI** + **Uvicorn**, **SSE** for live streaming to the UI |
| Frontend | Vanilla **HTML/CSS/JS** single-page app (`frontend/index.html`) |
| Deploy | **Google Cloud Run** — single container, one-command `gcloud run deploy --source .` |
| Voice | **Twilio** — real supplier confirmation calls |
| Email | **Gmail** — real PO / Part-573 draft delivery |
| State | In-memory per instance (demo-grade; min=max=1 instance) |

### 1.4 Module map (`remedy_mesh_app/remedy_mesh/`)
| File | Role |
|---|---|
| `server.py` | FastAPI app, routes, SSE, orchestration, auth wiring, call-outcome finalize |
| `auth.py` | Two-operator maker-checker auth (stateless HMAC tokens — see §2.9) |
| `agent.py`, `agent_runner.py` | ADK agent construction + run helpers |
| `layer1.py` | Evidence-verification agents & decisions |
| `layer2.py` | Negotiation mesh, re-source/backfill, resource narration |
| `negotiation.py` | Supplier-agent negotiation rounds |
| `suppliers` data (`remedy_mesh_synthetic_data/input/suppliers.json`) | OEM + 3 supplier parties |
| `callunderstanding.py`, `voice.py` | Phone-call understanding + Twilio voice |
| `resolution.py` | Case-resolution logic (maker-checker proposals) |
| `part573.py` | NHTSA Part 573 amendment memo draft |
| `purchase_order.py`, `email_po.py` | PO drafting + Gmail delivery |
| `pipeline.py`, `logic.py`, `timeline.py`, `tools.py`, `data.py`, `config.py` | Pipeline glue, domain logic, timeline, tools, data access, config |

### 1.5 The two operators (maker-checker)
- **Eleanor Ashworth — Analyst.** Investigates cases, proposes resolutions.
- **James Cole — Approver.** Reviews and approves/returns POs, escalations, and resolution
  proposals. Demo accounts; passwords are intentionally public for the demo.

Every consequential action an Analyst takes becomes a **proposal** (a notification) that the
Approver must approve before it takes effect.

### 1.6 The 9 synthetic demo cases (all Isar Automobile, campaign 24V-104)
| Case | Decision |
|---|---|
| CASE-001 | `confirmed_trigger_mesh` — **hero case** (full negotiate → call → decline → re-source → confirm → PO + 573) |
| CASE-020 | `confirmed_trigger_mesh` — second confirmed; Resolution-Loop showcase |
| CASE-015 | `chase_record` |
| CASE-009 | `remedy_quality_flag` — "Escalate to engineering" (maker-checker resolution demo) |
| CASE-006 | `monitor` |
| CASE-022 | `keep_open` |
| CASE-005 | `insufficient_evidence` |
| CASE-002, CASE-003 | `no_signal` |

Procurement parameters: cost ceiling **$128**, target **60,000 units**, **12 weeks**.

Suppliers (all fictional):
| Party | Cost / unit | Capacity / week |
|---|---|---|
| SUP_A — Haldane Components | $118.50 | 3,200 |
| SUP_B — Correns Manufacturing | **$120.00** | 2,600 |
| SUP_C — Ridgeline Precision | $131.00 | **2,500** |
| OEM — Isar Automobile | ceiling $128 (private) | — |

### 1.7 How negotiation actually works (it is genuinely live)
Each supplier is a **separate Gemini agent** holding its **own private cost data** (Tier-2
isolation — no reasoning step ever sees two suppliers' numbers, and the orchestrator never
holds raw cost). A supplier's accept decision is simply *"is the OEM's counter ≥ my cost?"*
Runs are genuinely non-deterministic: the same case run twice produces different opening
offers and completion order. The negotiation **settles a price (a quote, not a binding
order)**; the **phone call is the human confirmation** before a PO, and a **decline models
operational reality** (delivery window / capacity backlog) that the quote didn't capture.

> Honest framing (kept, by decision): the supplier agents are **simulated by the system** —
> they stand in for the real suppliers' sales desks. The product is the **buyer-side agent**;
> the real supplier touchpoint is the **phone call**. This maps to a real competitive
> multi-supplier RFQ / sealed-bid / split-award process. No "simulated" disclaimer labels are
> added to the UI — the README already states plainly that suppliers/VINs are fictional and
> the corpus is synthetic.

### 1.8 Two runtime modes
- **Cloud Run = REAL / LIVE.** Nothing is scripted. Real Twilio calls, real LLM negotiation,
  real re-sourcing. This is the honest, submission-grade environment.
- **Local = demo-call mode** (`REMEDY_DEMO_CALL=1`). **Only the phone-call audio/transcript is
  scripted**, and only so the video can be recorded without live speaking. Negotiation,
  decline handling, re-sourcing and confirmation all still run **genuinely live** via the LLM.

---

## Part 2 — Change log (this session, newest first)

All changes below were deployed to Cloud Run and verified in a headless browser unless noted.

### 2.9 Stateless auth tokens — fixes the "escalate → sign-in bounce" permanently
**Files:** `remedy_mesh_app/remedy_mesh/auth.py` (rewritten)
A token is now a stable `hmac(secret, username, sha256)[:32]` — deterministic and identical
before and after any restart or redeploy. `current_session` validates statelessly by matching
the presented token against each user's stable token; the old in-memory `SESSIONS` table is gone.
**Why it mattered:** the old session table was wiped on every backend restart / Cloud Run
redeploy, silently invalidating everyone's login. The next action (e.g. Escalate) hit a 401,
and the frontend's `if(status===401||403){ signOut() }` guard — which runs *before* the
notification UI — bounced the user to sign-in. It looked like "the notification code isn't
written," but the notification code was fine; auth was failing in front of it.
**Verified:** a token minted before a restart returns 200 after it (was 401); a bogus token → 401.
**One-time note:** after this change the user must sign in **once more** (their browser holds a
pre-stateless token that bounces one last time); every login after that survives all restarts.

### 2.8 Transcript collapses on case reopen; "View transcript" drawer
**Files:** `frontend/index.html` (`appendCallEvent` rewrite + new `openTranscriptDrawer`)
On replay of an already-run case, each call now collapses into one summary row
(`Confirmation call · [supplier] — confirmed/declined` + a **View transcript** button) instead
of dumping inline transcript bubbles below the status. Turns are grouped by supplier; the
button opens a drawer showing the full transcript.
**Verified:** 3 calls → 3 collapsed rows, 0 inline bubbles, drawer renders the turns.

### 2.7 SUP_B reliably wins its slot (negotiation tuning)
**Files:** `remedy_mesh_synthetic_data/input/suppliers.json` (SUP_B cost 124.75 → **120.00**)
Root cause of "no SUP_B call": SUP_B's thin margin at the $128 ceiling made the live LLM
*sometimes* refuse $128, which pushed SUP_B over ceiling and let SUP_C take its slot — so there
was no SUP_B to call. Widening the margin ($3.25 → $8) makes SUP_B accept reliably.
**Not a local-only bug** — same code on cloud; cloud simply hadn't hit the unlucky run.
**Verified:** 3 live runs all allocated SUP_A + SUP_B.

### 2.6 Persona pill + proper sign-out
**Files:** `frontend/index.html` (`renderWho` rebuilt)
The persona name was `#c7cad3` on a translucent-white top bar — invisible. Rebuilt theme-aware
as a `.whopill` (`var(--ink)` on `var(--panel-2)`) with a caret; clicking opens a `.whomenu`
dropdown showing the identity and a **Sign out** item. Removed the old two-arrows switch-user
glyph. Global click/Escape handlers close the menu. Name contrast now **13.52:1**.
**Verified** via screenshot + contrast check.

### 2.5 Separate re-negotiation panel
**Files:** `frontend/index.html`
Added `<div id="renegMount">` **before** `#allocMount`. Re-sourcing after a decline now renders
into its own panel (`renderRenegPanel`) rather than inside the allocation box. The allocation
table shows the declined supplier as a struck-through `.declined-row` above the backfill row.
**Verified** via screenshot.

### 2.4 Maker-checker resolution (propose → approve/return)
**Files:** `remedy_mesh_app/remedy_mesh/server.py` + `frontend/index.html`
Analyst resolution actions now **propose** (create a `resolution_proposal` notification, case
stays open) instead of resolving directly. `api_resolve` creates an idempotent notification
(one pending per case). The Approver's `/approve` marks the case resolved; `/reject` returns it
(case stays open, re-propose allowed). New state `BACKFILL_NOTES`. Frontend got the notification
icon/type, an Approve/Return footer, `approveResolution()`, and a pending-state render on reload.
**Verified:** propose → approve → resolved; return path; role enforcement (403 both ways).

### 2.3 Genuine re-source after a decline (real LLM, both environments)
**Files:** `remedy_mesh_app/remedy_mesh/server.py` (`_finalize_call_outcome` decline branch),
`remedy_mesh_app/remedy_mesh/layer2.py`
A decline now triggers `break_commitment_and_resolve(..., use_llm=True)` and
`narrate_resource(ctx, use_llm=True)` — a **real ~13s LLM re-negotiation** in both cloud and
local (previously deterministic/instant, which read as "hardcoded"). Added a `ResolutionAgent`
`LlmAgent`, `narrate_resource()` and `_fallback_resource_text()`. The result carries
`reserve_supplier` and `reserve_price`, which drive the re-negotiation panel.

### 2.2 Clean decline → backfill (no false shortfall/escalation)
**Files:** `remedy_mesh_app/remedy_mesh/layer2.py` (`break_commitment_and_resolve`)
The reserve pool now **excludes already-committed suppliers** (falls back to all-others if
none remain). SUP_C capacity raised 1,500 → **2,500/wk** so a single reserve covers the full
27,000-unit backfill. The happy-path decline no longer produces a spurious shortfall or
escalation.

### 2.1 Text-contrast fix (invisible UI text)
**Files:** `frontend/index.html`
Five spots used `color:var(--mut)` where `--mut` is a near-white/near-black wash token, so the
text was invisible. Repointed to readable ink tokens — `.mstep .rtn` → `--ink-2` (7.25:1),
`.mlane-hd small` / `.fline.sysnote .fhead b` / `.auth-input::placeholder` / `.auth-demo .dl`
→ `--ink-3`. Theme-aware; fixes both light and dark mode. Resolved the reported "wordings not
visible below the OEM encounter."

---

## Part 3 — Operating notes (how we run & reset)

### 3.1 Local (demo-call mode, for recording)
- Backend on `127.0.0.1:8080`, frontend on `localhost:5500`.
- Start: kill port 8080, source the local env file, `export REMEDY_DEMO_CALL=1`, then
  `.venv/bin/python -m uvicorn remedy_mesh.server:app --host 127.0.0.1 --port 8080`.
- Uses the project venv (`remedy_mesh_app/.venv`, which has `google.adk` 2.11.0). Do **not** use
  the global pyenv python — it lacks ADK.
- `REMEDY_DEMO_CALL=1` scripts **only** the call audio/transcript; everything else is live.

### 3.2 Cloud Run (live / submission)
- Service `remedy-mesh`, region `us-central1`, allow-unauthenticated, min=max=1 instance,
  cpu=1, memory=1Gi, in-memory state.
- Deploy: `gcloud run deploy remedy-mesh --source . --region us-central1 ... --env-vars-file <env.yaml> --quiet`.
  The env file does **not** set `REMEDY_DEMO_CALL`, so cloud stays fully live.
- Reset to a fresh container (pristine state):
  `gcloud run services update remedy-mesh --region=us-central1 --update-env-vars _RESET=$(date +%s) --quiet`.
- Pristine check (either environment): **active = 9, resolved = 0, notifications = 0.**

### 3.3 Secrets & repo hygiene (must persist)
- Twilio / Gmail credentials and the GCP project ID live in **process env only** — never
  committed, never written to any doc or log (including this one).
- The repo stays **PRIVATE** until the user explicitly asks to publish it
  (`gh repo edit <repo> --visibility public`).

---

## Part 4 — Status

- **Cloud Run** — all nine fixes above deployed and verified; pristine.
- **Local** — demo-call mode, pristine.
- **Not started:** pitch deck; demo-video recording.
- **Repo:** private, unchanged visibility.

<!-- Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com> -->
