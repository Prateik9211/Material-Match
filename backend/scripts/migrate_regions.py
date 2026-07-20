"""One-shot migration: stamp `region: "IN"` on every existing catalogue
record so the 2026-02-08 multi-region rollout doesn't strand pre-existing
Advance / Elysian data.

Idempotent — safe to re-run. Only writes to docs that don't already
have a `region` field.

Also migrates legacy user `preferred_region` values:
    "India"  → "IN"
    "Global" → "IN" (dropped as a search scope; see server.py notes)

Usage:
    python /app/backend/scripts/migrate_regions.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient


async def main() -> None:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    print("[migrate_regions] Starting…\n")

    # 1) ke_records — every existing record without a region → IN
    r1 = await db.ke_records.update_many(
        {"region": {"$exists": False}},
        {"$set": {"region": "IN"}},
    )
    print(f"  ke_records:          {r1.modified_count} rows stamped region='IN'")

    # 2) ke_uploads — same treatment
    r2 = await db.ke_uploads.update_many(
        {"region": {"$exists": False}},
        {"$set": {"region": "IN"}},
    )
    print(f"  ke_uploads:          {r2.modified_count} rows stamped region='IN'")

    # 3) affiliate_products — same treatment
    r3 = await db.affiliate_products.update_many(
        {"region": {"$exists": False}},
        {"$set": {"region": "IN"}},
    )
    print(f"  affiliate_products:  {r3.modified_count} rows stamped region='IN'")

    # 4) users — migrate legacy preferred_region values.
    r4a = await db.users.update_many(
        {"preferred_region": "India"},
        {"$set": {"preferred_region": "IN"}},
    )
    r4b = await db.users.update_many(
        {"preferred_region": "Global"},
        {"$set": {"preferred_region": "IN"}},
    )
    print(f"  users (India→IN):    {r4a.modified_count}")
    print(f"  users (Global→IN):   {r4b.modified_count}")

    # 5) Report
    counts = {}
    for col in ("ke_records", "ke_uploads", "affiliate_products"):
        counts[col] = {}
        for r in ("IN", "US", "AE"):
            counts[col][r] = await db[col].count_documents({"region": r})
    print("\nPost-migration counts by region:")
    for col, d in counts.items():
        print(f"  {col:22} IN={d['IN']:<5} US={d['US']:<5} AE={d['AE']}")

    print("\n[migrate_regions] Done.")


if __name__ == "__main__":
    asyncio.run(main())
