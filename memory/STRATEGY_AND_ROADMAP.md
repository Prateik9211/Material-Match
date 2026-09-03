# MaterialMatch AI — Strategy & Roadmap

> **This file is the canonical source for product strategy, the "pivot"
> reframing, upcoming priorities, and the technique-taxonomy work in
> progress. `PRD.md` remains the sprint-by-sprint execution log.
> Future sessions: read this file BEFORE PRD.md to load context.**

Last major update: 2026-02-14 (session summary + strategic pivot).

---

## 2026-02-14 — Session Summary: Hybrid Detection Pipeline, Region Expansion, Material View & Strategic Pivot

### What was built and shipped this session
- **New hybrid detection pipeline** (`intelligence/scene_segmentation.py`):
  SAM3 (via Roboflow) for object/boundary detection, GPT-4o-mini
  (`generate_swatch_dna`) for material classification — replacing SAM3's
  own weaker material-concept segmentation. Validated at **96.5%
  detection/material-plausibility accuracy across 31 real test images**
  (21 general + 10 deliberately hard cases: low-contrast materials,
  reflective surfaces).
- Wired into the LIVE user-facing `/analyze-region` flow (previously
  only in an admin test tool) — this was a critical fix, since the live
  flow had NO object detection before (whole image treated as one
  material).
- Fixed a major **consistency bug**: three different code paths were
  classifying materials differently for the same image region (manual
  selection vs full scene analysis vs an old fallback) — consolidated
  to one shared classification path, with a permanent regression test
  guarding against recurrence.
- Fixed multiple real accuracy bugs found via live user testing:
  - Cross-category catalogue matching (wall paint matching to laminate
    catalogue — root cause was overly-broad multi-family search from
    an earlier fix).
  - Cabinets defaulting to "Paint" family instead of "Laminate"
    (object-aware bias fix).
  - Duplicate/fragmented pins on one physical material run
    (similarity-based merge across the whole image, not just
    spatially-adjacent).
  - Products (track lights, hoods, refrigerators, cushions / pillows /
    mattresses / plants) incorrectly getting material readings instead
    of routing to Products-only.
- **User-uploadable catalogues** (Scope #1): users can now upload their
  own supplier PDF, processed through the same real ingestion pipeline
  as the admin Studio flow, scoped privately to that user, with a clear
  two-button choice (Check Admin Library vs Check My Catalogue) —
  never silently merged. Proven with real cross-scope isolation tests.
- **Multi-region architecture** (India / US / UAE): the previous
  "India/Global" toggle was purely cosmetic — built real region-scoped
  catalogue data, retrieval filtering, and a real region selector.
  Region isolation proven with live tests. US / UAE currently have zero
  catalogue content (expected — infrastructure ready, content is
  founder's next step).
- **E-commerce / product similarity search** (SerpApi Google Lens
  integration): feasibility-tested and then built for real — "Similar
  items" (not exact-match) framing, quality-gating to skip bad crops,
  wired into Products & Fixtures results. Known limitation: works well
  on clean product crops, less reliably on ambient room photos (needs
  a text-search fallback, not yet built).
- **Material View feature** (in progress as of this entry): generates
  a photorealistic textured visualization from flat catalogue swatch
  scans, using Gemini "Nano Banana" (image-to-image, grounded on the
  real swatch as reference — NOT GPT-Image-1, which was proven via
  feasibility test to hallucinate on unfamiliar materials, e.g.
  marble → plain stucco). Being wired into the publish flow +
  backfilled across the existing 264 published records, with a toggle
  on match cards between "Catalogue View" and "Material View".
- Admin / product-hygiene work: delete / replace for projects and
  images, a real signup counter, a filtered real-vs-test user list, a
  review / testimonial system (goes live instantly on submission,
  admin can hide), Google Analytics wired in, a whitelist-protected
  test-account purge endpoint (deployed to production, founder needs
  to trigger it manually via `/admin/users`).

---

### THE KEY STRATEGIC PIVOT (important — read this before making UX / matching decisions going forward)

The founder identified a **structural limit, not just an accuracy
problem**: for many material categories, there is genuinely NO reliable
visual tell between different real-world techniques that achieve the
same look (e.g. lime wash paint vs lime plaster vs Araish plaster vs
textured wallpaper all can look visually identical in a photo;
laminate vs veneer vs PU vs thermo-laminate wood finishes are
similarly visually indistinguishable). Additionally, catalogue photos
are flat PDF scans and can never fully capture real physical texture.

**Decision: pivot away from "detect one material, find one catalogue
match" toward "detect the visual finish, then educate the user on the
real range of techniques that could achieve it (with rough per-sq-ft
cost ranges), THEN connect to vendors for whichever techniques have
catalogue coverage."**

This reframes catalogue misses from failures into genuine educational
value, and is more honest given the underlying ambiguity is real and
permanent (not fixable by a smarter model). A technique TAXONOMY (per
material category: real distinct techniques + whether any genuine
visual tell exists between them) is being built collaboratively
between the founder (using his 12+ years architecture expertise) and
Claude, category by category, as a **prerequisite for implementing
this — NOT something to be invented by AI alone**.

#### Taxonomy progress so far
- **Wall / lime finishes**: lime wash paint, lime plaster, Araish
  plaster, textured wallpaper, Venetian plaster, tadelakt,
  textured / stucco paint — **NO reliable visual tell** between any
  of these. Show all options equally.
- **Wood-look finishes**: laminate, veneer, PU / lacquer, thermo-laminate
  (budget PU substitute) — **NO reliable visual tell**, even on close
  inspection. Show all options equally.
- **Flooring** (in progress): wood-look tiles, marble-look tiles,
  Kota / Shabadi stone-look, microconcrete. **IMPORTANT — this
  category DOES have a real, usable visual signal**: tile look-alikes
  reveal themselves via regular / repeating grout-line grids at fixed
  intervals (manufactured tile sizing); real natural stone (Kota,
  Shabadi) lacks this mechanical regularity since it can be custom-cut.
  Microconcrete has no convincing look-alike — safe to give a
  confident single match for it. Recommendation: for flooring
  specifically, let the AI make an informed primary guess based on
  grid regularity, then still show full option set underneath — NOT
  pure "show everything equally" like the other categories. US / UAE
  flooring options (vinyl, SPC / LVT etc.) still need research —
  founder's direct expertise is India-focused; Claude to research
  US / UAE equivalents separately.
- **Costing**: founder plans a simple per-sq-ft RANGE (not a single
  number) shown alongside technique options, deliberately signaling
  "indicative, verify before quoting" — avoids the trust risk of a
  wrong specific price. Real vendor-by-vendor pricing collection is a
  planned FUTURE step (not started); current ranges would be
  founder's own market knowledge, captured deliberately rather than
  AI-invented.

---

### Upcoming priorities (in order, per founder's stated goal)

1. **HARD DEADLINE: August 30 — Emergent building-contest voting
   deadline.** Founder submitted MaterialMatch, missed top 25 in a
   previous round, wants top 25 this time. **Explicit instruction: the
   PIVOT strategy itself must be implemented and experienceable by
   judges before the deadline, not deferred to post-competition.**
2. Finish the technique taxonomy (flooring in progress, then remaining
   categories) — collaborative, ongoing, zero cost.
3. Build the pivot into the real product: detect → show technique
   options + cost ranges → connect to vendor coverage where it exists.
4. QA sweep + production test-account purge (endpoint is live, needs
   founder to click the button on `/admin/users`).
5. Mobile responsiveness — **scoped to Landing page, interactive demo,
   and core Analysis flow first** (not the whole app) — since that's
   what a contest judge will most likely experience, often on a phone.
6. "Scan a Material" camera-capture mode — **IMPORTANT ARCHITECTURAL
   NOTE**: this should be a SEPARATE mode from room-scene analysis,
   skipping SAM3 object-detection entirely and running material
   classification directly on the whole photo — because a close-up
   texture-only photo has no "object" for SAM3 to detect against,
   unlike the room photos the existing pipeline was tuned on.

---

### Longer-term roadmap (post-competition, not to be pulled forward)
- Real vendor-by-vendor price data collection (replacing rough ranges).
- **Vendor monetization**: paid featured / priority catalogue placement,
  pay-per-qualified-lead — the same model as funded competitor
  Mattoboard (Home Depot Ventures-backed), which the founder is aware
  of as both validation and honest competitive context.
- BIM-ready texture licensing to vendors as a premium offering —
  deliberately sequenced AFTER real vendor relationships exist, not
  before.
- Possible adjacent-market licensing of the core detection engine
  (e.g. insurance / property-restoration).
- A structured correction / training-log tool (admin-only) to
  accumulate founder's expert corrections over time — informal version
  already happening via live bug-fixing tonight; formal version not
  yet built.

---

### Known open items / backlog (not urgent, don't forget)
- Trigger the production test-account purge (~82 artifacts) via
  `/admin/users` on materialmatches.com.
- Add a scene-type sanity guard (stop the pipeline hallucinating
  objects on non-interior photos like exteriors).
- Per-match thumbs-up / down feedback for future calibration.
- Let founder pin featured reviews to the top of the landing
  testimonials section.
- "Verified by AI Vision" filter for Published Library (mentioned
  earlier, not built).
- Text-search fallback for e-commerce matching on ambient (non-close-up)
  product photos.
- `server.py` has grown very large (~9,200+ lines) — flagged as due
  for a split / refactor eventually, not urgent.
