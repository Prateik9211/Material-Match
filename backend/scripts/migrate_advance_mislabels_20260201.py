"""One-off DB migration: fix mislabelled Advance laminate records.

Context
-------
The "Advance" brand is a laminates supplier. During earlier catalogue
ingestion, three of their catalogue swatches were mis-categorised as
`Tile` or `Stone/Wood` rather than `Laminate`. Because the Brain uses an
`object_locked` category gate at retrieval time (walls/floors/ceilings
lock into a single material family and refuse to broaden the search),
those three records were surfacing in the wrong searches — laminate
swatches were being returned as tile matches, and a wood-look laminate
was being returned as a stone match.

This script updates ONLY those three specific records identified during
manual audit (2 Tile + 1 Stone/Wood -> Laminate). It is deliberately
scoped by `_id` so a future re-run is a no-op.

Run
---
    cd /app/backend && python scripts/migrate_advance_mislabels_20260201.py

Idempotent: safe to run multiple times.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make backend importable when script is invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId


# The three mis-labelled records confirmed during manual audit.
# Each tuple: (record_id_hex, current_category, current_material_family,
#              material_name_at_audit) — the *_at_audit fields are used
# as a safety guard so we do not update a record that has already
# drifted.
TARGETS = [
    # 2 Advance laminates mis-labelled as Tile:
    ("6a57915f0676e6f5d868ead6", "Tile",  "Tile",  "Swatch p9.s1"),
    ("6a57915f0676e6f5d868ead7", "Tile",  "Tile",  "UNIQUE"),
    # 1 Advance wood-look laminate mis-labelled as Stone/Wood:
    ("6a4a6af0ab194ba808fa639a", "Stone", "Wood",  "Warm Oak Ripple 8834"),
]

NEW_CATEGORY = "Laminates"
NEW_FAMILY = "Laminate"


async def run() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    coll = db.ke_records

    updated = 0
    skipped = 0
    missing = 0
    report_lines: list[str] = []

    for rid_hex, expect_cat, expect_family, expect_name in TARGETS:
        try:
            oid = ObjectId(rid_hex)
        except Exception:  # noqa: BLE001
            report_lines.append(f"[bad-id] {rid_hex}")
            missing += 1
            continue

        rec = await coll.find_one({"_id": oid})
        if not rec:
            report_lines.append(f"[missing] _id={rid_hex} not found in ke_records")
            missing += 1
            continue

        cur_cat = rec.get("category")
        cur_fam = rec.get("material_family")
        cur_name = rec.get("material_name")

        # Already migrated -> idempotent no-op.
        if cur_cat == NEW_CATEGORY and cur_fam == NEW_FAMILY:
            report_lines.append(
                f"[skip-already-fixed] _id={rid_hex} name={cur_name!r}"
            )
            skipped += 1
            continue

        # Safety guard: refuse to mutate a record whose current state
        # doesn't match what we audited — the record may have been
        # edited by another process.
        if cur_cat != expect_cat or cur_fam != expect_family or cur_name != expect_name:
            report_lines.append(
                f"[safety-abort] _id={rid_hex} drifted from audit: "
                f"expected ({expect_cat!r}, {expect_family!r}, {expect_name!r}) "
                f"but got ({cur_cat!r}, {cur_fam!r}, {cur_name!r})"
            )
            skipped += 1
            continue

        result = await coll.update_one(
            {"_id": oid},
            {"$set": {
                "category": NEW_CATEGORY,
                "material_family": NEW_FAMILY,
                "migration_note": "advance_mislabel_fix_20260201",
                "migration_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        if result.modified_count == 1:
            updated += 1
            report_lines.append(
                f"[fixed] _id={rid_hex} name={cur_name!r} "
                f"{cur_cat}/{cur_fam} -> {NEW_CATEGORY}/{NEW_FAMILY}"
            )
        else:
            skipped += 1
            report_lines.append(f"[noop] _id={rid_hex} update matched but did not modify")

    client.close()

    print("=" * 68)
    print("Advance mislabel migration report")
    print("=" * 68)
    for line in report_lines:
        print(line)
    print("-" * 68)
    print(f"updated={updated}  skipped={skipped}  missing={missing}")
    print("=" * 68)
    return updated


if __name__ == "__main__":
    n = asyncio.run(run())
    sys.exit(0 if n >= 0 else 1)
