#!/usr/bin/env python3
"""
Remedy Mesh — INPUT data generator.

Philosophy: this produces ONLY input data. No pre-computed verdicts, no agent
work-products. The multi-agent system must reason over this corpus and PRODUCE:
  - the scope-expansion decision (Layer 1)
  - the activity log / obtained records (Resolution Loop)
  - the negotiation + commitment ledger (Layer 2)

Those three output files are therefore written EMPTY here. The finished-state
files that previously shipped are copied into expected_outputs/ as golden truth
to validate the agents against.

Deterministic (seed=42) so it is reproducible and scalable.

VIN scheme: "VXM" + 6-digit zero-padded integer, so VIN/lot-range matching is a
plain numeric comparison the deterministic layer can actually compute.
"""
import json, os, random, shutil
from datetime import date, datetime, timedelta

SEED = 42
random.seed(SEED)
TODAY = date(2026, 10, 12)
HERE = os.path.dirname(os.path.abspath(__file__))
INP = os.path.join(HERE, "input")
EXP = os.path.join(HERE, "expected_outputs")
os.makedirs(INP, exist_ok=True)
os.makedirs(EXP, exist_ok=True)

def vin(n): return f"VXM{n:06d}"
def iso(d): return d.isoformat()
def dt(d, h=10, m=0): return datetime(d.year, d.month, d.day, h, m).isoformat()
def days_between(a, b): return (b - a).days

# US state centroids (approx) for geocoding clusters
STATE_GEO = {
    "NC": (35.63, -79.81), "IN": (39.85, -86.26), "FL": (27.77, -81.69),
    "CO": (39.06, -105.31), "NM": (34.84, -106.25), "TN": (35.75, -86.69),
    "TX": (31.05, -97.56), "CA": (36.12, -119.68), "OH": (40.39, -82.76),
    "GA": (33.04, -83.64), "AZ": (33.73, -111.43), "WA": (47.40, -121.49),
}
def jitter(state):
    lat, lon = STATE_GEO[state]
    return round(lat + random.uniform(-1.2, 1.2), 4), round(lon + random.uniform(-1.4, 1.4), 4)

COMPONENTS = ["Integrated Brake Module", "Fuel Pump Control Unit", "Steering Rack Sensor",
              "HV Battery Contactor", "Airbag Inflator", "Seatbelt Pretensioner"]
SAFETY_CRITICAL = ["Integrated Brake Module", "HV Battery Contactor", "Airbag Inflator",
                   "Seatbelt Pretensioner", "Steering Rack Sensor"]

OEMS = ["Veltrix Motors", "Nordhavn Auto", "Kessler Drive", "Aurelia Mobility"]
PLANTS = ["Spartanburg Assembly", "Graz Plant 2", "Toluca Line B", "Nagoya Works 4"]

# Narrative pools so the Evidence Interpreter (LLM) has real unstructured text to parse.
NARR_POST = [
    "Brake warning light returned {wk} weeks after the dealership said the recall repair was complete.",
    "Took it in for the {comp} recall; problem came back on the highway two months later.",
    "Recall work was done in {mo}, but the same fault reappeared — pedal went soft again.",
    "Dealer confirmed recall repair finished, yet the warning chime is back and worse.",
    "After the fix I still lost assist once at low speed. Service record shows repair completed.",
]
NARR_PRE = [
    "Noticed the {comp} acting up; have not received the recall notice yet.",
    "Warning light on, car is not in any recall I was told about.",
    "Intermittent fault with {comp}, dealer says no open recall for my VIN.",
    "Pedal feels off. No recall repair has been done on this vehicle.",
]
NARR_MISC = [
    "Hard to say if related — happened once in cold weather.",
    "Filed late, the issue was from last winter but I forgot to report.",
    "Possible duplicate of my earlier report, same problem.",
]

def pick_narr(pool, comp):
    return random.choice(pool).format(comp=comp, wk=random.randint(3, 20), mo=random.choice(["March","June","August","October"]))

# ------------------------------------------------------------------ CAMPAIGNS
# Original recalls / compliance-board records. These are what scope is measured
# AGAINST — the agent decides whether reality has drifted outside them.
campaigns = []
# Hero campaign — grounded on real BMW NHTSA 24V-104 / 26V422 pattern (names fictional).
campaigns.append({
    "campaign_id": "CAMP-24V104",
    "case_id": "CASE-001",
    "oem": "Veltrix Motors",
    "component": "Integrated Brake Module",
    "filed_date": "2024-04-29",
    "remedy_available_date": "2024-06-01",
    "remedy_description": "Replace integrated brake module control unit; reflash ABS.",
    "original_vin_range_start": vin(44000),
    "original_vin_range_end": vin(200000),
    "original_production_date_start": "2023-01-01",
    "original_production_date_end": "2024-02-29",
    "population_original": 156000,
    "baseline_complaint_rate_per_1k_per_quarter": 0.8,
    "status": "remedy_in_market",
    "source_note": "Shaped on real NHTSA campaigns 24V-104 and the 26V422 'fix-the-fix'. Fictional names/VINs; pattern and dates are real and cited in the brief.",
})
# A few more real-recall cases that MAY need expansion (agent decides).
more_campaign_cases = {
    "CASE-009": ("Nordhavn Auto", "Fuel Pump Control Unit", 2.1),
    "CASE-015": ("Veltrix Motors", "HV Battery Contactor", 0.6),
    "CASE-020": ("Kessler Drive", "Airbag Inflator", 1.4),
    "CASE-022": ("Aurelia Mobility", "Seatbelt Pretensioner", 0.9),
    "CASE-006": ("Nordhavn Auto", "Steering Rack Sensor", 1.1),
    "CASE-013": ("Kessler Drive", "Integrated Brake Module", 0.7),
}
cid_counter = 105
for cid, (oem, comp, base) in more_campaign_cases.items():
    start = random.randint(50000, 120000)
    span = random.randint(60000, 140000)
    fy = random.choice([2022, 2023])
    campaigns.append({
        "campaign_id": f"CAMP-{cid_counter:03d}",
        "case_id": cid,
        "oem": oem,
        "component": comp,
        "filed_date": iso(date(fy, random.randint(1, 10), random.randint(1, 28))),
        "remedy_available_date": iso(date(fy, random.randint(10, 12), 1) + timedelta(days=90)),
        "remedy_description": f"Inspect and replace {comp.lower()} per bulletin.",
        "original_vin_range_start": vin(start),
        "original_vin_range_end": vin(start + span),
        "original_production_date_start": iso(date(fy, 1, 1)),
        "original_production_date_end": iso(date(fy, 12, 31)),
        "population_original": random.randint(40000, 180000),
        "baseline_complaint_rate_per_1k_per_quarter": base,
        "status": "remedy_in_market",
        "source_note": "Fully fictional comparator case.",
    })
    cid_counter += 1
campaign_by_case = {c["case_id"]: c for c in campaigns}

# ------------------------------------------------------------------ CASES (intake state only)
cases = []
HERO = "CASE-001"
# Decide which cases carry real complaint volume vs. sparse (sparse -> agent should say "no signal").
VOLUME = {  # case_id -> (n_complaints, signal_strength)
    "CASE-001": (260, "strong"),      # hero: genuine out-of-scope post-remedy spike
    "CASE-009": (64, "strong"),       # second genuine confirmation
    "CASE-020": (58, "strong"),       # third genuine confirmation
    "CASE-015": (41, "borderline"),   # has a campaign but signal is weak -> should NOT confirm
    "CASE-022": (23, "borderline"),
    "CASE-006": (14, "weak"),
    "CASE-013": (9, "insufficient"),
}
for i in range(1, 31):
    cid = f"CASE-{i:03d}"
    camp = campaign_by_case.get(cid)
    oem = camp["oem"] if camp else random.choice(OEMS)
    comp = camp["component"] if camp else random.choice(COMPONENTS)
    opened = TODAY - timedelta(days=random.randint(20, 620))
    cases.append({
        "case_id": cid,
        "oem": oem,
        "model": random.choice(["V-SUV7", "N-Sedan3", "K-Cross5", "A-Hatch2", "V-Coupe9"]),
        "component": comp,
        "campaign_id": camp["campaign_id"] if camp else None,
        "status": "New signal",            # INTAKE ONLY — agent moves this; not an output verdict
        "opened_date": iso(opened),
        "days_open": days_between(opened, TODAY),
        "intake_note": "Incoming complaints associated with a closed/remedied campaign." if camp
                       else "Incoming complaints, no prior recall on record.",
        "is_hero_case": cid == HERO,
        "grounding_note": campaign_by_case[HERO]["source_note"] if cid == HERO else None,
    })

# ------------------------------------------------------------------ PRODUCTION LOTS (unconfirmed)
lots = []
# Hero lot — production date AFTER original window, VIN range ABOVE original upper bound.
lots.append({
    "lot_id": "LOT-4471",
    "case_id": "CASE-001",
    "oem": "Veltrix Motors",
    "plant": "Spartanburg Assembly",
    "production_date": "2024-03-12",     # outside original_production_date_end 2024-02-29
    "vin_range_start": vin(200001),      # outside original_vin_range_end VXM200000
    "vin_range_end": vin(310716),
    "units_in_lot": 110716,
    "confirmed_by": None,                # <-- Resolution Loop must OBTAIN this
    "confirmed_ts": None,
})
lot_counter = 4472
HERO_DEALER_LOT = "LOT-4471"
for c in cases:
    if c["case_id"] == "CASE-001":
        continue
    lots.append({
        "lot_id": f"LOT-{lot_counter}",
        "case_id": c["case_id"],
        "oem": c["oem"],
        "plant": random.choice(PLANTS),
        "production_date": iso(date(random.choice([2022, 2023, 2024]), random.randint(1, 12), random.randint(1, 28))),
        "vin_range_start": vin(random.randint(40000, 150000)),
        "vin_range_end": vin(random.randint(160000, 330000)),
        "units_in_lot": random.randint(8000, 120000),
        "confirmed_by": None,
        "confirmed_ts": None,
    })
    lot_counter += 1
lot_by_case = {l["case_id"]: l for l in lots}

# ------------------------------------------------------------------ COMPLAINTS (raw, with VIN; NO post_remedy verdict)
complaints = []
cnum = 1
def make_complaints(cid, n, strength):
    global cnum
    camp = campaign_by_case.get(cid)
    lot = lot_by_case[cid]
    comp = next(c["component"] for c in cases if c["case_id"] == cid)
    # cluster states (fewer states => tighter geographic cluster)
    cluster_states = random.sample(list(STATE_GEO.keys()), {"strong":6,"borderline":7,"weak":9,"insufficient":9}.get(strength, 8))
    remedy_avail = date.fromisoformat(camp["remedy_available_date"]) if camp else None
    # fraction of complaints that are genuine post-remedy failures on out-of-scope VINs
    post_frac = {"strong":0.62,"borderline":0.20,"weak":0.12,"insufficient":0.08}.get(strength, 0.1)
    for k in range(n):
        is_post = camp is not None and random.random() < post_frac
        # VIN: for strong/out-of-scope cases, bias toward the (out-of-scope) lot range
        if camp and is_post:
            lo = int(lot["vin_range_start"][3:]); hi = int(lot["vin_range_end"][3:])
        else:
            lo = int(camp["original_vin_range_start"][3:]) if camp else 44000
            hi = int(camp["original_vin_range_end"][3:]) if camp else 300000
        v = vin(random.randint(lo, hi))
        state = random.choice(cluster_states)
        lat, lon = jitter(state)
        # dates: the real "the fix didn't work" pattern is a SPARSE pre-remedy baseline
        # (spread over a wide earlier window) and a CONCENTRATED post-remedy spike
        # (failures clustered in a recent window) -> post rate >> pre rate.
        if camp:
            if is_post:
                cdate = TODAY - timedelta(days=random.randint(10, 285))      # tight recent window
            else:
                cdate = remedy_avail + timedelta(days=random.randint(-460, 70))  # wide, earlier window
        else:
            cdate = TODAY - timedelta(days=random.randint(5, 400))
        if cdate > TODAY: cdate = TODAY - timedelta(days=random.randint(1, 30))
        # repair record: post-remedy failures have a repair_date AFTER remedy availability
        # and BEFORE the complaint (vehicle was fixed, then failed again).
        if is_post:
            rep_done = True
            rd = cdate - timedelta(days=random.randint(20, 180))
            floor = remedy_avail + timedelta(days=7)
            if rd < floor: rd = floor
            rep_date = iso(rd)
            narr = pick_narr(NARR_POST, comp)
        elif random.random() < 0.2:
            rep_done = False; rep_date = None; narr = pick_narr(NARR_MISC, comp)
        else:
            rep_done = False; rep_date = None; narr = pick_narr(NARR_PRE, comp)
        complaints.append({
            "complaint_id": f"CMP-{cnum:05d}",
            "case_id": cid,
            "vin": v,
            "date": iso(cdate),
            "city": state,  # coarse; state field carries the real geo
            "state": state,
            "lat": lat, "lon": lon,
            "component": comp,
            "mileage": random.randint(8000, 92000),
            "narrative": narr,
            "repair_completed": rep_done,
            "repair_date": rep_date,
        })
        cnum += 1
    # inject a few duplicates (same VIN, within the dedup window) for the dedup logic to catch
    if strength in ("strong", "borderline"):
        for _ in range(max(2, n // 40)):
            base = random.choice([x for x in complaints if x["case_id"] == cid])
            d2 = date.fromisoformat(base["date"]) + timedelta(days=random.randint(1, 20))
            if d2 > TODAY: d2 = TODAY
            dup = dict(base)
            dup.update({"complaint_id": f"CMP-{cnum:05d}", "date": iso(d2),
                        "narrative": random.choice(NARR_MISC)})
            complaints.append(dup); cnum += 1

for cid, (n, strength) in VOLUME.items():
    make_complaints(cid, n, strength)
# a little background noise on a few other cases (0-4 each) -> agent should reject as no-signal
for c in cases:
    if c["case_id"] in VOLUME:
        continue
    if random.random() < 0.35:
        make_complaints(c["case_id"], random.randint(1, 4), "insufficient")

# ------------------------------------------------------------------ POLICY / REFEREE RULES
policy = {
    "policy_version": "2026.10",
    "description": "Evidence-sufficiency and reportability rules the deterministic layer and Verification agent apply. Case-agnostic: contains thresholds, not answers.",
    "open_case_rules": {
        "min_complaints_to_open": 12,
        "post_remedy_rate_multiplier_over_baseline": 2.5,
        "min_post_remedy_failures": 20,
        "duplicate_window_days": 30,
    },
    "cluster_rules": {
        "cluster_min_complaints": 8,
        "cluster_radius_km": 300,
        "independent_of_raw_count": True,
    },
    "evidence_sufficiency_criteria": [
        "vin_match",            # complaint VIN resolves to a known lot
        "lot_match",            # lot identified for the affected VINs
        "scope_drift_shown",    # lot falls outside the campaign's original VIN/date scope
        "install_date_confirmed",  # lot production/confirmation record obtained (not guessed)
        "duplicate_cleared",    # duplicates removed before counting
        "threshold_crossed",    # post-remedy rate OR cluster threshold crossed
    ],
    "reportability": {
        "part_573_reportable_if": "scope_drift_shown AND threshold_crossed AND safety_critical_component",
        "safety_critical_components": SAFETY_CRITICAL,
    },
    "note": "A case is CONFIRMED (and may trigger the negotiation mesh) only when every required evidence_sufficiency_criteria item is met on real records. Missing any -> stay open / keep chasing / or close as no-signal. Never confirm on a missing criterion.",
}

# ------------------------------------------------------------------ SUPPLIERS (private; visible_to self)
suppliers = [
    {"party_id": "OEM", "party_name": "Veltrix Motors", "role": "oem",
     "target_units": 60000, "deadline_weeks": 12, "max_unit_cost": 128.0,
     "private_note": "target_units derived at runtime from the confirmed expanded scope (LOT-4471 replacement demand); ceiling is private. No single supplier can cover this within the window after backlog -> a split is forced.",
     "visible_to": ["OEM"]},
    {"party_id": "SUP_A", "party_name": "Haldane Components", "role": "supplier",
     "capacity_units_per_week": 3200, "unit_cost": 118.5, "lead_time_weeks": 3,
     "existing_backlog_units": 5400, "min_order_units": 2000, "visible_to": ["SUP_A"]},
    {"party_id": "SUP_B", "party_name": "Correns Manufacturing", "role": "supplier",
     "capacity_units_per_week": 2600, "unit_cost": 124.75, "lead_time_weeks": 2,
     "existing_backlog_units": 2100, "min_order_units": 1500, "visible_to": ["SUP_B"]},
    {"party_id": "SUP_C", "party_name": "Ridgeline Precision", "role": "supplier",
     "capacity_units_per_week": 1500, "unit_cost": 131.0, "lead_time_weeks": 1,
     "existing_backlog_units": 800, "min_order_units": 1000, "visible_to": ["SUP_C"]},
]

# ------------------------------------------------------------------ DEALERS / SERVICE CENTERS (Resolution Loop contact chain)
dealers = []
hero_states = ["NC", "IN", "FL", "CO", "NM", "TN"]
for i in range(1, 51):
    state = random.choice(list(STATE_GEO.keys()))
    lat, lon = jitter(state)
    serviced = []
    # the dealer that actually performed CASE-001 repairs references the hero lot
    if i == 7:
        state = "NC"; lat, lon = jitter("NC"); serviced = [HERO_DEALER_LOT]
    dealers.append({
        "dealer_id": f"DLR-{i:03d}",
        "name": f"{random.choice(['Hennessy','Larkspur','Crestview','Maple Ridge','Bayline'])} {suppliers and 'Veltrix' or 'Auto'} {state}",
        "city": state, "state": state, "lat": lat, "lon": lon,
        "phone": f"+1-555-{random.randint(200,999)}-{random.randint(1000,9999)}",
        "serviced_lots": serviced,
        "regional_quality_contact": {
            "name": f"Regional QA {state}",
            "phone": f"+1-555-{random.randint(200,999)}-{random.randint(1000,9999)}",
        },
    })

# ------------------------------------------------------------------ WRITE INPUT FILES
def dump(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
    return len(obj) if isinstance(obj, (list,)) else 1

counts = {}
counts["cases.json"] = dump(os.path.join(INP, "cases.json"), cases)
counts["complaints.json"] = dump(os.path.join(INP, "complaints.json"), complaints)
counts["production_lots.json"] = dump(os.path.join(INP, "production_lots.json"), lots)
counts["remedy_campaigns.json"] = dump(os.path.join(INP, "remedy_campaigns.json"), campaigns)
counts["suppliers.json"] = dump(os.path.join(INP, "suppliers.json"), suppliers)
counts["dealers.json"] = dump(os.path.join(INP, "dealers.json"), dealers)
with open(os.path.join(INP, "policy_rules.json"), "w") as f:
    json.dump(policy, f, indent=2)
counts["policy_rules.json"] = 1

# Agent OUTPUT files — start EMPTY. The agents produce these at runtime.
for out in ["activity_log.json", "negotiation_log.json", "commitment_ledger.json"]:
    with open(os.path.join(INP, out), "w") as f:
        json.dump([], f, indent=2)
    counts[out + " (empty)"] = 0

# ------------------------------------------------------------------ GOLDEN OUTPUTS (copy existing finished-state files)
for src in ["activity_log.json", "negotiation_log.json", "commitment_ledger.json"]:
    p = os.path.join(HERE, src)
    if os.path.exists(p):
        shutil.copy(p, os.path.join(EXP, src))

print("INPUT corpus written to", INP)
for k, v in counts.items():
    print(f"  {k:35} {v}")
print("Golden outputs copied to", EXP)
