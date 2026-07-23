"""Dry-run audit of test/artefact user accounts.

Uses the SAME identification logic as `_REAL_USER_QUERY` in server.py
(2026-02-08 investigation), inverted — i.e. anyone flagged by the
existing filter is a test artefact candidate.

Prints:
  * count of candidate test users
  * a random sample of 10 emails
  * counts of associated records across all user-scoped collections
  * explicit safety check: the 2 known real users are NEVER in the set
"""
from __future__ import annotations
import os
import asyncio
import random
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

# Same query as server.py _REAL_USER_QUERY — inverted (via $or of positive matches).
TEST_USER_QUERY: dict = {
    "$or": [
        {"email": {"$regex": r"@test\.com$",         "$options": "i"}},
        {"email": {"$regex": r"@t\.com$",            "$options": "i"}},
        {"email": {"$regex": r"@example\.com$",      "$options": "i"}},
        {"email": {"$regex": r"@materialmatch\.ai$", "$options": "i"}},
        {"email": {"$regex": r"^(test|uitest|sam3|sprint|region_pref|other|empty|qa)(_|[0-9]|$)",
                   "$options": "i"}},
    ]
}

# The founder-confirmed real accounts. NEVER delete these regardless
# of any pattern match. `admin@materialmatch.ai` is the operational
# admin login the founder actively uses — deleting it locks him out of
# the admin panel, so it stays even though it matches the
# `@materialmatch.ai` test-artefact pattern.
PROTECTED_EMAILS = {
    "pgirwalkar@gmail.com",
    "ar.priyankasg@gmail.com",
    "admin@materialmatch.ai",
}


async def main() -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    total_users = await db.users.count_documents({})
    test_users = await db.users.find(TEST_USER_QUERY, {"email": 1, "created_at": 1}).to_list(5000)
    test_count = len(test_users)

    # Safety: strip any protected emails that somehow slipped in.
    before_strip = test_count
    test_users = [u for u in test_users if (u.get("email") or "").lower() not in {e.lower() for e in PROTECTED_EMAILS}]
    after_strip = len(test_users)

    test_emails = [u.get("email") for u in test_users]
    test_ids = [str(u["_id"]) for u in test_users]        # ObjectId string form
    test_ids_raw = [u["_id"] for u in test_users]          # ObjectId form

    print("=" * 72)
    print("TEST USER AUDIT (dry-run — nothing will be deleted)")
    print("=" * 72)
    print(f"Total users in DB:        {total_users}")
    print(f"Candidate test users:     {test_count}")
    print(f"Removed by protected list: {before_strip - after_strip} (should be 0)")
    print(f"Final delete set size:    {after_strip}")
    print()

    # Explicit safety check
    print("SAFETY CHECK — protected real users:")
    for email in PROTECTED_EMAILS:
        in_set = any((e or "").lower() == email.lower() for e in test_emails)
        u = await db.users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}}, {"email": 1})
        exists = "EXISTS in DB" if u else "not found in DB"
        marker = "IN DELETE SET" if in_set else "SAFE (excluded)"
        print(f"  {email:35s} — {exists:25s} — {marker}")
    print()

    # Random sample
    sample = random.sample(test_emails, k=min(10, len(test_emails)))
    print("SAMPLE (10 random emails from the delete set):")
    for e in sample:
        print(f"  {e}")
    print()

    # Breakdown by pattern
    print("BREAKDOWN by pattern:")
    for label, pattern in [
        ("@test.com",         r"@test\.com$"),
        ("@t.com",            r"@t\.com$"),
        ("@example.com",      r"@example\.com$"),
        ("@materialmatch.ai", r"@materialmatch\.ai$"),
        ("prefix test_/uitest_/sam3_/sprint*/region_pref_/other_/empty_/qa*",
                              r"^(test|uitest|sam3|sprint|region_pref|other|empty|qa)(_|[0-9]|$)"),
    ]:
        n = await db.users.count_documents({"email": {"$regex": pattern, "$options": "i"}})
        print(f"  {label:70s}  {n}")
    print()

    # Associated records that would need to be cleaned.
    # user_id-keyed collections
    print("ASSOCIATED RECORDS (would be cleaned):")
    counts = {}
    counts["projects (user_id)"]        = await db.projects.count_documents({"user_id": {"$in": test_ids}})
    counts["reports (user_id)"]         = await db.reports.count_documents({"user_id": {"$in": test_ids}})
    counts["rooms (user_id)"]           = await db.rooms.count_documents({"user_id": {"$in": test_ids}})
    counts["reviews (user_id)"]         = await db.reviews.count_documents({"user_id": {"$in": test_ids}})
    counts["usage_counters (user_id)"]  = await db.usage_counters.count_documents({"user_id": {"$in": test_ids}})
    # uploaded_by-keyed collections
    counts["ke_records (uploaded_by, user-scope)"] = await db.ke_records.count_documents(
        {"uploaded_by": {"$in": test_ids}, "catalogue_scope": "user"})
    counts["ke_uploads (uploaded_by)"]  = await db.ke_uploads.count_documents({"uploaded_by": {"$in": test_ids}})

    for k, v in counts.items():
        print(f"  {k:45s}  {v}")

    print()
    print("=" * 72)
    print("Nothing deleted. Awaiting founder confirmation.")
    print("=" * 72)

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
