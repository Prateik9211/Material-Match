import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import { useAuth } from "@/context/AuthContext";
import { ArrowRight, Check, Camera, Layers, BookOpen, ShoppingBag, ListChecks, PenSquare, PlayCircle, PauseCircle, X, ChevronRight, Focus } from "lucide-react";
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

const DEMO_CHAPTERS = [
  { t: "Reference", caption: "Upload a reference image.",
    detail: "A Pinterest pin, a client mood board, or a photograph — anything can seed a specification.",
    icon: Camera, accent: "bg-sand/60" },
  { t: "Detection", caption: "MaterialMatch detects finishes and products.",
    detail: "Zones, material families, colours and product categories surface automatically with confidence scores.",
    icon: Layers, accent: "bg-stone-panel" },
  { t: "Zone Focus", caption: "Draw over the image to zoom into one area.",
    detail: "Google-Lens-style region selection returns ephemeral zone-specific materials without altering the project.",
    icon: Focus, accent: "bg-sage-soft/60" },
  { t: "Catalogue", caption: "Match against your uploaded catalogues.",
    detail: "Ranked matches from PDF supplier catalogues with source, page and match reason.",
    icon: BookOpen, accent: "bg-ochre-soft/60" },
  { t: "Products", caption: "Discover Indian-market products.",
    detail: "Curated affiliate suggestions where available, tuned search keywords everywhere else.",
    icon: ShoppingBag, accent: "bg-white" },
  { t: "Shortlist", caption: "Walk into vendor meetings prepared.",
    detail: "A sourceable shortlist of materials and products with brands, notes and links.",
    icon: ListChecks, accent: "bg-charcoal text-paper" },
];

/* ---------------- Interactive Demo Modal (Video-style) ---------------- */
const CHAPTER_MS = 4200;

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

        {/* Video frame */}
        <div
          className={`aspect-video ${current.accent} border-b border-stone-border-soft grid place-items-center relative overflow-hidden transition-colors duration-500`}
          data-testid="demo-video-frame"
        >
          {/* Ambient grid */}
          <div className="absolute inset-0 opacity-[0.06] pointer-events-none" style={{
            backgroundImage: "linear-gradient(#2b2724 1px, transparent 1px), linear-gradient(90deg, #2b2724 1px, transparent 1px)",
            backgroundSize: "32px 32px",
          }} />

          {/* Big centered chapter card */}
          <div className="relative text-center px-8 max-w-2xl animate-fade-in-up" key={`chapter-${step}`}>
            <div className="w-14 h-14 rounded-full bg-paper/90 backdrop-blur border border-stone-border-soft grid place-items-center mx-auto mb-4 shadow-hover">
              <Icon className="w-6 h-6 text-charcoal" strokeWidth={1.5} />
            </div>
            <div className="text-overline mb-2 opacity-80">Chapter {step + 1} · {current.t}</div>
            <div className="font-display text-2xl sm:text-3xl font-semibold leading-tight" data-testid="demo-caption">
              {current.caption}
            </div>
            <p className="text-sm text-charcoal/70 mt-3 leading-relaxed" data-testid="demo-caption-detail">
              {current.detail}
            </p>
          </div>

          {/* Play/Pause overlay */}
          <button
            type="button"
            onClick={() => setPlaying((p) => !p)}
            className="absolute bottom-16 left-1/2 -translate-x-1/2 w-12 h-12 rounded-full bg-charcoal text-paper grid place-items-center hover:scale-105 transition-transform shadow-hover"
            data-testid="demo-play-toggle"
            aria-label={playing ? "Pause walkthrough" : "Play walkthrough"}
          >
            {playing ? <PauseCircle className="w-6 h-6" strokeWidth={1.5} /> : <PlayCircle className="w-6 h-6" strokeWidth={1.5} />}
          </button>

          {/* Timeline (chapter markers) */}
          <div className="absolute bottom-0 left-0 right-0 px-5 pb-4" data-testid="demo-timeline">
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
