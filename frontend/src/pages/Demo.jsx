import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import axios from "axios";
import Header from "@/components/Header";
import ProductsSection from "@/components/analysis/ProductsSection";
import MaterialsFirstSection from "@/components/analysis/MaterialsFirstSection";
import { ArrowLeft, Sparkles, ExternalLink, ArrowRight, Layers } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const publicApi = axios.create({ baseURL: `${BACKEND_URL}/api` });

/**
 * Public read-only Demo project. Shows the full flow — specification, products
 * & fixtures, and catalogue matches — without requiring signup. There is also
 * a persistent CTA to explore the full concept presentation and create a real
 * project.
 */
export default function Demo() {
  const [project, setProject] = useState(null);
  const [refImg, setRefImg] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const [p, r] = await Promise.all([
          publicApi.get("/demo/project"),
          publicApi.get("/demo/reference-image").catch(() => null),
        ]);
        if (cancel) return;
        setProject(p.data);
        if (r) setRefImg(r.data.data_url);
      } catch {
        if (!cancel) setError("Demo is temporarily unavailable. Please try again shortly.");
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen grid place-items-center bg-paper text-sm text-warm-grey" data-testid="demo-loading">
        Preparing your design story…
      </div>
    );
  }
  if (error || !project) {
    return (
      <div className="min-h-screen grid place-items-center bg-paper p-6" data-testid="demo-error">
        <div className="text-center max-w-md">
          <div className="text-overline mb-2">Demo unavailable</div>
          <p className="text-warm-grey text-sm">{error || "Please try again shortly."}</p>
          <Link to="/" className="inline-block mt-4 text-charcoal underline">Back to home</Link>
        </div>
      </div>
    );
  }

  const rows = project?.mock_analysis?.rows || [];
  const summary = project?.mock_analysis?.summary || {};
  const products = project?.products_detected?.products || [];
  const matches = project?.match_results?.top_matches || [];

  return (
    <div className="min-h-screen bg-paper" data-testid="demo-page">
      <Header variant="marketing" />

      {/* Demo ribbon */}
      <div className="bg-sand/40 border-b border-stone-border-soft">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2 text-sm text-charcoal">
            <Sparkles className="w-4 h-4 text-ochre" strokeWidth={1.75} />
            <span className="font-medium">Demo Project</span>
            <span className="text-warm-grey">· read-only, no signup required</span>
          </div>
          <button
            type="button"
            onClick={() => navigate("/auth?mode=register")}
            className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-4 py-1.5 text-sm font-medium transition-colors"
            data-testid="demo-cta-signup"
          >
            Create your own project
            <ArrowRight className="w-3.5 h-3.5" strokeWidth={1.75} />
          </button>
        </div>
      </div>

      <main className="max-w-7xl mx-auto px-6 py-12 space-y-14">
        <Link to="/" className="inline-flex items-center gap-2 text-sm text-warm-grey hover:text-charcoal" data-testid="demo-back-home">
          <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
          Back to home
        </Link>

        {/* Hero row: reference + summary */}
        <section className="grid lg:grid-cols-12 gap-8 items-start">
          <div className="lg:col-span-5">
            <div className="text-overline mb-2">Reference</div>
            <div className="aspect-[4/5] rounded-2xl overflow-hidden bg-stone-panel shadow-soft border border-stone-border-soft">
              {refImg ? (
                <img src={refImg} alt="Demo reference" className="w-full h-full object-cover" data-testid="demo-reference-img" />
              ) : (
                <div className="w-full h-full grid place-items-center text-warm-grey text-sm">No reference image</div>
              )}
            </div>
          </div>
          <div className="lg:col-span-7 space-y-6">
            <div>
              <div className="text-overline mb-2">Specification Overview</div>
              <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight text-charcoal" data-testid="demo-project-name">
                {project.name}
              </h1>
              <p className="text-warm-grey mt-2">{project.client_name}</p>
            </div>
            {summary && Object.keys(summary).length > 0 && (
              <div className="bg-white rounded-2xl border border-stone-border-soft p-6 shadow-soft" data-testid="demo-summary">
                {summary.overall_style && (
                  <p className="font-display text-xl text-charcoal leading-relaxed mb-4">
                    {summary.overall_style}
                  </p>
                )}
                {summary.palette && summary.palette.length > 0 && (
                  <div className="mb-3">
                    <div className="text-overline mb-1.5">Palette</div>
                    <div className="flex flex-wrap gap-2">
                      {summary.palette.map((c) => (
                        <span key={c} className="text-xs px-3 py-1 rounded-full bg-stone-panel text-charcoal border border-stone-border-soft">{c}</span>
                      ))}
                    </div>
                  </div>
                )}
                {summary.dominant_materials && summary.dominant_materials.length > 0 && (
                  <div>
                    <div className="text-overline mb-1.5">Dominant materials</div>
                    <div className="flex flex-wrap gap-2">
                      {summary.dominant_materials.map((c) => (
                        <span key={c} className="text-xs px-3 py-1 rounded-full bg-sage-soft text-charcoal border border-sage/40">{c}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Specification Zones — catalogue-first materials */}
        {rows.length > 0 && (
          <div data-testid="demo-spec-zones">
            <MaterialsFirstSection rows={rows} />
          </div>
        )}

        {/* Products & Fixtures */}
        {products.length > 0 && <ProductsSection products={products} />}

        {/* Catalogue Matches */}
        {matches.length > 0 && (
          <section data-testid="demo-catalogue-matches">
            <div className="mb-6">
              <div className="text-overline mb-2">Catalogue Matching</div>
              <h2 className="font-display text-3xl font-semibold tracking-tight text-charcoal">Best matches from the supplier PDF</h2>
              <p className="text-sm text-warm-grey mt-2 max-w-2xl">
                Every match includes the source page, a similarity score, and the visual reasoning that drove the ranking.
              </p>
            </div>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {matches.map((m, i) => (
                <div key={i} className="bg-white border border-stone-border-soft rounded-2xl p-5 shadow-soft" data-testid={`demo-match-${i}`}>
                  <div className="flex items-baseline justify-between mb-2">
                    <div className="text-overline">Page {m.page_number}</div>
                    <span className="text-sm font-mono font-semibold text-sage">{m.match_percent}%</span>
                  </div>
                  <div className="font-display text-lg font-semibold text-charcoal leading-tight">
                    {m.material_name}
                  </div>
                  <p className="text-xs text-warm-grey mt-2 leading-relaxed">
                    {m.explanation}
                  </p>
                  <div className="text-[10px] text-warm-grey mt-3 truncate">{m.filename}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Concept Presentation CTA */}
        <section className="rounded-3xl bg-stone-panel border border-stone-border p-10 sm:p-14 text-center" data-testid="demo-presentation-cta">
          <Layers className="w-8 h-8 text-charcoal mx-auto mb-4" strokeWidth={1.5} />
          <div className="text-overline mb-2">Presentation</div>
          <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-charcoal">
            See the full client presentation.
          </h2>
          <p className="text-warm-grey mt-3 max-w-xl mx-auto">
            The demo also includes a shareable Concept Presentation — current space, moodboards, references,
            concept overview, material specifications, products &amp; fixtures, and designer notes.
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            <a
              href="/share/rooms/materialmatch-demo"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-6 py-3 text-sm font-medium transition-colors"
              data-testid="demo-open-presentation"
            >
              Open Concept Presentation
              <ExternalLink className="w-3.5 h-3.5" strokeWidth={1.75} />
            </a>
            <button
              type="button"
              onClick={() => navigate("/auth?mode=register")}
              className="inline-flex items-center gap-2 bg-white text-charcoal border border-stone-border hover:border-charcoal rounded-full px-6 py-3 text-sm font-medium transition-colors"
              data-testid="demo-signup-btn"
            >
              Create Your First Project
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}
