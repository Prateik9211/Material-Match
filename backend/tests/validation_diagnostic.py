"""Deep-diagnostic run — bypass the 62% min_overall gate and reveal raw
retrieval scores + Brain decisions per zone."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, "/app/backend")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")

# Import the live server pieces (this triggers server startup indirectly).
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

# We'll call the internal helpers directly to peek inside.
import server as srv  # noqa: E402
from intelligence.dna import (dna_from_query_row, embedding_text,  # noqa: E402
                              generate_swatch_dna)
from intelligence.embeddings import get_embedder  # noqa: E402
from intelligence.retrieval import retrieve  # noqa: E402

SITE = "/tmp/validation/site_0_860403ee827fc0d8.jpg"
REF0 = "/tmp/validation/ref_0_627a1ca5c4de7824.jpg"

ZONES = [
    ("Dark walnut wardrobe", SITE, (720, 850, 180, 400), "Laminate", "dark walnut wood grain"),
    ("Light oak floor", SITE, (250, 1450, 500, 130), "Laminate", "light warm oak wood plank"),
    ("Cream fabric headboard", SITE, (340, 870, 240, 130), "Fabric", "cream beige fabric"),
    ("White wall", SITE, (300, 100, 380, 150), "Paint", "off-white matte wall paint"),
    ("Wood slat headboard render", REF0, (330, 480, 500, 100), "Laminate", "warm oak wood grain"),
]


def _crop_b64(path: str, bbox_px: tuple) -> str:
    img = Image.open(path).convert("RGB")
    x, y, w, h = bbox_px
    W, H = img.size
    crop = img.crop((x, y, min(W, x + w), min(H, y + h)))
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


async def _load_index() -> list[dict]:
    """Load published records the same way the server does."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    docs = await db.ke_records.find({"status": "published"}).to_list(3000)
    return [srv._studio_record_to_search_item(d) for d in docs]


async def main() -> None:
    items = await _load_index()
    print(f"Loaded {len(items)} published items")

    api_key = os.environ.get("EMERGENT_LLM_KEY") or open("/app/backend/.env").read().split(
        'EMERGENT_LLM_KEY="')[1].split('"')[0]

    embedder = get_embedder()

    for name, path, bbox, expected_family, hint in ZONES:
        print(f"\n{'=' * 70}\n{name}   (expected={expected_family})")
        crop_b64 = _crop_b64(path, bbox)
        # 1) Generate live DNA on the crop (metadata dict as expected by fn)
        dna = await generate_swatch_dna(
            crop_b64, {"hint": hint}, api_key,
            provider="openai", model="gpt-4o-mini",
        )
        if not dna:
            print("  DNA generation failed"); continue
        print(f"  DNA -> family={dna.get('material_family')} type={dna.get('material_type')} "
              f"color={dna.get('color')} finish={dna.get('finish')}")
        print(f"       canonical: {dna.get('canonical_description')}")
        # 2) Embed the DNA
        qvec = embedder.embed([embedding_text(dna)])[0]
        # 3) Retrieve top 15 WITHOUT the 62% gate
        raw = retrieve(dna, qvec, items, top_k=15)
        print(f"  Retrieved {len(raw)} raw candidates (no min-overall cut):")
        for i, c in enumerate(raw[:10]):
            it = c["item"]
            emb = c.get("embedding_similarity")
            emb_s = f"{emb:.3f}" if emb is not None else "N/A"
            print(f"    [{i+1}] score={c['retrieval_score']:.3f} emb={emb_s} "
                  f"| {it.get('brand')} / {it.get('material_name')} ({it.get('material_code')}) "
                  f"[{it.get('category')}]")
            vd = it.get('visual_dna') or {}
            print(f"        cat_dna: fam={vd.get('material_family')} col={vd.get('color')} desc='{(vd.get('canonical_description') or '')[:80]}'")
        # 4) Brain decision
        # Build a minimal row like server does
        fake_row = {
            "material_family": dna.get("material_family"),
            "material_type": dna.get("material_type"),
            "color": dna.get("color"),
            "finish": dna.get("finish"),
            "zone": name,
            "vendor_type": "",
        }
        try:
            brain = srv.materialmatch_brain(fake_row)
            print(f"  Brain -> allowed_categories={brain['allowed_categories']}")
        except Exception as e:
            print(f"  Brain failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
