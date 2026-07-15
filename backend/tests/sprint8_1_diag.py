"""Diagnostic — trace z4 retrieval to see if GREY PERSIAN TEAK ranks."""
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

async def main():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    docs = await db.ke_records.find({'status': 'published'}).to_list(3000)
    items = [srv._studio_record_to_search_item(d) for d in docs]
    print(f'Loaded {len(items)} items')

    img = Image.open('/tmp/validation/site_0_860403ee827fc0d8.jpg').convert('RGB')
    x, y, w, h = 250, 1450, 500, 130
    crop = img.crop((x, y, x+w, y+h))
    buf = io.BytesIO(); crop.save(buf, 'JPEG', quality=90)
    cb = base64.b64encode(buf.getvalue()).decode()
    dna = await generate_swatch_dna(cb, {'detected_color':'light gray', 'detected_finish':'matte', 'object_type_hint':'floor'},
                                     srv.EMERGENT_LLM_KEY, 'openai', 'gpt-4o-mini')
    print(f'DNA family={dna.get("material_family")} color={dna.get("primary_color")} desc={dna.get("canonical_description")}')
    emb = get_embedder()
    qvec = emb.embed([embedding_text(dna)])[0]
    raw = retrieve(dna, qvec, items, top_k=15)
    print(f'\nTop-15 retrieval:')
    for i, c in enumerate(raw):
        it = c['item']
        print(f'  [{i+1}] score={c["retrieval_score"]:.3f} emb={c.get("embedding_similarity", 0):.3f}  '
              f'| {it.get("brand")} / {it.get("material_name")} ({it.get("material_code")}) [{it.get("category")}]')

    # Also check GREY PERSIAN TEAK direct similarity
    gpt = [it for it in items if 'GREY PERSIAN TEAK' in (it.get('material_name') or '').upper()]
    for g in gpt:
        gv = g.get('dna_embedding') or []
        if gv:
            import numpy as np
            a = np.array(qvec); b = np.array(gv)
            sim = float((a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)))
            print(f'\nDIRECT: GREY PERSIAN TEAK ({g.get("material_code")}) [{g.get("category")}] sim={sim:.3f}')
            print(f'  its DNA: {(g.get("visual_dna") or {}).get("canonical_description")}')

asyncio.run(main())
