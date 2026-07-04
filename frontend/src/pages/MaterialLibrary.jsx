import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { BookOpen, Users, User, Layers, ArrowUpRight, Plus, Sparkles } from "lucide-react";

function StatusPill({ status }) {
  const map = {
    beta: { label: "Beta", cls: "bg-ochre-soft text-ochre border-ochre/30" },
    coming_soon: { label: "Coming soon", cls: "bg-stone-panel text-warm-grey border-stone-border" },
    active: { label: "Active", cls: "bg-sage-soft text-sage border-sage/30" },
  };
  const m = map[status] || map.beta;
  return (
    <span className={`text-[10px] uppercase tracking-widest font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>
      {m.label}
    </span>
  );
}

function LibrarySection({ number, icon: Icon, title, subtitle, status, testid, children }) {
  return (
    <section className="bg-white border border-stone-border-soft rounded-2xl shadow-soft overflow-hidden" data-testid={testid}>
      <div className="p-6 sm:p-8 border-b border-stone-border-soft">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-xl bg-stone-panel grid place-items-center">
              <Icon className="w-4 h-4 text-charcoal" strokeWidth={1.5} />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <div className="text-xs font-mono text-warm-grey/60 tabular-nums">{number}</div>
                <StatusPill status={status} />
              </div>
              <h2 className="font-display text-xl font-semibold text-charcoal">{title}</h2>
              <p className="text-sm text-warm-grey mt-1 max-w-2xl leading-relaxed">{subtitle}</p>
            </div>
          </div>
        </div>
      </div>
      <div className="p-6 sm:p-8">{children}</div>
    </section>
  );
}

function GlobalItem({ item }) {
  return (
    <div className="border border-stone-border-soft rounded-xl p-4 hover:border-stone-border transition-colors" data-testid={`global-item-${item.id}`}>
      <div className="flex items-baseline justify-between mb-1">
        <div className="text-overline">{item.category}</div>
        <span className="text-[10px] text-warm-grey">{item.region}</span>
      </div>
      <div className="font-display font-semibold text-charcoal leading-tight">{item.name}</div>
      <div className="text-xs text-warm-grey mt-0.5">{item.brand}</div>
      <p className="text-xs text-charcoal/70 mt-2 leading-relaxed">{item.coverage_note}</p>
      <div className="mt-3 text-[10px] uppercase tracking-widest text-warm-grey/70">Coverage: coming soon</div>
    </div>
  );
}

function MyItem({ item }) {
  const dateStr = item.last_used_at ? new Date(item.last_used_at).toLocaleDateString() : "—";
  return (
    <div className="border border-stone-border-soft rounded-xl p-4 flex items-start justify-between gap-3 hover:border-stone-border transition-colors" data-testid={`my-item-${item.id}`}>
      <div className="flex items-start gap-3 min-w-0">
        <div className="w-9 h-9 rounded-lg bg-sand/40 grid place-items-center flex-shrink-0">
          <Layers className="w-4 h-4 text-charcoal" strokeWidth={1.5} />
        </div>
        <div className="min-w-0">
          <div className="font-display font-semibold text-charcoal truncate">{item.name}</div>
          <div className="text-xs text-warm-grey mt-0.5">
            Used {item.usage_count} time{item.usage_count === 1 ? "" : "s"} · Last {dateStr}
          </div>
          {item.projects && item.projects.length > 0 && (
            <div className="text-[10px] text-warm-grey/70 mt-1 truncate">
              In: {item.projects.slice(0, 3).join(" · ")}
            </div>
          )}
        </div>
      </div>
      <span className="text-[10px] uppercase tracking-widest text-warm-grey/70 whitespace-nowrap">
        Reuse: coming soon
      </span>
    </div>
  );
}

export default function MaterialLibrary() {
  const [global_, setGlobal] = useState([]);
  const [mine, setMine] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const [g, m] = await Promise.all([
        api.get("/library/global"),
        api.get("/library/my"),
      ]);
      setGlobal(g.data.items || []);
      setMine(m.data.items || []);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="min-h-screen bg-paper" data-testid="material-library-page">
      <Header />
      <main className="max-w-6xl mx-auto px-6 py-12">
        <div className="flex items-end justify-between flex-wrap gap-4 mb-10">
          <div>
            <div className="text-overline mb-2">Material Library</div>
            <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight text-charcoal">
              Your sourcing shelf.
            </h1>
            <p className="text-warm-grey mt-3 max-w-2xl">
              Upload catalogues once. Reuse them across future projects. Compare inspiration references
              against your growing library — supplier PDFs, product image sets and global brand collections.
            </p>
          </div>
          <button
            type="button"
            onClick={() => navigate("/projects/new")}
            className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-5 py-2.5 text-sm font-medium transition-colors"
            data-testid="library-upload-cta"
          >
            <Plus className="w-4 h-4" strokeWidth={1.75} />
            Add via a new project
          </button>
        </div>

        {loading ? (
          <div className="text-center text-sm text-warm-grey py-16">Loading library…</div>
        ) : (
          <div className="space-y-8">
            {/* MY LIBRARY */}
            <LibrarySection
              number="01"
              icon={User}
              title="My Library"
              subtitle="Catalogues you've uploaded across your projects. Aggregated for quick reference."
              status={mine.length > 0 ? "beta" : "coming_soon"}
              testid="library-section-mine"
            >
              {mine.length === 0 ? (
                <div className="text-center py-8 text-sm text-warm-grey" data-testid="my-empty">
                  You haven&apos;t uploaded any catalogues yet. Upload one via the Catalogue Match step inside any project.
                </div>
              ) : (
                <div className="grid sm:grid-cols-2 gap-3" data-testid="my-list">
                  {mine.map((it) => <MyItem key={it.id} item={it} />)}
                </div>
              )}
            </LibrarySection>

            {/* GLOBAL LIBRARY */}
            <LibrarySection
              number="02"
              icon={BookOpen}
              title="Global Library"
              subtitle="Platform-managed collections from leading Indian brands. Direct match against these catalogues is on the way."
              status="coming_soon"
              testid="library-section-global"
            >
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3" data-testid="global-list">
                {global_.map((it) => <GlobalItem key={it.id} item={it} />)}
              </div>
            </LibrarySection>

            {/* COMMUNITY LIBRARY */}
            <LibrarySection
              number="03"
              icon={Users}
              title="Community Library"
              subtitle="A future concept where designers may choose to contribute their reusable catalogues."
              status="coming_soon"
              testid="library-section-community"
            >
              <div className="rounded-xl border border-dashed border-stone-border p-8 text-center bg-stone-panel/40" data-testid="community-empty">
                <Sparkles className="w-6 h-6 text-warm-grey mx-auto mb-3" strokeWidth={1.25} />
                <p className="text-sm text-charcoal font-medium">Community catalogues — coming soon</p>
                <p className="text-xs text-warm-grey mt-2 max-w-md mx-auto">
                  If you&rsquo;d like to contribute reusable catalogues to the community, we&rsquo;ll open early access here.
                </p>
              </div>
            </LibrarySection>

            {/* Footer note */}
            <div className="rounded-2xl bg-sand/30 border border-stone-border-soft p-6 flex items-start gap-3" data-testid="library-transparency-note">
              <ArrowUpRight className="w-4 h-4 text-charcoal mt-0.5 flex-shrink-0" strokeWidth={1.5} />
              <p className="text-xs text-charcoal/80 leading-relaxed">
                <strong className="text-charcoal">Transparency:</strong> MaterialMatch does not claim
                verified availability of every SKU. Global Library coverage is being built. Always confirm
                availability, price and colour with your vendor before final selection.
              </p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
