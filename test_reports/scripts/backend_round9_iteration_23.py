#!/usr/bin/env python3
"""Focused backend verification for round 9 vocab + preview endpoint contract."""
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path('/app/backend')
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from intelligence.scene_segmentation import ARCHITECTURAL_VOCAB, DETERMINISTIC_MATERIAL, LABEL_MIN_CONFIDENCE  # noqa: E402

BASE = 'http://localhost:8001/api'
OUT = Path('/app/test_reports/backend_round9_iteration_23.json')
report = {'checks': [], 'violations': []}

def check(name, passed, details=None):
    report['checks'].append({'name': name, 'passed': bool(passed), 'details': details or {}})
    print(f"{'PASS' if passed else 'FAIL'}: {name} {details or ''}")

vocab = {v.lower() for v in ARCHITECTURAL_VOCAB}
required_vocab = ['feature wall', 'accent wall', 'wall art', 'framed art', 'artwork', 'painting', 'picture frame']
check('SAM3 vocabulary includes accent/feature wall + wall-art prompts', all(p in vocab for p in required_vocab), {'missing': [p for p in required_vocab if p not in vocab]})

wall_art_prompts = ['wall art', 'framed art', 'artwork', 'painting', 'picture frame']
check('wall-art prompts are deterministic skip-material None', all(p in DETERMINISTIC_MATERIAL and DETERMINISTIC_MATERIAL[p] is None for p in wall_art_prompts), {'values': {p: DETERMINISTIC_MATERIAL.get(p, '<missing>') for p in wall_art_prompts}})

check('feature/accent wall min confidence is 0.40', LABEL_MIN_CONFIDENCE.get('feature wall') == 0.40 and LABEL_MIN_CONFIDENCE.get('accent wall') == 0.40, {'feature wall': LABEL_MIN_CONFIDENCE.get('feature wall'), 'accent wall': LABEL_MIN_CONFIDENCE.get('accent wall')})

r = requests.get(f'{BASE}/catalogue-preview/d868ead7', timeout=30)
check('/api/catalogue-preview/{record_id} does not exist', r.status_code == 404, {'status': r.status_code, 'body': r.text[:200]})

r = requests.get(f'{BASE}/admin/studio/uploads/nope-xxx/page/1', timeout=30)
check('admin studio page preview route exists and is protected when unauthenticated', r.status_code == 401, {'status': r.status_code, 'body': r.text[:200]})

sess = requests.Session()
r = sess.post(f'{BASE}/auth/login', json={'email': 'admin@materialmatch.ai', 'password': 'MaterialAdmin2026!'}, timeout=30)
admin_login_ok = r.status_code == 200 and (r.json().get('role') in {'admin', 'user'})
headers = {'Authorization': f"Bearer {r.json().get('access_token')}"} if r.ok else {}
check('admin login usable for studio route check', admin_login_ok, {'status': r.status_code, 'role': r.json().get('role') if r.ok else None, 'body': None if r.ok else r.text[:200]})
if r.ok:
    r2 = sess.get(f'{BASE}/admin/studio/uploads/nope-xxx/page/1', headers=headers, timeout=30)
    check('admin studio page preview route remains mounted (admin reaches route, invalid id 404)', r2.status_code == 404, {'status': r2.status_code, 'body': r2.text[:200]})
else:
    check('admin studio page preview route remains mounted (admin reaches route, invalid id 404)', False, {'blocked': 'admin login failed'})

report['passed'] = all(c['passed'] for c in report['checks'])
OUT.write_text(json.dumps(report, indent=2))
if not report['passed']:
    raise SystemExit(1)
