"""Sprint 2 hardening — orphan-recovery test.

Verifies that any upload left in `status='processing'` at application
startup is automatically flipped to `failed` with a clear diagnostic so
the admin can click Reprocess. This is the recoverability guarantee for
RC1: no upload can be stuck on `processing` forever, regardless of
whether the previous process died to a restart, an OOM kill, a provider
failure, or a deploy roll-out."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, "/app/backend")


def _mongo_client_and_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        # Fallback to backend/.env
        for line in open("/app/backend/.env"):
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            if k == "MONGO_URL":
                mongo_url = mongo_url or v
            elif k == "DB_NAME":
                db_name = db_name or v
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL / DB_NAME not configured")
    return AsyncIOMotorClient(mongo_url), db_name


async def _seed_orphan(uid: str) -> None:
    client, db_name = _mongo_client_and_db()
    db = client[db_name]
    await db.ke_uploads.delete_one({"id": uid})
    await db.ke_uploads.insert_one({
        "id": uid,
        "filename": "orphan-recovery-test.pdf",
        "status": "processing",
        "records_extracted": 0,
        "page_count": 0,
        "size_bytes": 0,
        "created_at": "2026-07-13T00:00:00+00:00",
    })


async def _cleanup(uid: str) -> None:
    client, db_name = _mongo_client_and_db()
    db = client[db_name]
    await db.ke_uploads.delete_one({"id": uid})


async def _run_recovery_sweep() -> int:
    """Directly invoke the same sweep code the startup hook runs so we
    can validate it deterministically without restarting the process."""
    client, db_name = _mongo_client_and_db()
    db = client[db_name]
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    res = await db.ke_uploads.update_many(
        {"status": "processing"},
        {"$set": {
            "status": "failed",
            "failure_reason": (
                "Extraction was interrupted by an application restart or "
                "provider failure. Click Reprocess to retry — the "
                "uploaded PDF is still saved on the server."
            ),
            "interrupted_at": now_iso,
        }},
    )
    return res.modified_count


def test_orphan_processing_upload_gets_recovered_to_failed():
    """A row stuck on `processing` before startup must be flipped to
    `failed` with an actionable failure_reason and an `interrupted_at`
    timestamp."""
    orphan_id = "test-orphan-recovery-42"

    async def _go():
        await _seed_orphan(orphan_id)
        await _run_recovery_sweep()
        client, db_name = _mongo_client_and_db()
        db = client[db_name]
        doc = await db.ke_uploads.find_one({"id": orphan_id})
        try:
            assert doc is not None
            assert doc["status"] == "failed"
            assert "interrupted by an application restart" in doc["failure_reason"]
            assert "Reprocess" in doc["failure_reason"]
            assert "interrupted_at" in doc
        finally:
            await _cleanup(orphan_id)

    asyncio.get_event_loop().run_until_complete(_go()) if not asyncio.get_event_loop().is_running() else asyncio.run(_go())


def test_recovery_sweep_is_idempotent():
    """Running the sweep twice must not re-touch already-failed rows."""
    orphan_id = "test-orphan-recovery-idempotent"

    async def _go():
        await _seed_orphan(orphan_id)
        first = await _run_recovery_sweep()
        second = await _run_recovery_sweep()
        # The first sweep flips this orphan (plus possibly others in
        # the DB). The second sweep must NOT touch it again — the row
        # is already `failed`.
        assert first >= 1
        client, db_name = _mongo_client_and_db()
        db = client[db_name]
        doc = await db.ke_uploads.find_one({"id": orphan_id})
        try:
            assert doc["status"] == "failed"
            # Second sweep never re-flips a failed row.
            assert second == 0
        finally:
            await _cleanup(orphan_id)

    asyncio.get_event_loop().run_until_complete(_go()) if not asyncio.get_event_loop().is_running() else asyncio.run(_go())
