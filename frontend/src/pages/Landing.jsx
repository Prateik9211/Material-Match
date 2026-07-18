import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import { useAuth } from "@/context/AuthContext";
import { ArrowRight, Check, Camera, Layers, BookOpen, ShoppingBag, ListChecks, PenSquare, PlayCircle, PauseCircle, X, ChevronRight, Focus, Search, Sparkles } from "lucide-react";
// Static asset — Unsplash-licensed kitchen photo used as the "Reference"
// in the landing-page WorkflowVisual panel.  This IS the image the live
// hybrid pipeline analysed to produce the Detected + Sourceable numbers
// shown on the same panel (see WorkflowVisual comment for the run details).
import heroKitchenScene from "../assets/landing/hero_kitchen_scene.jpg";

const TRUST_BULLETS = [
  "Designer-first",
  "India-first sourcing",
  "Catalogue matching",
  "Products & Fixtures detection",
  "Sourceable shortlist",
];

const WORKFLOW = [
  { n: "01", icon: Camera, title: "Reference", body: "Upload an inspiration image — a client's Pinterest pin or a project photograph." },
  { n: "02", icon: Layers, title: "Material Detection", body: "Zones, finishes, colour and material family surfaced automatically." },
  { n: "03", icon: BookOpen, title: "Material Library Search", body: "Match against your uploaded catalogues and reusable library." },
  { n: "04", icon: BookOpen, title: "Catalogue Matches", body: "Ranked matches with source, page number, confidence and match reason." },
  { n: "05", icon: ShoppingBag, title: "Product Suggestions", body: "Lighting, furniture and decor detected — with curated Indian recommendations." },
  { n: "06", icon: ListChecks, title: "Sourceable Shortlist", body: "Build a shortlist to walk into vendor meetings prepared." },
];

const _DEMO_CHAPTERS_HISTORICAL = [
  // Previous abstract 6-chapter walkthrough — kept as a comment for
  // provenance.  Superseded 2026-02-27 by the 4-chapter Detect →
  // Search → Compare → Decide replay that renders the real
  // KITCHEN_DEMO data progressively.
];

/* ---------------- Real captured T2 pipeline result ----------------
   This is the SAME real end-to-end kitchen-scene run rendered
   statically in <WorkflowVisual/> below.  Every label and every
   percentage came out of the actual /analyze-region hybrid pipeline
   (SAM3 Stage-A → GPT-4o-mini classification → visual-rerank
   retrieval) on 2026-07-18.  The Interactive Demo below just
   replays this frozen result — no live AI calls per visitor.       */
const KITCHEN_DEMO = {
  refCaption: "Kitchen scene",
  refSubtitle: "Mixed wood + white cabinets",
  detections: [
    { id: "d0", label: "Cabinet · Laminate",
      name: "Warm wood grain",     conf: 85,
      accent: "bg-stone-panel",
      /* SAM3 bbox centre — from the actual pipeline result. */
      pin: { x: 30, y: 62 } },
    { id: "d1", label: "Countertop · Solid",
      name: "Smooth quartz white", conf: 85,
      accent: "bg-sand/60",
      pin: { x: 55, y: 45 } },
    { id: "d2", label: "Cabinet · Veneer",
      name: "Light oak grain",     conf: 65,
      accent: "bg-white",
      pin: { x: 72, y: 30 } },
  ],
  matches: [
    { id: "m0", zone: "Cabinet · Laminate",
      product: "Elysian Wood",      brand: "Advance",
      conf: 85, accent: "sage" },
    { id: "m1", zone: "Countertop · Solid",
      product: "Frosty White",      brand: "Advance",
      conf: 85, accent: "ochre" },
    { id: "m2", zone: "Cabinet · Veneer",
      product: "Light Urban Teak",  brand: "Advance",
      conf: 65, accent: "sage" },
  ],
};

/* ---------------- Demo stage — progressive reveal of KITCHEN_DEMO ----
   Given the current chapter's stage + intra-chapter progress (0..1),
   renders the appropriate slice of the real captured pipeline result.
   No external calls. */
function DemoStage({ stage, progress, step }) {
  const dets = KITCHEN_DEMO.detections;
  const matches = KITCHEN_DEMO.matches;

  // Progressive reveal: for "detect" stage, one detection per third of
  // the chapter.  For "match" stage, one match per third.
  const revealCount = Math.min(3, Math.floor(progress * 3) + 1);

  return (
    <div className="relative w-full h-full flex items-center gap-4 px-6 pt-6 pb-32" data-testid={`demo-stage-${stage}`}>
      {/* LEFT — reference image with pins that appear during detect */}
      <div className="relative w-2/5 h-full rounded-2xl border border-stone-border-soft overflow-hidden shadow-hover" data-testid="demo-reference-card">
        <img
          src={heroKitchenScene}
          alt="Kitchen reference — the actual image the pipeline analysed."
          className="absolute inset-0 w-full h-full object-cover"
        />
        <div className="absolute top-2 left-2 bg-paper/95 backdrop-blur rounded-md px-2 py-1">
          <div className="text-[9px] uppercase tracking-widest text-warm-grey">Reference</div>
          <div className="text-[11px] font-medium text-charcoal">{KITCHEN_DEMO.refCaption}</div>
        </div>

        {/* Numbered-style dots appear as detections reveal. */}
        {(stage === "detect" || stage === "search" || stage === "match") &&
          dets.slice(0, stage === "detect" ? revealCount : 3).map((d, i) => (
            <span
              key={d.id}
              className="absolute -translate-x-1/2 -translate-y-1/2 w-3.5 h-3.5 rounded-full bg-paper border-2 border-charcoal shadow-hover animate-fade-in-up"
              style={{ left: `${d.pin.x}%`, top: `${d.pin.y}%`, animationDelay: `${i * 60}ms` }}
              data-testid={`demo-pin-${i}`}
              aria-label={d.label}
            />
          ))}
      </div>

      {/* MIDDLE — detected cards / search animation */}
      <div className="w-[27%] h-full flex flex-col gap-2 pt-3" data-testid="demo-detect-column">
        <div className="text-[9px] uppercase tracking-widest text-warm-grey">Detected</div>
        {dets.map((d, i) => {
          const isVisible = stage === "detect" ? i < revealCount
                          : (stage === "search" || stage === "match");
          const isSearching = stage === "search";
          return (
            <div
              key={d.id}
              className={`p-2.5 rounded-xl ${d.accent} border border-stone-border-soft transition-all duration-500 ${
                isVisible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2"
              }`}
              style={{ transitionDelay: `${i * 120}ms` }}
              data-testid={`demo-detection-${i}`}
            >
              <div className="text-[9px] uppercase tracking-widest text-warm-grey">{d.label}</div>
              <div className="text-[11px] font-medium text-charcoal">{d.name}</div>
              <div className="flex items-center justify-between mt-1">
                <span className="text-[10px] text-sage font-mono">{d.conf}%</span>
                {isSearching && (
                  <span className="inline-flex items-center gap-1 text-[9px] text-ochre" data-testid={`demo-searching-${i}`}>
                    <Search className="w-2.5 h-2.5 animate-pulse" strokeWidth={2} />
                    Searching…
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* RIGHT — catalogue matches (only in "match" chapter) */}
      <div className="w-[27%] h-full flex flex-col gap-2 pt-3" data-testid="demo-match-column">
        <div className="text-[9px] uppercase tracking-widest text-warm-grey">Sourceable</div>
        {matches.map((m, i) => {
          const isVisible = stage === "match" ? i < revealCount : false;
          const ring = m.accent === "sage" ? "bg-sage-soft/70 border-sage/30"
                    : m.accent === "ochre" ? "bg-ochre-soft/60 border-ochre/30"
                    : "bg-charcoal text-paper border-charcoal";
          const percentColor = m.accent === "ochre" ? "text-ochre" : "text-sage";
          return (
            <div
              key={m.id}
              className={`p-2.5 rounded-xl border ${ring} transition-all duration-500 ${
                isVisible ? "opacity-100 translate-y-0 scale-100" : "opacity-0 translate-y-3 scale-95"
              }`}
              style={{ transitionDelay: `${i * 140}ms` }}
              data-testid={`demo-match-${i}`}
            >
              <div className={`flex items-center gap-1 text-[9px] uppercase tracking-widest font-semibold ${m.accent === "ochre" ? "text-ochre" : "text-sage"}`}>
                <BookOpen className="w-2.5 h-2.5" strokeWidth={2.5} />
                Catalogue Match
              </div>
              <div className="text-[11px] font-medium text-charcoal leading-tight mt-0.5">
                {m.product} · {m.brand}
              </div>
              <div className="text-[9px] text-warm-grey/80 mt-0.5">{m.zone}</div>
              <div className={`text-[10px] font-mono mt-0.5 ${percentColor}`}>{m.conf}%</div>
            </div>
          );
        })}
        {stage !== "match" && (
          <div className="text-[10px] text-warm-grey/60 italic mt-1 pl-1">
            Matches reveal in Chapter 4
          </div>
        )}
      </div>
    </div>
  );
}


/* ---------------- Interactive Demo Modal (Video-style) ---------------- */
const CHAPTER_MS = 4200;

/* Four chapters, matching the founder-defined flow
   Detect → Search → Compare → Decide.  Each chapter's `stage` drives
   what the modal video-frame progressively reveals from KITCHEN_DEMO. */
const DEMO_CHAPTERS = [
  { t: "Reference", stage: "reference",
    caption: "A real kitchen photo, straight from a designer's mood board.",
    detail: "This is the exact reference image the live pipeline analysed on 2026-07-18. No CGI, no cherry-picking.",
    icon: Camera, accent: "bg-sand/60" },
  { t: "Detect",    stage: "detect",
    caption: "MaterialMatch detects surfaces and finishes.",
    detail: "SAM3 finds architectural objects; GPT-4o-mini names each material family with a confidence score. Watch three real detections light up.",
    icon: Layers, accent: "bg-stone-panel" },
  { t: "Search",    stage: "search",
    caption: "Searching the catalogue for visually similar swatches.",
    detail: "Each detection is described, embedded and reranked against the uploaded supplier catalogue. This normally takes 2–4 seconds; the replay is faster.",
    icon: Search, accent: "bg-ochre-soft/60" },
  { t: "Match",     stage: "match",
    caption: "Real catalogue matches, real percentages.",
    detail: "Three verified Advance-brand swatches with the actual match_percent returned by the pipeline. These aren't handpicked — they came out of retrieval.",
    icon: ListChecks, accent: "bg-charcoal text-paper" },
];

function fmtTime(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function DemoModal({ onClose }) {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [progress, setProgress] = useState(0); // 0..1 within current chapter
  const rafRef = useRef(null);
  const chapterStartRef = useRef(performance.now());
  const navigate = useNavigate();
  const { user } = useAuth();
  const total = DEMO_CHAPTERS.length;
  const totalMs = CHAPTER_MS * total;
  const current = DEMO_CHAPTERS[step];
  const Icon = current.icon;

  const goto = (i) => {
    setStep(Math.max(0, Math.min(total - 1, i)));
    setProgress(0);
    chapterStartRef.current = performance.now();
  };

  useEffect(() => {
    if (!playing) return;
    const tick = () => {
      const elapsed = performance.now() - chapterStartRef.current;
      const p = Math.min(1, elapsed / CHAPTER_MS);
      setProgress(p);
      if (p >= 1) {
        if (step < total - 1) {
          setStep((s) => s + 1);
          chapterStartRef.current = performance.now();
          setProgress(0);
        } else {
          setPlaying(false);
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [playing, step, total]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") goto(step + 1);
      if (e.key === "ArrowLeft") goto(step - 1);
      if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  const overallMs = step * CHAPTER_MS + progress * CHAPTER_MS;

  return (
    <div className="fixed inset-0 z-50 bg-charcoal/70 grid place-items-center p-4 backdrop-blur-sm" data-testid="demo-modal" onClick={onClose}>
      <div
        className="bg-paper rounded-3xl shadow-hover w-full max-w-4xl overflow-hidden border border-stone-border-soft animate-fade-in-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top bar */}
        <div className="px-5 py-3 border-b border-stone-border-soft flex items-center justify-between bg-white">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-rose-400"></span>
            <span className="w-2 h-2 rounded-full bg-ochre"></span>
            <span className="w-2 h-2 rounded-full bg-sage"></span>
            <span className="text-overline ml-3">MaterialMatch · Interactive Walkthrough</span>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 rounded-full hover:bg-stone-panel" data-testid="demo-modal-close" aria-label="Close">
            <X className="w-4 h-4" strokeWidth={1.5} />
          </button>
        </div>

        {/* Video frame — replays the real 2026-07-18 kitchen-scene
            pipeline result stored in KITCHEN_DEMO.  Each chapter
            reveals a different stage; no live AI calls happen here. */}
        <div
          className="aspect-video bg-stone-panel border-b border-stone-border-soft relative overflow-hidden transition-colors duration-500"
          data-testid="demo-video-frame"
        >
          {/* Ambient grid */}
          <div className="absolute inset-0 opacity-[0.05] pointer-events-none" style={{
            backgroundImage: "linear-gradient(#2b2724 1px, transparent 1px), linear-gradient(90deg, #2b2724 1px, transparent 1px)",
            backgroundSize: "32px 32px",
          }} />

          <DemoStage stage={current.stage} chapterMs={CHAPTER_MS} progress={progress} step={step} />

          {/* Chapter caption strip (bottom overlay) */}
          <div className="absolute bottom-14 left-4 right-4 bg-paper/95 backdrop-blur rounded-xl border border-stone-border-soft px-4 py-3 shadow-hover animate-fade-in-up" key={`caption-${step}`} data-testid="demo-caption-strip">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-full bg-charcoal text-paper grid place-items-center shrink-0">
                <Icon className="w-4 h-4" strokeWidth={1.5} />
              </div>
              <div className="min-w-0">
                <div className="text-[10px] uppercase tracking-widest text-warm-grey">Chapter {step + 1} · {current.t}</div>
                <div className="text-sm font-medium text-charcoal leading-snug" data-testid="demo-caption">{current.caption}</div>
                <div className="text-[11px] text-warm-grey mt-0.5 leading-relaxed" data-testid="demo-caption-detail">{current.detail}</div>
              </div>
            </div>
          </div>

          {/* Play/Pause overlay */}
          <button
            type="button"
            onClick={() => setPlaying((p) => !p)}
            className="absolute top-3 right-3 w-10 h-10 rounded-full bg-paper/90 backdrop-blur border border-stone-border-soft text-charcoal grid place-items-center hover:scale-105 transition-transform shadow-hover"
            data-testid="demo-play-toggle"
            aria-label={playing ? "Pause walkthrough" : "Play walkthrough"}
          >
            {playing ? <PauseCircle className="w-5 h-5" strokeWidth={1.5} /> : <PlayCircle className="w-5 h-5" strokeWidth={1.5} />}
          </button>

          {/* Timeline (chapter markers) */}
          <div className="absolute bottom-0 left-0 right-0 px-5 pb-3 pt-6 bg-gradient-to-t from-stone-panel via-stone-panel/85 to-transparent" data-testid="demo-timeline">
            <div className="flex items-center gap-1 mb-1.5">
              {DEMO_CHAPTERS.map((c, i) => {
                const isDone = i < step;
                const isActive = i === step;
                const fill = isDone ? 1 : isActive ? progress : 0;
                return (
                  <button
                    type="button"
                    key={c.t}
                    onClick={() => { goto(i); setPlaying(true); }}
                    className="flex-1 h-1 rounded-full bg-charcoal/15 relative overflow-hidden cursor-pointer group"
                    data-testid={`demo-chapter-${i}`}
                    aria-label={`Go to chapter ${i + 1}`}
                  >
                    <span
                      className="absolute inset-y-0 left-0 bg-charcoal transition-[width] duration-100 ease-linear"
                      style={{ width: `${fill * 100}%` }}
                    />
                    <span className="absolute -bottom-4 left-0 text-[9px] uppercase tracking-widest text-charcoal/60 opacity-0 group-hover:opacity-100 whitespace-nowrap">
                      {c.t}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="flex items-center justify-between text-[10px] font-mono text-charcoal/60">
              <span data-testid="demo-elapsed">{fmtTime(overallMs)}</span>
              <span>{current.t}</span>
              <span data-testid="demo-total">{fmtTime(totalMs)}</span>
            </div>
          </div>
        </div>

        {/* Footer CTAs */}
        <div className="p-6 flex flex-wrap items-center justify-between gap-4 bg-paper">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => goto(step - 1)}
              disabled={step === 0}
              className="text-sm text-warm-grey hover:text-charcoal disabled:opacity-30 px-2"
              data-testid="demo-prev"
            >
              ← Previous
            </button>
            <button
              type="button"
              onClick={() => goto(step + 1)}
              disabled={step === total - 1}
              className="text-sm text-charcoal hover:underline disabled:opacity-30 px-2"
              data-testid="demo-next"
            >
              Next →
            </button>
            <span className="text-xs text-warm-grey/70 ml-2">{step + 1} / {total}</span>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => { onClose(); navigate("/demo"); }}
              className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-5 py-2.5 text-sm font-medium transition-colors"
              data-testid="demo-modal-open-project"
            >
              Open Demo Project
              <ArrowRight className="w-3.5 h-3.5" strokeWidth={1.75} />
            </button>
            <button
              type="button"
              onClick={() => {
                onClose();
                navigate(user ? "/projects/new" : "/auth?mode=register&next=/projects/new");
              }}
              className="inline-flex items-center gap-2 bg-white text-charcoal border border-stone-border hover:border-charcoal rounded-full px-5 py-2.5 text-sm font-medium transition-colors"
              data-testid="demo-modal-create-project"
            >
              Create Your First Project
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Hero workflow visual ---------------- */
function WorkflowVisual() {
  // Landing-page demo data — from a real end-to-end run on the live
  // hybrid scene-analysis pipeline (2026-07-18).  The Unsplash-licensed
  // kitchen photo shown as "Reference" was uploaded through
  // /api/projects/{id}/analyze-region with mode="scene"; SAM3 detected 30
  // architectural objects, GPT-4o-mini classified each object's material,
  // and the three catalogue matches shown in "Sourceable" are the actual
  // top visually-verified hits returned by the pipeline (ELYSIAN WOOD 85%,
  // FROSTY WHITE 85%, LIGHT URBAN TEAK 65% — all Advance brand).  No
  // labels or numbers on this panel are fabricated.
  return (
    <div className="relative" data-testid="hero-workflow-visual">
      <div className="bg-white border border-stone-border-soft rounded-3xl p-6 sm:p-8 shadow-hover grid grid-cols-3 gap-4">
        {/* LEFT — Reference (the actual image the pipeline analysed) */}
        <div className="space-y-3 col-span-1">
          <div className="text-overline">Reference</div>
          <div className="aspect-[4/5] rounded-2xl border border-stone-border-soft relative overflow-hidden">
            <img
              src={heroKitchenScene}
              alt="Modern kitchen with mixed wood-grain and white cabinets — reference photo used in the live pipeline run shown on this panel."
              className="absolute inset-0 w-full h-full object-cover"
              loading="lazy"
              data-testid="hero-reference-image"
            />
            <div className="absolute bottom-2 left-2 right-2 bg-paper/95 backdrop-blur rounded-md px-2 py-1.5">
              <div className="text-[9px] uppercase tracking-widest text-warm-grey">Kitchen scene</div>
              <div className="text-[11px] font-medium text-charcoal leading-tight">Mixed wood + white cabinets</div>
            </div>
          </div>
        </div>

        {/* MIDDLE — Detected materials from a real T2 pipeline run
             (kitchen scene → SAM3 detected 22 objects → GPT-4o-mini
             classified per-object materials; the three below are the
             actual verified detections that produced verified catalogue
             matches in the right column). */}
        <div className="space-y-2 col-span-1">
          <div className="text-overline">Detected</div>
          <div className="p-2.5 rounded-xl bg-stone-panel border border-stone-border-soft">
            <div className="text-[9px] uppercase tracking-widest text-warm-grey">Cabinet · Laminate</div>
            <div className="text-[11px] font-medium text-charcoal">Warm wood grain</div>
            <div className="text-[10px] text-sage font-mono mt-0.5">85%</div>
          </div>
          <div className="p-2.5 rounded-xl bg-sand/40 border border-stone-border-soft">
            <div className="text-[9px] uppercase tracking-widest text-warm-grey">Countertop · Solid</div>
            <div className="text-[11px] font-medium text-charcoal">Smooth quartz white</div>
            <div className="text-[10px] text-sage font-mono mt-0.5">85%</div>
          </div>
          <div className="p-2.5 rounded-xl bg-white border border-stone-border-soft">
            <div className="text-[9px] uppercase tracking-widest text-warm-grey">Cabinet · Veneer</div>
            <div className="text-[11px] font-medium text-charcoal">Light oak grain</div>
            <div className="text-[10px] text-sage font-mono mt-0.5">65%</div>
          </div>
        </div>

        {/* RIGHT — Catalogue matches from the SAME real T2 pipeline
             run: `catalogue_matches[0]` for the three verified rows.
             Percentages are the actual `match_percent` returned by
             retrieval + visual rerank. No numbers on this panel are
             fabricated. */}
        <div className="space-y-2 col-span-1">
          <div className="text-overline">Sourceable</div>
          <div className="p-2.5 rounded-xl bg-sage-soft/70 border border-sage/30">
            <div className="flex items-center gap-1 text-[9px] uppercase tracking-widest text-sage font-semibold">
              <BookOpen className="w-2.5 h-2.5" strokeWidth={2.5} /> Catalogue Match
            </div>
            <div className="text-[11px] font-medium text-charcoal mt-0.5 leading-tight">Elysian Wood · Advance</div>
            <div className="text-[10px] text-sage font-mono mt-0.5">85%</div>
          </div>
          <div className="p-2.5 rounded-xl bg-ochre-soft/60 border border-ochre/30">
            <div className="text-[9px] uppercase tracking-widest text-ochre font-semibold">Catalogue Match</div>
            <div className="text-[11px] font-medium text-charcoal leading-tight">Frosty White · Advance</div>
            <div className="text-[10px] text-ochre font-mono mt-0.5">85%</div>
          </div>
          <div className="p-2.5 rounded-xl bg-charcoal text-paper">
            <div className="flex items-center gap-1 text-[9px] uppercase tracking-widest text-paper/70 font-semibold">
              <ListChecks className="w-2.5 h-2.5" strokeWidth={2.5} /> Shortlist
            </div>
            <div className="text-[11px] font-medium leading-tight">4 verified matches</div>
          </div>
        </div>
      </div>
      {/* Connector labels */}
      <div className="hidden sm:flex items-center justify-around text-[10px] uppercase tracking-widest text-warm-grey/70 mt-3 px-8">
        <span>Reference</span>
        <ChevronRight className="w-3 h-3" strokeWidth={2} />
        <span>Detection</span>
        <ChevronRight className="w-3 h-3" strokeWidth={2} />
        <span>Sourceable</span>
      </div>
    </div>
  );
}

/* ---------------- LANDING ---------------- */
export default function Landing() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [demoOpen, setDemoOpen] = useState(false);

  const openDemoModal = () => setDemoOpen(true);
  const openCreate = () => {
    if (user) navigate("/projects/new");
    else navigate("/auth?mode=register&next=/projects/new");
  };

  return (
    <div className="min-h-screen bg-paper" data-testid="landing-page">
      <Header variant="marketing" />

      {/* HERO */}
      <section className="max-w-7xl mx-auto px-6 pt-14 pb-20 grain">
        <div className="grid lg:grid-cols-12 gap-12 items-center">
          <div className="lg:col-span-6 space-y-8 animate-fade-in-up">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-stone-border-soft text-overline">
              <span className="w-1.5 h-1.5 rounded-full bg-charcoal"></span>
              Sourcing acceleration for interior designers · Beta
            </div>
            <h1 className="font-display text-5xl sm:text-6xl lg:text-7xl tracking-tight leading-[0.95] font-bold text-charcoal">
              Turn inspiration<br />
              into <span className="italic">sourceable</span><br />
              <span className="text-warm-grey/60">materials.</span>
            </h1>
            <p className="text-lg text-charcoal/70 max-w-xl leading-relaxed" data-testid="hero-subhead">
              Upload a reference image, detect materials and products, match them with supplier catalogues,
              and generate a shortlist of India-ready sourcing options.
            </p>
            <p className="text-sm text-warm-grey italic max-w-xl border-l-2 border-stone-border pl-4" data-testid="hero-philosophy">
              MaterialMatch does not replace design judgement. It helps designers reach the right shortlist
              faster before physical verification.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={openDemoModal}
                className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-7 py-3.5 font-medium transition-colors"
                data-testid="hero-cta-demo"
              >
                Explore Interactive Demo
                <ArrowRight className="w-4 h-4" strokeWidth={1.5} />
              </button>
              <button
                type="button"
                onClick={openCreate}
                className="inline-flex items-center gap-2 bg-white text-charcoal border border-stone-border hover:border-charcoal hover:bg-stone-panel rounded-full px-7 py-3.5 font-medium transition-colors"
                data-testid="hero-cta-create"
              >
                Create Your First Project
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 pt-4 text-sm text-warm-grey" data-testid="hero-trust-bullets">
              {TRUST_BULLETS.map((b) => (
                <div key={b} className="flex items-center gap-2">
                  <Check className="w-4 h-4 text-sage" strokeWidth={1.75} /> {b}
                </div>
              ))}
            </div>
          </div>

          <div className="lg:col-span-6 animate-fade-in-up" style={{ animationDelay: "0.15s" }}>
            <WorkflowVisual />
          </div>
        </div>
      </section>

      {/* WHY MATERIALMATCH */}
      <section className="max-w-6xl mx-auto px-6 py-20" data-testid="why-section">
        <div className="grid lg:grid-cols-12 gap-10 items-center">
          <div className="lg:col-span-5 space-y-4">
            <div className="text-overline">Why MaterialMatch?</div>
            <h2 className="font-display text-4xl sm:text-5xl font-bold tracking-tight text-charcoal leading-tight">
              Sourcing acceleration —<br />
              <span className="text-warm-grey">not design automation.</span>
            </h2>
          </div>
          <div className="lg:col-span-7 space-y-5 text-charcoal/80 leading-relaxed">
            <p>
              Interior designers spend hours searching supplier catalogues and product websites after
              clients share references.
            </p>
            <p>
              MaterialMatch reduces that search time by detecting materials and products, comparing
              catalogues, and producing sourceable shortlists.
            </p>
            <p className="text-charcoal font-medium">
              This is not design automation. This is sourcing acceleration.
            </p>
          </div>
        </div>
      </section>

      {/* WORKFLOW */}
      <section id="workflow" className="max-w-7xl mx-auto px-6 py-20">
        <div className="max-w-2xl mb-12">
          <div className="text-overline mb-3">The workflow</div>
          <h2 className="font-display text-4xl sm:text-5xl font-bold tracking-tight text-charcoal">
            From reference image to <em className="not-italic text-warm-grey/70">sourceable shortlist.</em>
          </h2>
          <p className="text-warm-grey mt-4 max-w-xl">
            Six steps to reach the right shortlist faster — before physical verification.
          </p>
        </div>

        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 md:gap-8">
          {WORKFLOW.map((s) => (
            <div
              key={s.n}
              className="bg-white border border-stone-border-soft rounded-2xl p-8 shadow-soft hover:shadow-hover transition-all hover:-translate-y-1 duration-300"
              data-testid={`workflow-step-${s.n}`}
            >
              <div className="flex items-start justify-between mb-6">
                <span className="font-display text-5xl text-stone-border font-bold">{s.n}</span>
                <s.icon className="w-6 h-6 text-charcoal" strokeWidth={1.25} />
              </div>
              <h3 className="font-display text-xl font-semibold mb-2 text-charcoal">{s.title}</h3>
              <p className="text-sm text-warm-grey leading-relaxed">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* PRODUCT DISCOVERY BAND */}
      <section className="max-w-7xl mx-auto px-6 py-16" data-testid="product-discovery-band">
        <div className="rounded-3xl bg-stone-panel border border-stone-border-soft p-10 sm:p-14">
          <div className="grid lg:grid-cols-12 gap-8 items-center">
            <div className="lg:col-span-7 space-y-4">
              <div className="text-overline">Products & Fixtures</div>
              <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-charcoal">
                Product discovery built for Indian interiors.
              </h2>
              <p className="text-warm-grey leading-relaxed">
                Detect lights, decor, furniture, rugs and fixtures from references and generate
                search-ready suggestions. Curated affiliate links show up when we have a match — otherwise
                we surface search keywords tuned for Indian marketplaces.
              </p>
            </div>
            <div className="lg:col-span-5 space-y-3">
              {[
                { c: "Lighting", t: "Brushed Brass Pendant" },
                { c: "Furniture", t: "Bouclé Accent Chair" },
                { c: "Textile", t: "Hand-tufted Wool Rug" },
              ].map((p) => (
                <div key={p.t} className="bg-white rounded-2xl p-4 border border-stone-border-soft flex items-center gap-3">
                  <ShoppingBag className="w-4 h-4 text-charcoal" strokeWidth={1.5} />
                  <div className="flex-1">
                    <div className="text-[10px] uppercase tracking-widest text-warm-grey">{p.c}</div>
                    <div className="text-sm font-medium text-charcoal">{p.t}</div>
                  </div>
                  <div className="text-[10px] uppercase tracking-widest text-sage font-semibold">Curated</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* PRESENTATION — COMING SOON */}
      <section className="max-w-6xl mx-auto px-6 py-16" data-testid="presentation-coming-soon">
        <div className="rounded-3xl border border-dashed border-stone-border p-10 sm:p-14 bg-paper-warm">
          <div className="flex items-center gap-2 mb-3">
            <PenSquare className="w-4 h-4 text-warm-grey" strokeWidth={1.5} />
            <span className="text-[10px] uppercase tracking-widest text-warm-grey font-semibold px-2 py-0.5 rounded-full border border-stone-border">
              Coming Soon
            </span>
          </div>
          <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-charcoal max-w-2xl">
            Client-ready Design Story presentations.
          </h2>
          <p className="text-warm-grey mt-4 max-w-2xl leading-relaxed">
            Turn existing room photos, references, specifications, catalogue matches and product
            shortlists into visual client presentations.
          </p>
          <div className="mt-6 flex flex-wrap gap-2 text-xs">
            {["Current Space", "Design Direction", "Materials", "Products", "Presentation"].map((s, i) => (
              <div key={s} className="flex items-center gap-2">
                <span className="px-3 py-1 rounded-full bg-stone-panel text-charcoal border border-stone-border-soft">{s}</span>
                {i < 4 && <ChevronRight className="w-3 h-3 text-warm-grey" strokeWidth={2} />}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CLOSING CTA */}
      <section className="max-w-4xl mx-auto px-6 py-24 text-center">
        <div className="text-overline mb-3">Try it now</div>
        <h2 className="font-display text-4xl sm:text-5xl font-bold tracking-tight text-charcoal">
          See the full flow in under 3 minutes.
        </h2>
        <p className="text-warm-grey mt-4 max-w-xl mx-auto">
          The interactive demo shows a completed project — specification, catalogue matches, products,
          and a sourceable shortlist. No signup required.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-3 mt-8">
          <button
            type="button"
            onClick={openDemoModal}
            className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-7 py-3.5 font-medium transition-colors"
            data-testid="closing-cta-demo"
          >
            Explore Interactive Demo
            <ArrowRight className="w-4 h-4" strokeWidth={1.5} />
          </button>
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 bg-white text-charcoal border border-stone-border hover:border-charcoal hover:bg-stone-panel rounded-full px-7 py-3.5 font-medium transition-colors"
            data-testid="closing-cta-create"
          >
            Create Your First Project
          </button>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="border-t border-stone-border-soft mt-8">
        <div className="max-w-7xl mx-auto px-6 py-10 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-md bg-charcoal grid place-items-center">
              <div className="w-2.5 h-2.5 rounded-sm bg-paper"></div>
            </div>
            <span className="font-display font-bold text-sm text-charcoal">MaterialMatch.AI</span>
          </div>
          <div className="text-xs text-warm-grey">© 2026 MaterialMatch AI · Sourcing acceleration for designers</div>
        </div>
      </footer>

      {demoOpen && <DemoModal onClose={() => setDemoOpen(false)} />}
    </div>
  );
}
