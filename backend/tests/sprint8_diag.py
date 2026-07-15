"""Trace z2 (cane wardrobe inset) & z6/z7 (paint) retrieval to see raw scores."""
import asyncio, base64, io, os, sys
from PIL import Image
sys.path.insert(0, '/app/backend')
os.environ.setdefault('MONGO_URL', 'mongodb://localhost:27017')
os.environ.setdefault('DB_NAME', 'test_database')
from motor.motor_asyncio import AsyncIOMotorClient
import server as srv
from intelligence.dna import generate_swatch_dna, embedding_text
from intelligence.embeddings import get_embedder
from intelligence.retrieval import retrieve

SITE = '/tmp/validation/site_0_860403ee827fc0d8.jpg'
CASES = [
    ('z2 cane inset', (60, 700, 160, 300), 'wardrobe', 'furniture', 'beige woven cane rattan texture panel'),
    ('z6 arch niche paint', (350, 550, 200, 80), 'wall', 'wall', 'warm beige matte wall paint arch niche'),
    ('z7 ceiling paint', (60, 40, 200, 100), 'ceiling', 'ceiling', 'bright white matte ceiling paint'),
]

async def main():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    docs = await db.ke_records.find({'status': 'published'}).to_list(3000)
    items = [srv._studio_record_to_search_item(d) for d in docs]
    print(f'Loaded {len(items)} items')
    key = srv.EMERGENT_LLM_KEY
    emb = get_embedder()

    img = Image.open(SITE).convert('RGB')
    for name, bbox, obj, fam_cls, hint in CASES:
        x,y,w,h = bbox
        crop = img.crop((x,y,x+w,y+h))
        buf = io.BytesIO(); crop.save(buf, 'JPEG', quality=90)
        cb = base64.b64encode(buf.getvalue()).decode()
        meta = {'classifier_material_family': fam_cls, 'object_type': obj,
                'hint': hint}
        print(f'\n{"="*70}\n{name}\n  obj={obj}  hint={hint}')
        dna = await generate_swatch_dna(cb, meta, key, 'openai', 'gpt-4o-mini')
        if not dna:
            print('  DNA failed'); continue
        print(f'  DNA family={dna.get("material_family")} surface={dna.get("surface_type")} pattern={dna.get("pattern")} texture={dna.get("texture")}')
        print(f'  canonical: {dna.get("canonical_description")}')
        # Try retrieval in TWO modes: unrestricted and family-filtered
        qvec = emb.embed([embedding_text(dna)])[0]
        raw = retrieve(dna, qvec, items, top_k=10)
        print(f'  Top-10 unrestricted:')
        for i, c in enumerate(raw[:10]):
            it = c['item']
            print(f'    [{i+1}] score={c["retrieval_score"]:.3f} '
                  f'emb={c.get("embedding_similarity", 0):.3f}  '
                  f'| {it.get("brand")} / {it.get("material_name")} ({it.get("material_code")}) '
                  f'[{it.get("category")}]')
        # Also check LINEN JUTE specifically for z2
        if 'cane' in name.lower():
            linen = [it for it in items if 'LINEN JUTE' in (it.get('material_name') or '')]
            for lj in linen:
                # Compute embedding sim to it directly
                lj_vec = lj.get('dna_embedding') or []
                if not lj_vec:
                    continue
                import numpy as np
                a = np.array(qvec); b = np.array(lj_vec)
                sim = float((a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)))
                print(f'    LINEN direct: {lj.get("material_name")} ({lj.get("material_code")}) [{lj.get("category")}] sim={sim:.3f}')

asyncio.run(main())
