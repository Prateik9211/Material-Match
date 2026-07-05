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

## Sprint 6 — Competition Refocus (2026-07-04)
Refocused product around **Material Library + Catalogue Intelligence + Product Discovery**. Presentation demoted to Beta/Coming Soon per strategic reset. New product line: "MaterialMatch helps designers reach the right shortlist faster before physical verification."
- ✅ **Landing rewrite** with new hero "Turn inspiration into sourceable materials." Workflow visual (Reference → Detected → Sourceable) replaces the previous stock interior image. New "Why MaterialMatch?" section ("Sourcing acceleration — not design automation"), Product Discovery band, Presentation Coming-Soon band. Trust bullets now include "Sourceable shortlist".
- ✅ **Interactive Demo modal** on both hero-cta-demo and closing-cta-demo — 6-step walkthrough (video-frame placeholder + captions + progress bar + prev/next) with Open Demo Project and Create Your First Project actions.
- ✅ **Material Library page** at `/library` (auth-protected). Three sections: **My Library** (aggregated from `projects.match_results.uploaded_files` with usage_count + last_used_at; reuse marked Coming Soon), **Global Library** (4 seeded Indian brands — Asian Paints, Kajaria, Century Ply, Häfele India — all Coming Soon), **Community Library** (Coming Soon placeholder). Transparency note calls out no availability guarantees.
- ✅ **Sourceable Shortlist** — new backend endpoints `POST/GET/DELETE /api/projects/{id}/shortlist` (ShortlistItemCreate with source_type: catalogue_match | product | spec | custom). Frontend `ShortlistSection` on Analysis page, "Add to Shortlist" pill on each Product card that flips to "Shortlisted" state (sage) after add.
- ✅ **Header nav** now shows Material Library link with BookOpen icon on all authenticated pages.
- ✅ **Dashboard empty state** — welcome panel now has **3 CTAs** (Demo, Create, Material Library) and 5 workflow steps ending in "Build sourceable shortlist".
- ✅ **Concept Presentation** button on Analysis page now shows an ochre **Beta** pill; route + workspace preserved intact (nothing deleted per instruction).
- ✅ Copy audit clean: no "AI Summary", "Generated by AI", "GPT", "OpenAI", "Mock", "Backend" anywhere. Approved vocabulary consistently used.
- ✅ Testing agent: **10 new Sprint 6 tests + 61 regression = 71/71 backend pass, 100% frontend, 0 issues found**.

## Sprint 5A — Presentation Experience 2.0 (2026-07-03)
- ✅ **Redesigned Concept Presentation** with new section order: **Cover Page → Room Title → 01 Existing Space → 02 Design Direction → 03 Concept Overview → 04 Material Specifications → 05 Products & Fixtures → 06 Designer Notes**. Empty sections auto-hide in the client-facing view.
- ✅ **Cover Page** for shared + print view: overline "Concept Presentation", big display title (Project Name), "For {Client Name}" subtitle, ROOM + DESIGNER + DATE metadata block, "Prepared with MaterialMatch" tagline. `page-break-after` CSS so the cover prints on its own page.
- ✅ **Design Direction section** merges Reference / Moodboard / Final Render galleries with type-labelled chips in the visual grid. New backend image kind `final_render` + `final_render_images` array. Editor exposes 3 direction tabs (`direction-tab-reference` / `moodboard` / `final_render`) to control which sub-gallery upload goes into.
- ✅ **Rich Material Spec Cards** — each card shows Zone, Material Type, Family, Finish, Texture, Color, Confidence, Procurement Difficulty, PLUS either a sage **Catalogue Match** block (`present-spec-catalogue-{i}` — material_name, filename, page number, match %, explanation) OR an ochre **Recommended Indian Options** block (`present-spec-indian-{i}` — indian_alternative + brands_to_check chips + vendor category + sourcing keywords). Never both on the same card. Catalogue matching is now truly optional per spec.
- ✅ **Auto-populated presentation** — on room creation, `pinned_material_row_ids` defaults to every zone from `project.mock_analysis.rows` (up to 16) and `pinned_product_ids` defaults to every detected product (up to 16). Designer edits from a ready-to-share baseline instead of blank pins.
- ✅ **Public share endpoint enrichment** — `GET /api/public/rooms/{slug}` now returns `catalogue_matches` (from `project.match_results.top_matches`), `designer_name` (best-effort user lookup, no PII leak), and `final_render_images`.
- ✅ **Warm palette** applied to the presentation view + public bar (paper bg, stone-panel section chrome, sage-soft for catalogue matches, ochre-soft for Indian recs, charcoal display type).
- ✅ **Terminology cleanup** — no "AI Summary" / "Generated by AI" / "Mock" / "Backend" anywhere on presentation or editor. Approved labels: Existing Space, Design Direction, Concept Overview, Material Specifications, Products & Fixtures, Designer Notes, Recommended Indian Options, Catalogue Match.
- ✅ Testing agent: **100% frontend + 100% backend (61/61 tests, 5 new Sprint 5A tests all green)**. Zero issues found. Dead helper `_room_public_projection()` removed as tiny cleanup.

## Sprint 4 — Warm Palette Polish + First Experience (2026-07-02)
- ✅ **Warm minimal palette** applied globally — paper `#FAF8F5` (page bg), stone panels `#F5F1EC`, stone borders `#D8CEC2`, charcoal `#2B2724` (primary text), warm-grey `#7A7168` (secondary text), sage `#7F9D7A` (success), ochre-soft (warning). Primary CTAs kept charcoal-on-paper for contrast. Named tokens added to `tailwind.config.js` (paper, stone-panel, charcoal, warm-grey, sage, sand, ochre) so future work can use `bg-paper`, `text-charcoal`, etc.
- ✅ **Landing page rewrite**: hero "Keep designing. We'll handle everything after.", subhead, philosophy line ("MaterialMatch does not design for you…"), 5 trust bullets (Designer-first, India-first sourcing, Catalogue matching, Products & Fixtures detection, Client-ready presentations), two CTAs (Explore Interactive Demo → `/demo`, Create Your First Project → auth/new). USD pricing / GPT / OpenAI / technical wording all removed.
- ✅ **Public read-only Demo Project** seeded at backend startup — `GET /api/demo/project` and `GET /api/demo/reference-image` (no auth). Demo includes: reference image, specification overview (palette + dominant materials), 4 specification zones, 4 products with curated affiliate matches, 3 catalogue matches, and a shareable Concept Presentation at `/share/rooms/materialmatch-demo`. Idempotent seed — safe to redeploy.
- ✅ **First-run Welcome Panel** on empty dashboard: 2 CTAs (Explore Demo, Create Project) + 5-step workflow cards. When projects exist, a small "Explore Demo" button appears in the header row.
- ✅ **Microcopy sweep**: "Analysing…" → "Reading finishes and specification zones…", "Matching…" → "Scanning catalogue pages…", "Loading presentation…" → "Preparing your design story…". Stat card "Mock" label replaced with "Sourcing region". No GPT/OpenAI/backend/mock strings in user-facing copy.
- ✅ **Made with Emergent badge** removed from `/app/frontend/public/index.html` (app-code-removable; not platform-enforced).
- ✅ Testing agent: 100% frontend + 100% backend (56/56 tests, 7 new Sprint 4 demo tests all green). Zero issues found. Prior regression suites (Sprint 2 + Sprint 3, 49 tests) all still pass.

## Sprint 3 — Concept Presentation Workspace (2026-07-02)
- ✅ **Rooms nested inside projects**: new `rooms` MongoDB collection with `project_id`/`user_id` scoping and ordering. 10 room types (living/bedroom/kitchen/bath/dining/office/kids/outdoor/hallway/custom).
- ✅ **7-step visual story** in this exact order: 01 Current Space → 02 Moodboards → 03 Reference Images → 04 Concept Overview → 05 Material Specifications → 06 Suggested Products → 07 Designer Notes. Empty sections auto-hide.
- ✅ **Three image galleries per room** (current-site "before" photos, moodboards, references) — up to 12 images per kind, 6 MB max, JPEG/PNG/WebP. Endpoints: `POST/GET/DELETE /api/rooms/{id}/images/{kind}[/{img_id}]`.
- ✅ **AI Concept Overview draft**: `POST /api/rooms/{id}/generate-overview` returns a 60-120 word paragraph based on pinned materials/products + designer notes. Designer ALWAYS edits and approves — never called "AI Summary". Saved to `concept_overview_ai_draft`; the live `concept_overview` only changes via explicit PATCH.
- ✅ **Pinning**: designers pin material rows (by `zone` key) and detected products (by product id) from the parent project analysis.
- ✅ **Public share link** (v1 must-have): `POST /api/rooms/{id}/share {enabled:true}` returns a public slug. `GET /api/public/rooms/{slug}` and `/images/{kind}/{img_id}` serve the presentation with NO auth. When `share_enabled=false`, endpoints 404. Only pinned rows/products/images are exposed publicly — no user_id, no ai_draft, no other project rows leak.
- ✅ **Printable presentation view**: `/share/rooms/{slug}?print=1` renders without header/nav, applies `@media print` page-break rules, auto-opens the browser print dialog on load.
- ✅ Frontend routes: `/projects/:id/concept` (workspace with sidebar, editor, preview modal, share modal) and `/share/rooms/:slug` (public client view + print).
- ✅ "Concept Presentation" CTA added to the Analysis page top action bar (test-id `open-concept-btn`).
- ✅ 12 new pytest unit tests + 17/18 integration tests all PASS. Testing agent verified full E2E flow (100% frontend, 97% backend). One minor double-DELETE image bug found by testing agent and FIXED (verified via curl: 200 → 404 → 404). All Sprint 3 flows green.

## Sprint 2 — Products & Fixtures + Affiliate DB (2026-07-02)
- ✅ **Products & Fixtures Detection**: separate AI pass triggered automatically by the "Generate Specification" button. Uses `openai/gpt-4o-mini` vision via `emergentintegrations.LlmChat`. Env flag `ENABLE_REAL_PRODUCTS`, timeout `LLM_PRODUCTS_TIMEOUT_S`, model `LLM_MODEL_PRODUCTS`.
- ✅ Schema: `product_name`, `category` (9 enums: lighting, furniture, decor, art, textile-decor, fixture, plant-planter, electronics, other), `description`, `style_keywords`, `color_keywords`, `material_keywords`, `finish_keywords`, `estimated_price_inr`, `search_keywords`, `confidence` (0-100).
- ✅ Deterministic mock fallback (`MOCK_PRODUCTS_LIBRARY` — 6 seed products) when real AI is disabled.
- ✅ **Admin-managed Affiliate Database** (`affiliate_products` collection) with admin CRUD at `/api/admin/affiliates[/{id}]` (GET/POST/PUT/DELETE). Guarded by new `require_admin` dependency.
- ✅ Admin role auto-promotion via `ADMIN_EMAILS` env var (comma-separated). Idempotent — flips `role=admin` on every authenticated request when the email is listed.
- ✅ 10-item Indian-market seed on startup: Pepperfry, Urban Ladder, IKEA India, WoodenStreet, Hafele India, Amazon India, Jaipur Rugs covering lighting, furniture, decor, art, textile-decor, fixture, plant-planter categories.
- ✅ **Keyword-similarity matching engine**: weighted Jaccard on name (30%) + style (25%) + material (20%) + color (15%) + finish (10%). Threshold `AFFILIATE_MATCH_MIN_SCORE=0.20`. Cross-category fallback with 0.75× penalty when no in-category match found.
- ✅ Fallback search URLs: every detected product gets `amazon.in/s?k=…` and `google.com/search?tbm=shop&q=…+india` links (single "india" — no duplication).
- ✅ `GET /api/projects/{id}/products` returns cached detection results with matches. Cross-user access → 404.
- ✅ Frontend: new `/admin/affiliates` page (add/edit/delete modal, image thumbnails, category badges, keyword chips). Header "Affiliates" link shown only when `role=admin`.
- ✅ Frontend: new `ProductsSection` component on Analysis page — visual cards with category badge, confidence pill, price band, style/material keyword chips, green "Curated Recommendation" badge (with clickable affiliate link) + Amazon.in + Google Shopping fallback pills.
- ✅ Landing hero trust bullet updated: "Catalogue-first" → "Products & Fixtures Detection" (testid `hero-trust-products`).
- ✅ 19 new pytest unit tests in `tests/test_products_and_affiliates.py` covering jaccard/tokenizer/score/validator/search URLs/seed invariants — all PASS. Testing agent verified 48/48 relevant tests + full E2E flows green.

## Sprint 7 — Interactive Material Intelligence (2026-07-04)
- ✅ **Region-based analysis backend** `POST /api/projects/{id}/analyze-region` — accepts `crop_b64` + `note`, returns ephemeral `rows/summary/ephemeral=True` without persisting to the project. Deterministic mock fallback when live AI disabled.
- ✅ **RegionSelector** component (Google-Lens style) — click "Select area of interest", drag rectangle on the reference image, client-side canvas crop, POST to analyze-region.
- ✅ **Analysis page redesign** — two-column reference + intelligence-panel layout. Right-side `SummaryPanel` shows full-image analysis by default; switches to zone-specific summary with crop preview when a region is analyzed. `Back to full image` restores default state.
- ✅ **Materials-first hierarchy** — `MaterialsFirstSection` replaces the old spec table. Order: Materials → inline Alternatives (cost tiers + brands) → per-card Match with Catalogue → Products → Product Alternatives → Shortlist.
- ✅ **Per-card Match with Catalogue** button on saved rows; correctly hidden for ephemeral region rows.
- ✅ **Video-style Demo Modal** on Landing — 6 chapters (Reference, Detection, Zone Focus, Catalogue, Products, Shortlist) with auto-advance (4.2s), play/pause, chapter markers timeline, elapsed/total mm:ss counters, jump-to-chapter, keyboard (Space/Arrows/Esc), Open Demo Project CTA.
- ✅ Backend tests: `tests/test_sprint7_analyze_region.py` — 6/6 PASS. Full regression 58/59 (one pre-existing sprint5a public-share failure unrelated).
- ✅ Frontend E2E QA green (testing agent iteration_10): all new testids visible, region draw→analyze→zone-panel→clear flow verified, hierarchy preserved.

## Sprint 2 Revision — Catalogue-First Material Intelligence (2026-07-04)
**Philosophy change:** MaterialMatch stops proving *what* a material is and starts finding *the closest available catalogues* that resemble each selected region — Detect → Search → Compare → Decide.

- ✅ **Seeded Global Catalogue** — 177 hand-authored records across 9 categories in `/app/backend/catalogue_seed.py`:
  - Veneers (20 — Greenlam, Merino, Century Ply, Advance, ArchidPly, Anchor Wood, DesignLam)
  - Laminates (20 — Greenlam, Merino, Century, Sundek, Formica, Wilsonart, Advance, Royale Touche, Duroply, Action Tesa)
  - Stone (15 — RK Marble, Bhandari, Kalinga Stone, Caesarstone, Levantina, Kajaria, Somany, Nitco, Rex Granite)
  - Tiles (15 — Kajaria, Somany, Nitco, Orient Bell, RAK, Simpolo, AGL, Nexion)
  - Fabric (15 — Fabindia, Sarita Handa, D'Decor, Deco Window, The White Window, Jaipur Rugs, Obeetee, Cocoon, House of MG)
  - Lighting (10), Hardware (10), Furniture (10)
  - Paints (62 shades — Asian Paints, Berger, Nerolac, Dulux). **Real codes only** — every unknown code renders "Code unavailable in current database".
- ✅ **Catalogue matcher** (`_score_catalogue_item`) — family-alias + keyword-Jaccard + RGB colour-similarity + finish + texture. Cap at 97 (we never claim exact certainty).
- ✅ **Similarity breakdown** — per match: `overall`, `visual`, `color`, `finish`, `texture` (0–100 each).
- ✅ **Region classification** — heuristic `_classify_row` returns Material Surface / Product / Fixture / Decor / Mixed / Unclear. Product-family rows switch the header to "Closest Product Matches".
- ✅ **Alternative material systems** — 9 families with category-level swaps (e.g. Wood → Natural Veneer / HPL Laminate / Textured Laminate / PVC Panel / Fluted MDF / Wood-look Porcelain Tile).
- ✅ **Enriched endpoints** — `/analyze-region`, `/analyze` (full-image) and `/mock-analyze` all now attach `classification`, `catalogue_matches` (top 5–10), `alternative_systems` to every row.
- ✅ **/library/global upgraded** — status flipped from "beta" → "seeded", returns categories grouped map + total count. Legacy `items` field preserved for backwards compatibility.
- ✅ **Demo project rebuilt** — Warm Modern Bedroom (Unsplash `1595526114035-0d45ed16cfbf`). 9 internally-consistent zones (Headboard Fluted Oak, Bouclé Headboard, Ivory Linen Bedding, Oak Flooring, Wool Rug, Warm White Paint, Walnut Nightstand, Brass Sconce, Sheer Linen Curtains). Every row carries a `pin` (x, y) so the reference image renders numbered pin badges linking to each material card.
- ✅ **Frontend: MaterialsFirstSection v2** — each card shows a pin # badge, classification tag, detected appearance, top catalogue matches with swatch thumbnail + brand + catalogue + code (or fallback) + page (or fallback) + match % pill + expandable Similarity breakdown, alternative material systems section, Indian sourcing note, and per-match "Shortlist" button that captures brand + catalogue + code + match %.
- ✅ **Frontend: RegionSelector** upgraded to render numbered pin badges overlaid on the reference image (linked to the material card indexes; hover-syncs both directions).
- ✅ **Demo page rewired** — the public read-only `/demo` now uses `MaterialsFirstSection` so visitors get catalogue-first UX before signing up.
- ✅ Tests: `tests/test_sprint2_revision_catalogue_first.py` — 10/10 PASS (9 pass + 1 skipped when live-AI quota exceeded). Full backend regression clean (all pre-Sprint-2-Revision failures were pre-existing).

## Sprint 8 — MaterialMatch Studio (PDF Ingestion) (2026-02-27)
- ✅ **Backend endpoints (server.py 5010-5122)**:
  - `POST /api/admin/studio/upload` — multipart PDF upload; PyMuPDF parses each page and creates draft `ke_records` with dominant swatch, material name/code/category heuristics.
  - `GET /api/admin/studio/uploads` — list ingested catalogues.
  - `GET /api/admin/studio/uploads/{id}/records` — per-upload records.
  - `POST /api/admin/studio/records/approve|reject` — batch review actions.
  - `POST /api/admin/studio/uploads/{id}/publish` — publish remaining drafts.
  - `GET /api/admin/studio/library` — live published records (category filter).
- ✅ **Matcher prioritization** — `_STUDIO_INDEXED_RECORDS` (published Studio records) is prepended to the seed catalogue in every match call, so uploaded PDFs surface first.
- ✅ **Knowledge Engine merge** — `/api/admin/knowledge-engine` now merges published Studio records ahead of `SEEDED_CATALOGUE` and returns `source: "Uploaded PDF"` so the admin UI can render an `UPLOADED` badge.
- ✅ **Frontend: `/admin/studio` page** — 4-tab admin workspace (Upload Catalogue / Processing Queue / Review Queue / Published Library) with stats header (Catalogues ingested / Awaiting review / Fully published), drag-and-drop or click-to-upload PDF, per-record swatch preview, bulk-select+approve+reject+publish, category-filtered library.
- ✅ **Header nav** — new admin-only `Studio` link (Rocket icon); Knowledge Engine page adds `Open MaterialMatch Studio` CTA and `UPLOADED` badge on studio-sourced rows.
- ✅ Tests: `tests/test_studio_pipeline.py` — **13/13 PASS** (admin-guard, non-PDF 400, upload→approve→library, KE prioritization, publish-all, reject). E2E frontend flow verified via testing agent (iteration 11).

## Sprint 8.1 — Demo Studio Seed (2026-02-27)
- ✅ **Idempotent boot seed** — on startup, if `ke_records` has no `demo_seed=True` docs, insert 3 synthetic uploads: Asian Paints (4 shades), Greenlam (3 laminates), Kajaria (3 porcelain tiles) — all with `MM-DEMO-*` codes, `demo_seed=True`, `source="Demo catalogue"`.
- ✅ **Ranking preserved** — `_refresh_studio_index()` and `/admin/knowledge-engine` both sort `demo_seed=True` docs after user uploads: order becomes **Uploaded PDF → Demo catalogue → Global Library** (verified via KE search for "Asian Paints": 2 uploaded → 4 demo → 6 seed).
- ✅ **UI badges** — Studio Published Library shows `DEMO` (amber) chip; Knowledge Engine shows `UPLOADED` (black) or `DEMO` (amber) chip next to `PUBLISHED`.
- ✅ **Match verification** — real project analyze-region on a paints context surfaces user-uploaded records at pct:75 alongside seeded ones.

## Sprint 8.2 — Quality Pass (2026-02-27)
Focus: eliminate weak / misleading matches. No new modules, no refactor.
- ✅ **Real-record Studio seed** — fabricated `MM-DEMO-*` records replaced with 14 curated real entries pulled directly from `catalogue_seed.py` (Asian Paints × 6, Greenlam × 4, Kajaria × 4). Legacy records auto-purged on startup via `seed_version` migration.
- ✅ **Matcher quality gates**:
  - `min_overall` 40 → **62** in `_find_catalogue_matches`
  - Removed the `not s["family_match"]` bypass that let sub-40 items through
  - Absent-data defaults for `finish` / `texture` reduced 40 → 25 (stops empty metadata from inflating scores)
  - `family_score` when the AI family is unrecognised now scores neutrally 75 (was 20) so category-approved items can still reach the Best tier on strong colour + keyword evidence.
- ✅ **Brain context expansion** — explicit branches added for `backsplash`, `kitchen wall`, `feature wall` (marble-look vs wood-look), `headboard wall`. Zone context now overrides an AI-supplied `family=Paint` for these clearly non-paint applications.
- ✅ **UI honesty** — MaterialCard now surfaces a recommended match only when ≥ 1 hit clears **75 %**; otherwise renders "No high-confidence catalogue match found." No more silent fallback to weak `possible` / `low` matches.
- ✅ **Badge language** — Studio "Demo" chip renamed to subtle "Reference" (grey) since the records are real; "Uploaded" chip unchanged.
- ✅ Regression: all 13 `test_studio_pipeline.py` still pass.

## Prioritized Backlog

### P1
- Multi-page report layout improvements (page breaks at sections)
- Project rename/delete from dashboard
- Catalogue item pagination/lazy loading for large libraries (50+ items)
- Forgot password / reset password flow

### P2
- Concept Presentation Workspace (rooms + notes + images)
- Client Brief Questionnaire (structured intake form for new projects)
- Team workspaces & sharing
- Vendor/SKU tagging on catalogue items
- Stripe billing (Studio free / Practice $49 paywall)
- Google OAuth social login
- Save/star favorite matches
- Comparison view (side-by-side reference vs matched product)
- Refactor `server.py` (~2900 lines) into `routes/`, `services/`, `models/` modules
- shadcn AlertDialog for affiliate delete confirmation (replace native `window.confirm`)

### P3
- PDF Report Generation / Export
- Vendor Marketplace & live shop addresses
- Object storage migration (out of MongoDB base64)
- Parallel LLM calls (asyncio.gather) for faster analysis on big catalogues
- Server-side PDF generation (ReportLab) for higher fidelity prints
- Multi-tenant brand customization
