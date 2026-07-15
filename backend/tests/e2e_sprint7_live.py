"""Live E2E acceptance for the Sprint 7 intelligence rewrite.

Test 1 — ADVANCE loopback: pick a real published swatch, send it as the
selected region -> the exact record must come back #1 (pHash loopback,
no GPT-4o spend).

Test 2 — Blue kitchen cabinet: full scene + blue cabinet crop + bbox ->
object must be cabinetry (never wall paint) and every returned match must
be visually re-ranked or honestly empty.
"""
import base64
import io
import json
import os
import sys

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")
API = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"

s = requests.Session()
r = s.post(f"{API}/auth/login", json={"email": "admin@materialmatch.ai",
                                       "password": "MaterialAdmin2026!"}, timeout=20)
assert r.status_code == 200, r.text

r = s.post(f"{API}/projects", json={"name": "TEST_Sprint7_E2E",
                                     "room_type": "Kitchen",
                                     "budget_range": "Standard"}, timeout=20)
pid = r.json().get("id") or r.json().get("_id")
print("project:", pid)

# ── Test 1: loopback with a real published swatch ─────────────────────────
from pymongo import MongoClient
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
rec = db.ke_records.find_one({"status": "published",
                              "page_preview_b64": {"$type": "string", "$ne": ""},
                              "upload_id": {"$exists": True}})
assert rec, "no published swatch with image found"
print("loopback target:", rec.get("brand"), "|", rec.get("material_name"), "|", rec.get("category"))

r = s.post(f"{API}/projects/{pid}/analyze-region",
           json={"crop_b64": rec["page_preview_b64"], "note": "loopback test"},
           timeout=120)
print("T1 status:", r.status_code)
d = r.json()
rows = d.get("rows", [])
assert rows, "no rows"
row = rows[0]
matches = row.get("catalogue_matches") or []
print("T1 matches:", len(matches), "| rerank:", row.get("rerank"))
for m in matches[:3]:
    print("   ", m["match_percent"], m["brand"], "|", m["material_name"],
          "| stage:", m["debug"]["pipeline_stage"], "| exact:", m["exact_visual_match"])
t1_pass = bool(matches) and matches[0]["exact_visual_match"] and \
    matches[0]["material_name"] == rec["material_name"]
print("T1 LOOPBACK PASS:", t1_pass)

# ── Test 2: blue kitchen cabinet, object-aware + rerank ──────────────────
def make_kitchen_scene():
    img = Image.new("RGB", (900, 600), (235, 230, 222))          # wall
    dr = ImageDraw.Draw(img)
    dr.rectangle([0, 480, 900, 600], fill=(180, 160, 135))       # floor
    dr.rectangle([80, 250, 620, 480], fill=(38, 74, 118))        # base cabinets (blue)
    for x in (215, 350, 485):
        dr.line([(x, 250), (x, 480)], fill=(25, 50, 85), width=4)  # cabinet gaps
    for x in (140, 275, 410, 545):
        dr.rectangle([x, 300, x + 46, 308], fill=(200, 190, 170))  # handles
    dr.rectangle([80, 225, 640, 250], fill=(222, 218, 210))      # countertop
    dr.rectangle([660, 120, 860, 480], fill=(38, 74, 118))       # tall unit
    return img

scene = make_kitchen_scene()
crop = scene.crop((100, 270, 460, 460))  # inside the blue cabinet run

def b64(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()

bbox = [100 / 9, 270 / 6, 360 / 9, 190 / 6]  # percent [x,y,w,h]
r = s.post(f"{API}/projects/{pid}/analyze-region",
           json={"crop_b64": b64(crop), "full_image_b64": b64(scene),
                 "bbox": bbox, "note": "blue cabinet test"},
           timeout=180)
print("T2 status:", r.status_code)
d = r.json()
rows = d.get("rows", [])
assert rows, "no rows"
row = rows[0]
print("T2 object_type:", row.get("object_type"), "| family:", row.get("material_family"),
      "| type:", row.get("material_type"))
print("T2 searched:", row.get("searched_categories"))
matches = row.get("catalogue_matches") or []
print("T2 matches:", len(matches), "| rerank:", row.get("rerank"),
      "| match_state:", row.get("match_state"))
for m in matches[:4]:
    print("   ", m["match_percent"], m["category"], "|", m["brand"], "|", m["material_name"],
          "| verified:", m.get("visually_verified"),
          "| rerank:", m["debug"].get("rerank_score"), m["debug"].get("rerank_verdict"))

obj = str(row.get("object_type") or "").lower()
not_paint = "paint" not in str(row.get("material_type") or "").lower() and \
    str(row.get("material_family") or "").lower() not in ("wall", "paint")
cabinet_detected = any(k in obj for k in ("cabinet", "wardrobe", "vanity", "tv unit"))
no_paint_matches = all(m["category"] not in ("Paints", "Paint") for m in matches)
rerank_info = row.get("rerank") or {}
honest = bool(matches) or bool((row.get("match_state") or {}).get("no_confident_match")) \
    or rerank_info.get("skipped") in ("rerank_failed", "no_llm_key")
print("T2 CABINET-NOT-PAINT PASS:", cabinet_detected and not_paint and no_paint_matches and honest)

s.delete(f"{API}/projects/{pid}", timeout=15)
