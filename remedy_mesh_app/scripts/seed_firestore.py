#!/usr/bin/env python3
"""Seed Firestore with the Remedy Mesh input corpus.

Run ONCE after billing + Firestore are enabled:

    export GOOGLE_CLOUD_PROJECT=<project-id>
    gcloud auth application-default login          # if not already
    python scripts/seed_firestore.py

Idempotent: re-running overwrites the same documents. Uses batched writes.
"""
import json
import os
import pathlib
import sys

INPUT = pathlib.Path(os.environ.get(
    "REMEDY_DATA_DIR",
    pathlib.Path(__file__).resolve().parents[2] / "remedy_mesh_synthetic_data" / "input",
))

# collection name -> (json file, id field)
COLLECTIONS = {
    "cases":            ("cases.json", "case_id"),
    "complaints":       ("complaints.json", "complaint_id"),
    "production_lots":  ("production_lots.json", "lot_id"),
    "remedy_campaigns": ("remedy_campaigns.json", "campaign_id"),
    "suppliers":        ("suppliers.json", "party_id"),
    "dealers":          ("dealers.json", "dealer_id"),
}


def main():
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        sys.exit("Set GOOGLE_CLOUD_PROJECT first.")
    from google.cloud import firestore

    db = firestore.Client(project=project)

    total = 0
    for coll, (fname, idfield) in COLLECTIONS.items():
        rows = json.load(open(INPUT / fname))
        batch = db.batch()
        n = 0
        for i, row in enumerate(rows, 1):
            batch.set(db.collection(coll).document(str(row[idfield])), row)
            n += 1
            if i % 450 == 0:          # Firestore batch limit is 500
                batch.commit()
                batch = db.batch()
        batch.commit()
        total += n
        print(f"  seeded {coll:18} {n} docs")

    # policy rules as a single config document
    policy = json.load(open(INPUT / "policy_rules.json"))
    db.collection("config").document("policy_rules").set(policy)
    print(f"  seeded config/policy_rules")
    print(f"Done. {total} records + policy written to Firestore in project {project}.")


if __name__ == "__main__":
    main()
