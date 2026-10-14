"""Data access layer with a pluggable backend.

REMEDY_BACKEND = "json"       (default) read the bundled input/ corpus — free, no billing
REMEDY_BACKEND = "firestore"  read Firestore collections — the "agent queries a live DB" mode

The agents and tools ONLY call the accessors below, so flipping the backend is a
one-env-var change: no agent/tool code changes when we move to the database.

Firestore layout (seeded by scripts/seed_firestore.py):
  collection "cases"            doc id = case_id
  collection "complaints"       doc id = complaint_id   (field: case_id)
  collection "production_lots"  doc id = lot_id         (field: case_id)
  collection "remedy_campaigns" doc id = campaign_id    (field: case_id)
  collection "suppliers"        doc id = party_id
  collection "dealers"          doc id = dealer_id
  collection "config"           doc id = "policy_rules"
"""
import json
import os
import pathlib
from functools import lru_cache

BACKEND = os.environ.get("REMEDY_BACKEND", "json").lower()

_DEFAULT = pathlib.Path(__file__).resolve().parents[2] / "remedy_mesh_synthetic_data" / "input"
DATA_DIR = pathlib.Path(os.environ.get("REMEDY_DATA_DIR", str(_DEFAULT)))


# ----------------------------------------------------------------- JSON backend
@lru_cache(maxsize=None)
def _load(name: str):
    with open(DATA_DIR / name) as f:
        return json.load(f)


# ------------------------------------------------------------ Firestore backend
@lru_cache(maxsize=1)
def _fs():
    from google.cloud import firestore  # lazy: only needed in firestore mode
    return firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))


@lru_cache(maxsize=None)
def _fs_collection(name: str):
    return [d.to_dict() for d in _fs().collection(name).stream()]


# ----------------------------------------------------------------- public accessors
def _coll(json_name: str, fs_name: str):
    return _fs_collection(fs_name) if BACKEND == "firestore" else _load(json_name)


def cases():            return _coll("cases.json", "cases")
def complaints():       return _coll("complaints.json", "complaints")
def production_lots():  return _coll("production_lots.json", "production_lots")
def remedy_campaigns(): return _coll("remedy_campaigns.json", "remedy_campaigns")
def suppliers():        return _coll("suppliers.json", "suppliers")
def dealers():          return _coll("dealers.json", "dealers")


def policy_rules():
    if BACKEND == "firestore":
        return _fs().collection("config").document("policy_rules").get().to_dict()
    return _load("policy_rules.json")


# convenience filters (used by logic/tools) — backend-agnostic
def case(case_id: str):
    return next((c for c in cases() if c["case_id"] == case_id), None)

def campaign_for(case_id: str):
    return next((c for c in remedy_campaigns() if c["case_id"] == case_id), None)

def lot_for(case_id: str):
    return next((l for l in production_lots() if l["case_id"] == case_id), None)

def complaints_for(case_id: str):
    if BACKEND == "firestore":
        # a real indexed query against the database (the "agent analyzing the DB" story)
        q = _fs().collection("complaints").where("case_id", "==", case_id).stream()
        return [d.to_dict() for d in q]
    return [x for x in complaints() if x["case_id"] == case_id]
