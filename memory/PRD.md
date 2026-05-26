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

## Simplified MVP Flow (2026-02-26 — current active path)
- ✅ **New Project flow** simplified to one screen: name + optional client + reference image upload, "Create & continue" button disabled until both name and image are present
- ✅ **Material Analysis page** with reference image + "Analyse Materials" CTA
- ✅ **Mock analysis** backed by deterministic server-side library (`/api/projects/{id}/mock-analyze`), 5–8 rows, schema: `zone, material_type, color, texture, finish, design_style, keywords, confidence`
- ✅ Mock analysis persisted on project (`project.mock_analysis`) and survives reload/revisit
- ✅ Re-analyse button returns same stable rows per project ID
- ✅ Auth guard (401) + missing-reference guard (400) + cross-user 404 all enforced
- ✅ Dashboard cards route directly to `/projects/:id/analysis`
- ✅ 6/6 backend pytest pass + 100% frontend Playwright E2E pass

## Catalogue Match Flow (2026-02-26 — mock)
- ✅ **Find Matches** button per row on Analysis table (9th "Action" column) → `/projects/:id/match?zone={zone}`
- ✅ **Match page** shows: project summary, reference image preview, selected material details card, optional manual prompt textarea, optional PDF + product image uploads, Run Match CTA
- ✅ **Mock match engine** (`POST /api/projects/{id}/match`) generates deterministic top-5 candidates from `MOCK_PRODUCT_LIBRARY` (32 products across wood/stone/fabric/metal/plaster/rug). Files accepted but only `name/type/size` metadata stored — **no PDF parsing**
- ✅ Each match card: product name, catalogue ref, match % (50–98), score label (Strong/Good/Partial/Low), 3 reasons, optional disqualifier (cards 4–5), colored thumbnail block
- ✅ Persisted under `project.match_results.<zone>` — revisit auto-loads saved cards
- ✅ Find Matches button text toggles to "View matches" once that zone has saved results
- ✅ 7/7 backend pytest pass + 100% frontend E2E pass

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
