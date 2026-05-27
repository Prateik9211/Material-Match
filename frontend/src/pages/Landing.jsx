import React from "react";
import { Link } from "react-router-dom";
import Header from "@/components/Header";
import { ArrowRight, Upload, Sparkles, FileDown, Check, Layers, Palette, Camera } from "lucide-react";

const interiorImage = "https://images.unsplash.com/photo-1771371097061-3befd4b71b59?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA2MjJ8MHwxfHNlYXJjaHw0fHxtaW5pbWFsaXN0JTIwaW50ZXJpb3IlMjBsaXZpbmclMjByb29tJTIwYnJpZ2h0fGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85";
const interiorImage2 = "https://images.unsplash.com/photo-1759722668087-efcc63c91ed2?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA2MjJ8MHwxfHNlYXJjaHwxfHxtaW5pbWFsaXN0JTIwaW50ZXJpb3IlMjBsaXZpbmclMjByb29tJTIwYnJpZ2h0fGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85";
const materialWood = "https://images.unsplash.com/photo-1768320837734-02390d59dfea?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Nzd8MHwxfHNlYXJjaHwzfHxhcmNoaXRlY3R1cmFsJTIwbWF0ZXJpYWwlMjB0ZXh0dXJlJTIwd29vZCUyMHN0b25lfGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85";
const materialClose = "https://images.unsplash.com/photo-1772423945486-8e941a5c01a9?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Nzd8MHwxfHNlYXJjaHwyfHxhcmNoaXRlY3R1cmFsJTIwbWF0ZXJpYWwlMjB0ZXh0dXJlJTIwd29vZCUyMHN0b25lfGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85";

export default function Landing() {
  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="landing-page">
      <Header variant="marketing" />

      {/* HERO */}
      <section className="max-w-7xl mx-auto px-6 pt-16 pb-20 grain">
        <div className="grid lg:grid-cols-12 gap-12 items-center">
          <div className="lg:col-span-7 space-y-8 animate-fade-in-up">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-black/5 text-overline">
              <span className="w-1.5 h-1.5 rounded-full bg-black"></span>
              For Architects & Interior Designers
            </div>
            <h1 className="font-display text-5xl sm:text-6xl lg:text-7xl tracking-tight leading-[0.95] font-bold">
              Match every<br />
              material to its<br />
              <span className="text-neutral-400">inspiration.</span>
            </h1>
            <p className="text-lg text-neutral-600 max-w-xl leading-relaxed">
              Upload a reference photo. Drop in your catalogue. Our AI identifies materials, finishes, and finishes — then matches them to products from your library, with percentage scores and visual reasoning.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <Link
                to="/auth?mode=register"
                className="inline-flex items-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-7 py-3.5 font-medium transition-colors"
                data-testid="hero-cta-primary"
              >
                Start matching
                <ArrowRight className="w-4 h-4" strokeWidth={1.5} />
              </Link>
              <a
                href="#workflow"
                className="inline-flex items-center gap-2 bg-white text-neutral-900 border border-black/10 hover:bg-black/5 rounded-full px-7 py-3.5 font-medium transition-colors"
                data-testid="hero-cta-secondary"
              >
                See how it works
              </a>
            </div>
            <div className="flex items-center gap-6 pt-4 text-sm text-neutral-500">
              <div className="flex items-center gap-2"><Check className="w-4 h-4" strokeWidth={1.5} /> No render generation</div>
              <div className="flex items-center gap-2"><Check className="w-4 h-4" strokeWidth={1.5} /> Catalogue-first</div>
              <div className="flex items-center gap-2"><Check className="w-4 h-4" strokeWidth={1.5} /> PDF reports</div>
            </div>
          </div>

          <div className="lg:col-span-5 relative animate-fade-in-up" style={{ animationDelay: "0.2s" }}>
            <div className="relative aspect-[4/5] rounded-3xl overflow-hidden shadow-hover">
              <img src={interiorImage} alt="Interior" className="w-full h-full object-cover" />
              <div className="absolute top-4 left-4 px-3 py-1.5 rounded-full bg-white/90 backdrop-blur text-overline">
                Reference
              </div>
            </div>
            <div className="absolute -bottom-6 -left-6 w-44 aspect-square rounded-2xl overflow-hidden shadow-hover border-4 border-[#F9F9F8]">
              <img src={materialWood} alt="Material" className="w-full h-full object-cover" />
              <div className="absolute bottom-2 left-2 right-2 bg-white/90 backdrop-blur px-2 py-1 rounded-md text-[10px]">
                <div className="font-semibold">94% match</div>
                <div className="text-neutral-500">White Oak Veneer</div>
              </div>
            </div>
            <div className="absolute -top-6 -right-4 w-32 aspect-square rounded-2xl overflow-hidden shadow-hover border-4 border-[#F9F9F8] hidden sm:block">
              <img src={materialClose} alt="Material close" className="w-full h-full object-cover" />
            </div>
          </div>
        </div>
      </section>

      {/* WORKFLOW */}
      <section id="workflow" className="max-w-7xl mx-auto px-6 py-24">
        <div className="max-w-2xl mb-16">
          <div className="text-overline mb-3">How it works</div>
          <h2 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
            Three steps from <em className="not-italic text-neutral-400">inspiration</em> to <em className="not-italic text-neutral-400">sourcing.</em>
          </h2>
        </div>

        <div className="grid md:grid-cols-3 gap-6 md:gap-8">
          {[
            { n: "01", icon: Camera, title: "Upload reference", body: "Drop a Pinterest pin or photograph of an interior you love. Add a prompt to focus the analysis if needed." },
            { n: "02", icon: Layers, title: "Drop your catalogue", body: "Upload a PDF catalogue or a folder of product photos. We'll parse and index every material." },
            { n: "03", icon: Palette, title: "Get matches & report", body: "AI ranks the closest materials by visual similarity, explains why, and exports a polished PDF." },
          ].map((s) => (
            <div
              key={s.n}
              className="bg-white border border-black/5 rounded-2xl p-8 shadow-soft hover:shadow-hover transition-all hover:-translate-y-1 duration-300"
              data-testid={`workflow-step-${s.n}`}
            >
              <div className="flex items-start justify-between mb-6">
                <span className="font-display text-5xl text-neutral-200 font-bold">{s.n}</span>
                <s.icon className="w-6 h-6 text-neutral-700" strokeWidth={1.25} />
              </div>
              <h3 className="font-display text-xl font-semibold mb-2">{s.title}</h3>
              <p className="text-sm text-neutral-600 leading-relaxed">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* PRODUCT IMAGE SHOWCASE */}
      <section className="max-w-7xl mx-auto px-6 py-16">
        <div className="grid lg:grid-cols-12 gap-8">
          <div className="lg:col-span-7 relative aspect-[16/10] rounded-3xl overflow-hidden">
            <img src={interiorImage2} alt="Interior 2" className="w-full h-full object-cover" />
          </div>
          <div className="lg:col-span-5 flex flex-col justify-center space-y-6">
            <div className="text-overline">Built for studios</div>
            <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight leading-tight">
              Stop guessing. Start sourcing with confidence.
            </h2>
            <p className="text-neutral-600 leading-relaxed">
              Specify materials your client will love — backed by visual evidence. Cut hours of catalogue flipping and material library searches into a single workflow.
            </p>
            <ul className="space-y-3">
              {["Detected materials with confidence scores", "Side-by-side visual reasoning", "Branded client-ready PDF reports"].map((b) => (
                <li key={b} className="flex items-start gap-3 text-sm text-neutral-700">
                  <Check className="w-4 h-4 mt-0.5 text-black" strokeWidth={2} />
                  {b}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* PRICING — India Early-Access Beta */}
      <section id="pricing" className="max-w-7xl mx-auto px-6 py-24">
        <div className="text-center mb-12">
          <div className="text-overline mb-3">Pricing</div>
          <h2 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
            Early Access Beta.
          </h2>
          <p className="text-neutral-500 mt-4 max-w-xl mx-auto text-sm sm:text-base">
            We're inviting selected Indian architects and interior designers to test
            MaterialMatch.AI free of cost during the beta.
          </p>
        </div>

        <div className="max-w-xl mx-auto">
          <div
            className="rounded-3xl p-10 bg-black text-white relative overflow-hidden"
            data-testid="pricing-card-beta"
          >
            <span className="absolute top-6 right-6 text-[10px] uppercase tracking-widest bg-white text-black px-2 py-1 rounded-full font-semibold">
              India · Beta
            </span>

            <div className="flex items-baseline justify-between mb-6">
              <h3 className="font-display text-2xl font-semibold">Early Access Beta</h3>
            </div>

            <div className="mb-6">
              <span className="font-display text-6xl font-bold" data-testid="pricing-amount">
                ₹0
              </span>
              <span className="text-sm text-neutral-400 ml-2">/ limited beta</span>
            </div>

            <p className="text-sm text-neutral-300 mb-6 leading-relaxed">
              For selected Indian architects and interior designers. Help shape the
              product while it's still being built around your workflow.
            </p>

            <ul className="space-y-3 mb-8" data-testid="pricing-features">
              {[
                "AI material analysis from reference images",
                "Catalogue image matching with Top-5 ranked results",
                "India-mode AI: prompts use Indian-market sourcing context",
                "Unlimited projects during beta",
                "Direct feedback channel with the product team",
              ].map((f) => (
                <li key={f} className="flex items-start gap-2 text-sm text-neutral-200">
                  <Check className="w-4 h-4 mt-0.5 shrink-0" strokeWidth={2} />
                  {f}
                </li>
              ))}
            </ul>

            <Link
              to="/auth?mode=register"
              className="inline-flex items-center gap-2 rounded-full px-6 py-3 font-medium transition-colors bg-white text-black hover:bg-white/90"
              data-testid="pricing-cta-beta"
            >
              Request Early Access
              <ArrowRight className="w-4 h-4" strokeWidth={1.5} />
            </Link>

            <p
              className="text-xs italic text-neutral-400 mt-6 leading-relaxed"
              data-testid="pricing-note-india"
            >
              Pricing for Indian studios will be finalized after beta feedback.
            </p>
          </div>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="border-t border-black/5 mt-16">
        <div className="max-w-7xl mx-auto px-6 py-10 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-md bg-black grid place-items-center">
              <div className="w-2.5 h-2.5 rounded-sm bg-white"></div>
            </div>
            <span className="font-display font-bold text-sm">MaterialMatch.AI</span>
          </div>
          <div className="text-xs text-neutral-500">© 2026 MaterialMatch AI · Crafted for designers</div>
        </div>
      </footer>
    </div>
  );
}
