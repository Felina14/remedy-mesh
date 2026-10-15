# Remedy Mesh Hybrid — Build Log

> The complete record of the **hybrid** version of Remedy Mesh: what it is, how it runs end to
> end, every change made to build it, how each was verified, what it costs, and what is still open.
>
> Last updated: **2026-10-15** · Google AI Builder Cup 2026 (JAPAC)
> Folder: `RemedyMesh-Hybrid/` (a copy of the original app; the original folder and its Cloud
> Run service were deliberately left untouched as the fallback).
> Secret-free by design: no project IDs, account emails, phone numbers, tokens or resource IDs
> appear here. Where one is needed it is written as `<project-id>` or similar.

---

## 1. What the hybrid is

Remedy Mesh watches closed car recalls against field evidence, proves when a recall's scope has
drifted, and then sources the replacement parts and drafts the NHTSA Part 573 amendment, with a
person approving every real-world action. (Full product description: `CHANGELOG.md` Part 1 and
`REMEDY_MESH_OVERVIEW.md`.)

The **hybrid** keeps that product identical and changes *where the agents run*:

| | Original app | Hybrid |
|---|---|---|
| Where agents run | Inside the Cloud Run container, as library calls | **8 separate managed services on Vertex AI Agent Engine**, called from Cloud Run |
| Layer-1 agents | Defined but **never executed**; timeline labels shown anyway | **Actually run**, chained Interpreter → Investigator → Verifier |
| Case data | Bundled JSON files | **Firestore** (read-only), same data |
| Isolation | Supplier vs OEM separated by code convention | Supplier and OEM agents are **separate services** |
| Cloud Run service | `remedy-mesh` | `remedy-mesh-hybrid` (own service account) |

**The headline:** Cloud Run runs the app and the orchestrator; each agent is its own managed
service; verdicts and allocations stay in deterministic code; agents read case data from
Firestore; a person approves everything that leaves the system.

Architecture diagram: `docs/remedy-mesh-architecture.png` (slides) and
`docs/remedy-mesh-architecture.html` (source).

---

## 2. Architecture, end to end

### 2.1 Components

| Component | Role |
|---|---|
| **Cloud Run** `remedy-mesh-hybrid` | FastAPI orchestrator in one container: single-page app, REST + live SSE feed, maker-checker sign-in, deterministic verdict gate (`evaluate_case`), negotiation core (allocation + ledger + re-source), agent connector, Twilio voice webhook, PO + Part 573 PDF drafting, approval queue |
| **Vertex AI Agent Engine** | 8 Google ADK `LlmAgent`s, each deployed as its own managed resource |
| **Gemini 2.5** on Vertex AI | Flash for most agents; Pro for the Investigator and Verifier |
| **Firestore** (Native, us-central1) | The case database: read by the app and by 4 agents |
| **Twilio Voice** | Real outbound confirmation calls; spoken replies come back to the webhook |
| **Gmail SMTP** | Sends POs and files the Part 573 amendment, only after the Approver approves |
| **People** | Eleanor (Analyst) runs cases and places calls; James (Approver) approves, files and decides |

### 2.2 The 8 agents

| Agent | Model | Reads Firestore | What it gets per turn |
|---|---|---|---|
| Evidence Interpreter (L1) | Flash | yes — `cases`, `complaints` | the case ID |
| Remedy Investigator (L1) | Pro | yes — cases, complaints, lots, campaigns, policy, dealers | the Interpreter's `claims` + the app's obtained lot records |
| Verification Agent (L1) | Pro | yes — policy + case records | the Investigator's `investigation` + the app's obtained lot records |
| Resolution Agent (L1) | Flash | no | the fixed verdict, metrics and criteria (in the prompt) |
| Call Understanding Agent | Flash | no | the order, the call so far, the supplier's latest spoken reply |
| Supplier Offer Agent (L2) | Flash | yes — `suppliers`, **only its own party** | `supplier_id` + public demand (one scoped run per supplier) |
| OEM Counter Agent (L2) | Flash | no | one supplier's offer + a **count** of alternatives (never a rival's bid) |
| Re-source Narration Agent (L2) | Flash | no | the computed backfill facts |

There is **one** Supplier Offer Agent. SUP_A, SUP_B and SUP_C are three scoped runs of it, each
with its own session state. That is the original Tier-2 design, unchanged.

Every agent turn gets a fresh, short-lived session. **No Memory Bank** is used, by decision:
cross-run memory would weaken the supplier isolation guarantee.

### 2.3 One confirmed case, step by step

1. Eleanor opens a case; the run streams to her screen over SSE.
2. **Layer 1 on Agent Engine:** Interpreter → Investigator → Verifier, each reading the case
   records from Firestore and passing its output to the next.
3. **Verdict gate:** deterministic code checks the six evidence criteria. The Verifier audits that
   verdict independently; any disagreement is shown and flagged for a person, never resolved silently.
4. **Layer 2:** the Supplier Offer Agent runs once per supplier; the OEM Counter Agent counters
   each offer; deterministic code allocates the split award under the $128 ceiling.
5. **Confirmation call:** Twilio rings the supplier; the Call Understanding Agent reads every reply.
6. **Decline:** the reserve supplier is re-quoted, countered, and re-committed; the Re-source
   Narration Agent explains the choice; James gets a decline notice to acknowledge.
7. **Drafts:** POs and the Part 573 amendment land in James's queue.
8. **Approval:** James approves; only then is the email sent or the amendment filed.

### 2.4 What Firestore does (and does not do)

- **Read-only.** `data.py` is the only file that talks to Firestore and contains no writes. The
  only writer is the one-off `scripts/seed_firestore.py`.
- **Holds the case input data:** 9 cases, 503 complaints, 30 production lots, 7 recall campaigns,
  4 parties (OEM + 3 suppliers), 50 dealers, plus `config/policy_rules`. 603 records in all.
- **Does not store the workflow.** Timelines, negotiations, the ledger, call transcripts,
  notifications and approvals stay in memory, which is why a redeploy resets to pristine.
- Most collections are read once and cached per process; complaints are queried live per case.
- **Decision (option 1):** keep Firestore as-is for the deadline and describe it accurately as
  *"agents read the case data from Firestore"*. Do not claim it stores the workflow.
- Verified identical: all 9 verdicts match the JSON backend exactly.

---

## 3. Change log (newest first)

All changes are in `RemedyMesh-Hybrid/` only, deployed to `remedy-mesh-hybrid` and verified unless noted.

### 3.14 OEM Counter Agent never counters above its ceiling
**File:** `layer2.py` (instructions only)
The "counter softer" clause said *"closer to their price, but still aiming at your ceiling"*,
which let the agent offer above its own ceiling: once it countered SUP_B at $130 "to leave room
for negotiation" when SUP_B would have accepted $128, flagging the hero case as over ceiling.
The clause now says to counter *at* the ceiling when leverage is weak, and states that the counter
is the price the OEM offers and the ceiling is the most it will pay: never counter above it. If a
supplier cannot meet the ceiling, its revised offer shows that and the allocation handles it.
The original app has the old wording.
**Verified:** 0 of 12 counters above $128 in-process across four leverage situations
(several / one / no alternatives, offer far above the ceiling), and 0 of 12 on the deployed
agent after updating it in place.

### 3.15 Saved to git
The hybrid folder is now a working copy of the private repository on branch **`agent-engine`**
(based on `main`). Agent resource maps (`agent_engine_lab/deployed*.json`) are git-ignored because
they contain project-identifying names; the old project ID in the seed script was scrubbed.

### 3.13 Firestore as the case database (read path)
**Files:** `agent_engine_lab/update_agents.py`, `remedy_mesh/remote_agents.py`, cloud env.
- Enabled the Firestore API, created a Native database in us-central1, seeded 603 records + policy.
- The hybrid Cloud Run service now runs under its **own service account** with only
  `roles/aiplatform.user` and `roles/datastore.user`, so the original service's account was not widened.
- Granted `roles/datastore.user` to the Agent Engine service agent so the deployed agents can read.
- Updated the 4 data-reading agents in place with `REMEDY_BACKEND=firestore` and the Firestore client.
- `/api/agent_engines` now also reports `data_backend`.
**Verified:** parity on all 9 cases (identical decision, criteria, metrics); CASE-001 ran fully on
Firestore in 78 s with every agent call OK; SUP_B accepted a $128 counter 6/6 reading from Firestore.

### 3.12 Escalations get a real Approver decision
**Files:** `server.py`, `frontend/index.html`
An escalation (a re-source that cannot cover the whole gap) used to sit in James's queue with no
button. Now:
- It carries its real numbers: units needed, covered by the reserve, uncovered, declined supplier.
- James chooses **Accept partial cover** (covered units go ahead; the rest stays an open sourcing
  gap) or **Return to Analyst** (case stays open to source the rest).
- Either decision is written to the case's audit trail and shown on Eleanor's case page.
- Escalation text now uses the supplier's name, not its internal ID.
**Verified:** two real escalations created by the live decline → re-source flow on a *test copy* of
the data (Ridgeline capacity reduced, scratch folder only): CASE-001 accepted, CASE-020 returned;
Eleanor's pages show each decision; Analyst → 403; a returned escalation cannot then be accepted;
no JS errors. Escalations do not occur in the normal demo data.

### 3.11 Decline notices are acknowledged by the Approver
**Files:** `server.py` (new `POST /api/notifications/{id}/acknowledge`), `frontend/index.html`
A supplier decline used to sit under "Needs your action" with no button and a stale
"re-sourcing the gap" line. Now:
- After the live re-source the notice reads what actually happened, e.g. *"Re-sourced to
  Ridgeline Precision @ $131.00 — that supplier still needs its own confirmation call."*
- The drawer shows the Re-source Narration Agent's reasoning above the call transcript.
- **Acknowledge decline** (Approver only) records who and when, and moves it to "Sent & cleared".
**Verified:** browser test — badge 2 → 1, card cleared, no JS errors; Analyst → 403, no login → 401,
repeat click is a no-op, only decline notices can be acknowledged.

### 3.10 Evidence Interpreter summary renders as text
**File:** `pipeline.py` (`_readable`)
The Interpreter sometimes returns its summary as a nested object, which showed on screen as raw
`{'suspected_failure': …}`. It is now rendered as plain sentences.

### 3.9 Phone agent reads replies correctly (instructions only)
**File:** `callunderstanding.py`
The deployed phone agent read a clean "yes" as *conditional* 7 times in 8. Rewrote its
instructions as a precise definition of each outcome (confirmed / declined / partial / counter /
conditional), including "a yes without the delivery window is still confirmed — confirm the window
on the record", and "agreed_price is the price they name if they counter". Temperature set to 0.
- A code-level price override was briefly added and **removed at the user's direction: nothing
  hardcoded; the live agent alone decides.**
**Verified:** 23/24 in-process; **12/12 on the deployed agent**; then live on a real call.

### 3.8 Re-sourcing negotiates instead of paying the opening price
**File:** `layer2.py` (`break_commitment_and_resolve`)
After a decline, the reserve's **opening** quote was accepted with no counter, so Ridgeline was
paid $157.20 seconds after accepting $131 in the main round. Added one counter round, exactly like
the main negotiation: an over-ceiling reserve quote is countered by the OEM agent and the supplier
revises, never below its own cost. Result carries `reserve_opening_price` too.
**Verified:** $157.20 → countered → **$131.00**, live, including from a real phone decline.
*This weakness also exists in the original app.*

### 3.7 Verifier and Investigator audit the same records as the app
**Files:** `tools.py` (`_evaluate_case_scoped`), `pipeline.py`
Each Agent Engine service has its own memory, so a lot record the app had already chased was
invisible to the Verifier: on CASE-015 (the Resolution-Loop showcase) it wrongly reported "not
confirmed — missing install_date_confirmed". The orchestrator now passes its obtained lot records
in the session state and the evaluation tool syncs to them before evaluating.
**Verified:** CASE-015 Verifier → confirmed, missing none.

### 3.6 The Layer-1 agents actually run
**File:** `pipeline.py`
In the original app the timeline shows "EvidenceInterpreter" and "VerificationAgent" but no agent
runs there (a hardcoded label, and the Resolution Agent's text under the Verifier's name). The
hybrid runs the real chain on Agent Engine:
- Interpreter → its summary is the timeline line.
- Investigator → "ran the deterministic evaluation (after chasing the missing lot record, only if
  the app really chased one) — reports …".
- Verifier → independent audit with missing criteria; if it disagrees with the deterministic
  verdict, the line says so and the case is flagged for a person (the deterministic verdict stands).
- The verdict explanation is labelled **ResolutionAgent**, the agent that actually writes it.
Each line carries `served_by: agent_engine` in its metadata.

### 3.5 The app calls the deployed agents
**Files:** new `remedy_mesh/remote_agents.py`; `agent_runner.py`; `layer2.py`; `resolution.py`;
`callunderstanding.py`; `server.py`; `requirements.txt`
- `remote_agents.py`: maps each in-process agent object (by identity — two share the name
  "ResolutionAgent") to its deployed resource; opens one session per turn with the scoped state;
  records latency per call. Switched on by `REMEDY_AGENT_ENGINE=1`; the map comes from
  `REMEDY_AGENT_ENGINES` (JSON) on Cloud Run, or `agent_engine_lab/deployed_all.json` locally.
- `agent_runner.run_agent_text` routes to the deployed agent when available, else runs in-process.
- Supplier and OEM negotiation calls go through the same helper with the same session state, so
  isolation is identical either way.
- Turn budgets raised in remote mode (resolution and re-source 20 s; phone 10 s, inside Twilio's
  ~15 s window) so the real agent answers instead of the run quietly using fallback text.
- Startup warm-up opens every agent handle in the background (cold first call ~8 s → warm ~2–5 s).
- `GET /api/agent_engines`: which agents are attached, the data backend, recent call latencies.
- `requirements.txt` adds `google-cloud-aiplatform[agent_engines]==2.5.0`.

### 3.4 Frontend talks to its own backend
**File:** `frontend/index.html`
On localhost the frontend always called port 8080, so a hybrid backend on another port would have
silently used the original backend. Now only the separate `:5500` static server uses 8080; a page
served by a backend uses its own origin. No effect on Cloud Run.

### 3.3 All 8 real agents deployed to Agent Engine
**Files:** `agent_engine_lab/deploy_all.py`, `agent_engine_lab/update_agents.py`, `remedy_mesh/data.py`, `remedy_mesh/_corpus/`
- Deploys the **real** agents imported from the codebase (not rewrites), each as its own resource,
  via `agent_engines.create(AdkApp(agent=…, enable_tracing=True), extra_packages=["remedy_mesh"])`.
- The package ships a bundled copy of the corpus (`_corpus/`); `data.py` resolves data as:
  `REMEDY_DATA_DIR` → repo corpus → bundled `_corpus`.
- `update_agents.py` updates agents **in place** (same resource names) from this copy's code.
- Gotchas found: `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` are reserved on Agent Engine
  (passing them is rejected); the Cloud Resource Manager API had to be enabled.

### 3.2 Corrected the first experiment
The first experiment deployed two simplified agents with SUP_B's numbers baked in. That was wrong:
there is one supplier agent, run per supplier. Both were deleted and replaced by the real 8 above.
The experiment's scripts (`agents.py`, `deploy.py`, `orchestrate.py`) were removed so the
repository only shows the correct design.

### 3.1 Isolated copy
`RemedyMesh-Hybrid/` was created from the experiment copy (itself copied from the original),
without `.venv`, `.git` or caches. It is **not a git repo**, so it cannot push to the real repository.
It has its own venv.

---

## 4. Verification summary

### 4.1 All 9 cases on the hybrid cloud
Every case ran all three Layer-1 agents on Agent Engine, the Verifier agreed with the
deterministic verdict every time, and there were no errors, rate limits or fallbacks.

| Case | Verdict | Outcome | Time |
|---|---|---|---|
| CASE-001 | confirmed | negotiated SUP_A 33,000 + SUP_B 27,000 | 96 s |
| CASE-020 | chase → confirmed | record chased, then negotiated | 114 s |
| CASE-015 | chase → confirmed | record chased, then negotiated | 83 s |
| CASE-009 | remedy-quality flag | Escalate to engineering | 73 s |
| CASE-006 | monitor | re-check | 52 s |
| CASE-022 | keep open | request records | 36 s |
| CASE-005 | insufficient evidence | request records | 39 s |
| CASE-002 / 003 | no signal | dismiss | 48 s / 83 s |

### 4.2 Real phone calls (cloud, live mode)
| Call | What the supplier said | Read as | Then |
|---|---|---|---|
| SUP_B confirm | "Yeah, I can do that." | confirmed · 27,000 @ $128 (1.86 s) | PO draft for Correns |
| SUP_B decline | "No, I can't do that. Sorry." | declined · 0 (2.8 s) | live re-source: Ridgeline $157.20 → countered → $131 |
| SUP_C confirm | "Yeah, we can do that. December 3rd." | confirmed · 27,000 @ $131, kept the date (1.95 s) | PO draft for Ridgeline |

### 4.3 Approver flow (browser, cloud)
Eleanor ran CASE-009 → **Escalate to engineering** → proposal created, no sign-in bounce. James then:
approved the escalation (case resolved), **filed** the Part 573 (real email), **sent** the Ridgeline
PO (real email with PDF). CASE-001 and CASE-009 moved to Resolved; active 9 → 7; no JS errors.

---

## 5. Cost (measured from the project's own usage data)

| Item | While idle | Notes |
|---|---|---|
| Cloud Run, 2 services always-on (min 1, 1 vCPU, 1 GiB, request-based billing) | ≈ $0.86 / day | ≈ $0.43 each; nearly the whole bill |
| Agent Engine, 8 agents | $0 | billed only while working; a heavy test day used ≈ 2 vCPU-h + 67 GiB-h ≈ $0.78 |
| Gemini 2.5 Flash + Pro | $0 | heavy test day ≈ $0.65; roughly 3–5¢ per case run |
| Firestore | ≈ $0 | 603 docs, inside the free tier |
| Images + buckets | ≈ $0.12 / month | 1.7 GB of images; buckets under 11 MB |

- **Idle total ≈ $0.90/day ≈ ₹75–80/day ≈ ₹2,400/month** (at ~₹85–90 per US$).
- The billing budget alert is **₹400**, which idle running alone reaches in about 5 days.
- Twilio call charges are billed on the Twilio account, not Google Cloud.
- Biggest saving: after recording, set the service that is *not* submitted to zero always-on
  instances (halves the bill). Keep the submitted one always-on so its in-memory state holds.

---

## 6. Operating notes

### 6.1 Cloud (live mode — real calls, nothing scripted)
- Service `remedy-mesh-hybrid`, us-central1, min = max = 1, 1 vCPU, 1 GiB, own service account.
- Env (values live in the scratch env file, never in the repo): the original variables, plus
  `REMEDY_AGENT_ENGINE=1`, `REMEDY_AGENT_ENGINES=<json map>`, `REMEDY_BACKEND=firestore`.
  `REMEDY_DEMO_CALL` is **not** set.
- Deploy: `gcloud run deploy remedy-mesh-hybrid --source . --region us-central1 --allow-unauthenticated --min-instances 1 --max-instances 1 --cpu 1 --memory 1Gi --service-account <hybrid-sa> --env-vars-file <env.yaml> --quiet`
- Reset to pristine: `gcloud run services update remedy-mesh-hybrid --region=us-central1 --update-env-vars _RESET=$(date +%s) --quiet`
- Pristine check: active 9, resolved 0, notifications 0, `data_backend: firestore` at `/api/agent_engines`.

### 6.2 Agents
- Update in place after a code change (keeps resource names):
  `cd remedy_mesh_app && REMEDY_BACKEND=firestore .venv/bin/python agent_engine_lab/update_agents.py <agent_key> …`
  (run with `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_GENAI_USE_VERTEXAI` exported).
- Only the 4 data-reading agents need `REMEDY_BACKEND=firestore`.
- Re-seed Firestore after a data change: `GOOGLE_CLOUD_PROJECT=<project-id> .venv/bin/python scripts/seed_firestore.py`

### 6.3 Local
- Hybrid backend runs on **port 8090** from `RemedyMesh-Hybrid/remedy_mesh_app/.venv`; the original
  recording setup stays on 8080. Open `http://127.0.0.1:8090/`.
- `REMEDY_DEMO_CALL=1` scripts only the call conversation (for recording); everything else is live.
- **Live calls locally** need a public tunnel. The ngrok account has a single address, already
  pointed at the original backend on 8080; a second tunnel gets the same address and conflicts.
  So live calls for the hybrid are tested on the cloud.

---

## 7. Decisions on record

- **Hybrid over all-in-Cloud-Run** for the story (agents as managed services, isolation by
  architecture), with the original service kept as the fallback.
- **One supplier agent, run per supplier.** Never one agent per supplier.
- **Nothing hardcoded.** Agent behaviour is shaped only through instructions; verdicts and
  allocations stay in deterministic code, as in the original.
- **No Memory Bank** for the deadline; supplier-reliability memory is roadmap.
- **Firestore option 1:** read-only case database, described accurately.
- **Approver acknowledges declines** rather than having them auto-cleared.

---

## 8. Open items

- **Main branch history** still contains an old project ID (in the seed script) and the live URL
  with the project number; rewrite or scrub before the repository is made public.
- **Budget alert** at ₹400 versus ≈ ₹80/day idle (see §5).

## 9. Roadmap ideas

1. In-app "Agents" panel showing the 8 deployed agents, their latency and data source.
2. Persist notifications and ledgers in Firestore so restarts keep work.
3. A live "new complaint arrives" path that writes to Firestore and changes a verdict on screen.
4. Supplier-reliability memory across recalls (Memory Bank).
5. Real supplier systems negotiating over Google's agent-to-agent (A2A) protocol.
6. Real complaint data from NHTSA's public datasets.
