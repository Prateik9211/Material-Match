import React from "react";
import { Link, useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import { useAuth } from "@/context/AuthContext";
import { ArrowRight, Check, Camera, Layers, Palette, ShoppingBag, PenSquare } from "lucide-react";

const interiorImage = "https://images.unsplash.com/photo-1771371097061-3befd4b71b59?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA2MjJ8MHwxfHNlYXJjaHw0fHxtaW5pbWFsaXN0JTIwaW50ZXJpb3IlMjBsaXZpbmclMjByb29tJTIwYnJpZ2h0fGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85";
const interiorImage2 = "https://images.unsplash.com/photo-1759722668087-efcc63c91ed2?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA2MjJ8MHwxfHNlYXJjaHwxfHxtaW5pbWFsaXN0JTIwaW50ZXJpb3IlMjBsaXZpbmclMjByb29tJTIwYnJpZ2h0fGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85";
const materialWood = "https://images.unsplash.com/photo-1768320837734-02390d59dfea?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Nzd8MHwxfHNlYXJjaHwzfHxhcmNoaXRlY3R1cmFsJTIwbWF0ZXJpYWwlMjB0ZXh0dXJlJTIwd29vZCUyMHN0b25lfGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85";
const materialClose = "https://images.unsplash.com/photo-1772423945486-8e941a5c01a9?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Nzd8MHwxfHNlYXJjaHwyfHxhcmNoaXRlY3R1cmFsJTIwbWF0ZXJpYWwlMjB0ZXh0dXJlJTIwd29vZCUyMHN0b25lfGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85";

const TRUST_BULLETS = [
  "Designer-first",
  "India-first sourcing",
  "Catalogue matching",
  "Products & Fixtures detection",
  "Client-ready presentations",
];

const WORKFLOW = [
  { n: "01", icon: Camera, title: "Upload reference", body: "Drop a Pinterest pin or interior photograph. Add a prompt to focus the specification, if needed." },
  { n: "02", icon: Layers, title: "Generate specification", body: "We identify surfaces, finishes and specification zones — with India-first sourcing context surfaced automatically." },
  { n: "03", icon: Palette, title: "Match your catalogue", body: "Upload a supplier PDF or product images. Materials are ranked by visual similarity with a clear reason for every match." },
  { n: "04", icon: ShoppingBag, title: "Detect products & fixtures", body: "Lighting, furniture, decor and fixtures are identified separately — with curated Indian recommendations where they exist." },
  { n: "05", icon: PenSquare, title: "Present to your client", body: "Assemble rooms into a design story — current space, moodboards, references, materials, products, notes — and share a link." },
];

export default function Landing() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const openDemo = () => {
    // Public demo works whether or not user is signed in.
    navigate("/demo");
  };
  const openCreate = () => {
    if (user) navigate("/projects/new");
    else navigate("/auth?mode=register&next=/projects/new");
  };

  return (
    <div className="min-h-screen bg-paper" data-testid="landing-page">
      <Header variant="marketing" />

      {/* HERO */}
      <section className="max-w-7xl mx-auto px-6 pt-16 pb-20 grain">
        <div className="grid lg:grid-cols-12 gap-12 items-center">
          <div className="lg:col-span-7 space-y-8 animate-fade-in-up">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-stone-border-soft text-overline">
              <span className="w-1.5 h-1.5 rounded-full bg-charcoal"></span>
              Specification &amp; Sourcing Workspace · Beta
            </div>
            <h1 className="font-display text-5xl sm:text-6xl lg:text-7xl tracking-tight leading-[0.95] font-bold text-charcoal">
              Keep designing.<br />
              We&apos;ll handle<br />
              <span className="text-warm-grey/60">everything after.</span>
            </h1>
            <p className="text-lg text-charcoal/70 max-w-xl leading-relaxed" data-testid="hero-subhead">
              Upload references, generate specifications, compare supplier catalogues,
              discover products, and prepare client-ready presentations — without
              replacing your creative process.
            </p>
            <p className="text-sm text-warm-grey italic max-w-xl border-l-2 border-stone-border pl-4" data-testid="hero-philosophy">
              MaterialMatch does not design for you. It removes repetitive work around
              sourcing, catalogues, specifications, products and presentations.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={openDemo}
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

          <div className="lg:col-span-5 relative animate-fade-in-up" style={{ animationDelay: "0.2s" }}>
            <div className="relative aspect-[4/5] rounded-3xl overflow-hidden shadow-hover">
              <img src={interiorImage} alt="Interior" className="w-full h-full object-cover" />
              <div className="absolute top-4 left-4 px-3 py-1.5 rounded-full bg-paper/90 backdrop-blur text-overline">
                Reference
              </div>
            </div>
            <div className="absolute -bottom-6 -left-6 w-44 aspect-square rounded-2xl overflow-hidden shadow-hover border-4 border-paper">
              <img src={materialWood} alt="Material" className="w-full h-full object-cover" />
              <div className="absolute bottom-2 left-2 right-2 bg-paper/90 backdrop-blur px-2 py-1 rounded-md text-[10px]">
                <div className="font-semibold text-charcoal">94% match</div>
                <div className="text-warm-grey">White Oak Veneer</div>
              </div>
            </div>
            <div className="absolute -top-6 -right-4 w-32 aspect-square rounded-2xl overflow-hidden shadow-hover border-4 border-paper hidden sm:block">
              <img src={materialClose} alt="Material close" className="w-full h-full object-cover" />
            </div>
          </div>
        </div>
      </section>

      {/* WORKFLOW */}
      <section id="workflow" className="max-w-7xl mx-auto px-6 py-24">
        <div className="max-w-2xl mb-14">
          <div className="text-overline mb-3">The workflow</div>
          <h2 className="font-display text-4xl sm:text-5xl font-bold tracking-tight text-charcoal">
            From inspiration to a <em className="not-italic text-warm-grey/70">client-ready presentation.</em>
          </h2>
          <p className="text-warm-grey mt-4 max-w-xl">
            Five steps a designer already knows — accelerated end-to-end.
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

      {/* PRODUCT IMAGE SHOWCASE */}
      <section className="max-w-7xl mx-auto px-6 py-16">
        <div className="grid lg:grid-cols-12 gap-8 items-center">
          <div className="lg:col-span-7 relative aspect-[16/10] rounded-3xl overflow-hidden">
            <img src={interiorImage2} alt="Interior" className="w-full h-full object-cover" />
          </div>
          <div className="lg:col-span-5 flex flex-col justify-center space-y-6">
            <div className="text-overline">Built for studios</div>
            <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight leading-tight text-charcoal">
              Stop guessing. Start sourcing with confidence.
            </h2>
            <p className="text-warm-grey leading-relaxed">
              Specify materials your client will love — backed by visual evidence. Cut hours
              of catalogue flipping and material library searches into a single workflow.
            </p>
            <ul className="space-y-3">
              {[
                "Specification zones with confidence scores",
                "Side-by-side visual reasoning on every match",
                "India-first sourcing recommendations",
                "Client-ready presentations, shareable in one link",
              ].map((b) => (
                <li key={b} className="flex items-start gap-3 text-sm text-charcoal">
                  <Check className="w-4 h-4 mt-0.5 text-sage" strokeWidth={2} />
                  {b}
                </li>
              ))}
            </ul>
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
          The interactive demo shows a fully-finished project — specification, catalogue matches,
          products, and a shareable concept presentation. No signup required.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-3 mt-8">
          <button
            type="button"
            onClick={openDemo}
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
          <div className="text-xs text-warm-grey">© 2026 MaterialMatch AI · Crafted for designers</div>
        </div>
      </footer>
    </div>
  );
}
