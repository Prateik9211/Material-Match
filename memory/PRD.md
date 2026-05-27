# MaterialMatch AI - Product Requirements Document

## Original Problem Statement
Build a modern AI SaaS web application called "MaterialMatch AI" that helps architects and interior designers analyze inspiration images and match them with similar materials/products from uploaded catalogues. The app focuses on material analysis, visual similarity matching, and material sourcing workflows. NO rendering, 3D editing, vendor scraping, marketplace, or CAD integrations.

## Architecture
- **Frontend:** React 19 + React Router 7 + Tailwind + Shadcn UI + Sonner toasts
- **Backend:** FastAPI + Motor (async MongoDB) + JWT (httpOnly cookies)
- **AI:** Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`) via `emergentintegrations` (Emergent Universal LLM key)
- **PDF Reports:** Client-side using jsPDF + html2canvas
- **Catalogue PDF parsing:** PyMuPDF (fitz) - converts up to 6 pages per PDF into images
- **Storage:** Base64 in MongoDB (sufficient for MVP)
- **Typography:** Cabinet Grotesk (display) + Satoshi (body) via Fontshare
- **Design language:** Swiss & High-Contrast Architectural Minimalist - white/neutral, soft shadows, bento grids

## User Personas
- Interior designers, Architects, Material consultants, Furniture studios, Design firms

## Core Requirements (Static)
- Authentication system (JWT, email+password)
- Project workflow: create → upload → analyze → report
- Reference image analysis (materials, colors, textures, finishes, style)
- Catalogue matching with similarity scores and explanations
- Downloadable PDF reports
- Modern premium SaaS UX

## Implemented Features (2026-02-26)
- ✅ Landing page (hero, workflow, pricing, footer)
- ✅ Auth page (login/register, Google placeholder)
- ✅ Dashboard (stats, recent projects grid, recent reports)
- ✅ All 26 backend tests passing (auth, projects, uploads, analysis pipeline, reports)
- ✅ Claude Sonnet 4.5 vision integration end-to-end verified (still wired; currently unhooked from main UI flow)

## Live AI Material Analysis (2026-02-26)
- ✅ **Real AI** via `POST /api/projects/{id}/analyze` using OpenAI **gpt-4o-mini** vision through Emergent Universal Key + `emergentintegrations.LlmChat`
- ✅ Feature flag `ENABLE_REAL_ANALYSIS` (env), falls back to mock when off
- ✅ Strict schema validator with retry-with-nudge for malformed JSON (`_validate_analysis_payload`)
- ✅ New schema: `confidence` is integer 0–100, new `material_family` enum (14 values), variable row count (1–12) — no padding
- ✅ Per-user daily quota (`usage_counters` Mongo collection, TTL-cleaned after 32 days)
- ✅ Dedup window (10 min) returns cached result without new LLM call
- ✅ In-progress 409 guard (60s)
- ✅ 5 MiB image cap (413)
- ✅ 45s timeout (504), 2 retries with exponential backoff
- ✅ Quota rolled back on failure
- ✅ Saved analyses readable forever via version-discriminated `mock_analysis` field
- ✅ Frontend `Analysis.jsx` updated for integer % display, `material_family` pill, retuned confidence thresholds
- ✅ DemoModeBanner copy updated: "Live AI analysis · Catalogue matching still in demo mode"

## Simplified MVP Flow (2026-02-26 — current active path)
- ✅ **New Project flow** simplified to one screen: name + optional client + reference image upload, "Create & continue" button disabled until both name and image are present
- ✅ **Material Analysis page** with reference image + "Analyse Materials" CTA
- ✅ **Mock analysis** backed by deterministic server-side library (`/api/projects/{id}/mock-analyze`), 5–8 rows, schema: `zone, material_type, color, texture, finish, design_style, keywords, confidence`
- ✅ Mock analysis persisted on project (`project.mock_analysis`) and survives reload/revisit
- ✅ Re-analyse button returns same stable rows per project ID
- ✅ Auth guard (401) + missing-reference guard (400) + cross-user 404 all enforced
- ✅ Dashboard cards route directly to `/projects/:id/analysis`
- ✅ 6/6 backend pytest pass + 100% frontend Playwright E2E pass

## Real Catalogue Matching Phase 1 (2026-02-27)
- ✅ `POST /api/projects/{id}/match` with uploaded product images goes through OpenAI gpt-4o-mini vision via Emergent Universal Key when `ENABLE_REAL_MATCH=true`
- ✅ Batched scoring (BATCH_SIZE=4, concurrency=4) with `_score_one_batch` + `_run_real_match`
- ✅ Strict candidate-type classification: `product_material_candidate` / `room_scene_or_lifestyle` / `unclear`
- ✅ Family-aware gating (`COMPATIBLE_FAMILIES`, hard/soft family caps, wrong-family pattern detector caps at 39)
- ✅ Per-user daily match quota (`usage_counters.match_count`, default 50/day)
- ✅ 5 MiB per-image cap, 20 candidate max, 35s timeout per batch, 1 retry, 10-min dedup window
- ✅ Server-side anti-inflation clamp (max 92), min-threshold gate (`MATCH_MIN_THRESHOLD=40`)
- ✅ Top-5 results persisted under `project.match_results.<zone>`; warnings list surfaced to UI
- ✅ **2026-02-27 hotfix:** `_validate_batch_result` now validates per-item with zero-fallback for mangled entries — a single bad item in a batch can no longer drop the whole batch. Room-scene candidates may legitimately come with 0–2 reasons. 9 new unit tests in `tests/test_batch_validator.py` lock this in.
- ✅ Verified against `/tmp/test_candidate_validation.py`: wood product → top result, room scenes & banana excluded, wrong-family swatches correctly below threshold.

## Catalogue Match Flow (2026-02-26 — mock)
- ✅ **Find Matches** button per row on Analysis table (9th "Action" column) → `/projects/:id/match?zone={zone}`
- ✅ **Match page** shows: project summary, reference image preview, selected material details card, optional manual prompt textarea, optional PDF + product image uploads, Run Match CTA
- ✅ **Mock match engine** (`POST /api/projects/{id}/match`) generates deterministic top-5 candidates from `MOCK_PRODUCT_LIBRARY` (32 products across wood/stone/fabric/metal/plaster/rug). Files accepted but only `name/type/size` metadata stored — **no PDF parsing**
- ✅ Each match card: product name, catalogue ref, match % (50–98), score label (Strong/Good/Partial/Low), 3 reasons, optional disqualifier (cards 4–5), colored thumbnail block
- ✅ Persisted under `project.match_results.<zone>` — revisit auto-loads saved cards
- ✅ Find Matches button text toggles to "View matches" once that zone has saved results
- ✅ 7/7 backend pytest pass + 100% frontend E2E pass

## India Sourcing Intelligence (2026-02-27, hardened 2026-02-28)
- ✅ Per-user `preferred_region` preference (`India` | `Global`, default `India`)
- ✅ Endpoints: `GET /api/users/me/preferences`, `PUT /api/users/me/preferences`
- ✅ `/api/config` now publishes `supported_regions` and `default_region`
- ✅ Register response + `/auth/me` include `preferred_region`; backfill default applied via `get_current_user`
- ✅ Region-aware AI prompts: `_build_analysis_prompt(region)` and `_build_match_user_prompt(region, …)` append an INDIA SOURCING CONTEXT block (Greenlam / Merino / Century / Action Tesa / Royale Touche / Kajaria / Simpolo / Nitco / Somany / Hafele India / Hettich India / Asian Paints Royale / Nerolac / Berger + Indian terminology — Kota stone, PU matt, vitrified, MDF + laminate, etc.) when `region == "India"`
- ✅ Optional `indian_alternative` field (≤ 120 char string or null) on both analysis rows and match candidates — validator-agnostic
- ✅ **2026-02-28 prompt strengthening:** moved from "MAY add" to "add ONE extra field per row — populate for EVERY row where useful"; dropped strict confidence floor. Verified live: 4/4 analysis rows now consistently populated; match candidates with ≥ 45% also populated.
- ✅ **2026-02-28 header UX fix:** the IN/Global toggle now reads `Region: India | Global` with full words; active button highlighted; toast confirms switch ("India mode: AI now uses Indian-market sourcing context" / "Global mode: no India-specific context").
- ✅ Mock paths surface a static `INDIA_ALTERNATIVES_BY_FAMILY` hint when region=India, omitted when region=Global.
- ✅ Frontend: small **IN / Global** toggle in `Header.jsx` (testids `region-toggle`, `region-in-btn`, `region-global-btn`); amber italic IN-alt line in `Analysis.jsx` (`analysis-indian-alt-{i}`) and `Match.jsx` (`match-indian-alt-{i}`)
- ✅ 14 new tests in `tests/test_region_india.py` (prompt-builder unit + live API integration) + existing batch-validator tests all PASS (23/23 total)
- ✅ Live AI verified: with region=India the model produces Indian terminology and references brands (e.g. "easily achieved with popular Indian brands like Asian Paints", "options available with Indian manufacturers"); existing match gating still rejects room-scenes and wrong-family swatches.

## Live AI Material Analysis (2026-02-26)

## Prioritized Backlog

### P1
- Multi-page report layout improvements (page breaks at sections)
- Project rename/delete from dashboard
- Catalogue item pagination/lazy loading for large libraries (50+ items)
- Forgot password / reset password flow

### P2
- Team workspaces & sharing
- Vendor/SKU tagging on catalogue items
- Stripe billing (Studio free / Practice $49 paywall)
- Google OAuth social login
- Save/star favorite matches
- Comparison view (side-by-side reference vs matched product)

### P3
- Object storage migration (out of MongoDB base64)
- Parallel LLM calls (asyncio.gather) for faster analysis on big catalogues
- Server-side PDF generation (ReportLab) for higher fidelity prints
- Multi-tenant brand customization
