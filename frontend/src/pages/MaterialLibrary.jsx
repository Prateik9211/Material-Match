import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { BookOpen, Users, User, Layers, ArrowUpRight, Plus, Sparkles, Palette, Grid3x3, Trees, Mountain, Square, Blocks, Lightbulb, Wrench, Armchair, Shirt } from "lucide-react";
import MyCatalogueSection from "@/components/library/MyCatalogueSection";

function StatusPill({ status }) {
  const map = {
    beta: { label: "Beta", cls: "bg-ochre-soft text-ochre border-ochre/30" },
    growing: { label: "Growing library", cls: "bg-sage-soft text-sage border-sage/30" },
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

const CATEGORY_ICON = {
  Paints: Palette,
  Laminates: Grid3x3,
  Veneers: Trees,
  Stone: Mountain,
  Tiles: Square,
  Fabric: Shirt,
  Lighting: Lightbulb,
  Hardware: Wrench,
  Furniture: Armchair,
};

function CategoryTile({ tile }) {
  const Icon = CATEGORY_ICON[tile.category] || Blocks;
  const navigate = useNavigate();
  const slug = (tile.category || "").toLowerCase();
  const disabled = !tile.count || tile.status === "coming_soon";
  return (
    <button
      type="button"
      onClick={() => !disabled && navigate(`/library/${encodeURIComponent(slug)}`)}
      disabled={disabled}
      className={`text-left w-full border border-stone-border-soft rounded-2xl p-5 bg-white transition-all ${
        disabled ? "opacity-70 cursor-not-allowed" : "hover:border-charcoal/50 hover:shadow-hover cursor-pointer"
      }`}
      data-testid={`category-tile-${slug}`}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="w-10 h-10 rounded-xl bg-stone-panel grid place-items-center">
          <Icon className="w-5 h-5 text-charcoal" strokeWidth={1.5} />
        </div>
        <span className="text-[10px] uppercase tracking-widest text-warm-grey/70">{tile.status}</span>
      </div>
      <div className="text-overline mb-0.5">{tile.library_label || tile.category}</div>
      <div className="flex items-baseline gap-2">
        <span className="font-display text-3xl font-bold text-charcoal" data-testid={`category-count-${slug}`}>{tile.count}</span>
        <span className="text-xs text-warm-grey">records</span>
      </div>
      {tile.sample_brands && tile.sample_brands.length > 0 && (
        <div className="text-[11px] text-warm-grey mt-2 leading-relaxed">
          <span className="text-warm-grey/60">Brands · </span>
          {tile.sample_brands.slice(0, 4).join(" · ")}
          {tile.sample_brands.length > 4 && <span className="text-warm-grey/60"> …</span>}
        </div>
      )}
      {!disabled && (
        <div className="mt-3 text-[11px] uppercase tracking-widest text-warm-grey inline-flex items-center gap-1">
          Open library <ArrowUpRight className="w-3 h-3" strokeWidth={1.75} />
        </div>
      )}
    </button>
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
  const [globalMeta, setGlobalMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const g = await api.get("/library/global");
      setGlobalMeta(g.data);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const tiles = globalMeta?.tiles || [];
  const totalRecords = globalMeta?.total || 0;
  const totalCategories = globalMeta?.category_names?.length || 0;
  const totalBrands = globalMeta?.brands_total || 0;

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
              against your growing library — supplier PDFs, product image sets and the platform-wide MaterialMatch Library.
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
            {/* MATERIALMATCH LIBRARY — coverage banner + category tiles */}
            <LibrarySection
              number="01"
              icon={BookOpen}
              title="MaterialMatch Library"
              subtitle="Platform-managed Knowledge Engine. Curated across regional supplier catalogues and searched category-first so a paint region never returns a wood veneer."
              status="beta"
              testid="library-section-materialmatch"
            >
              {/* Coverage banner */}
              <div className="rounded-2xl bg-gradient-to-br from-ochre-soft/40 to-sand/30 border border-ochre/20 p-5 mb-5 flex items-baseline justify-between flex-wrap gap-3" data-testid="mm-coverage-banner">
                <div>
                  <div className="text-overline mb-1">Coverage</div>
                  <div className="flex items-baseline gap-6 flex-wrap">
                    <div>
                      <span className="font-display text-3xl font-bold text-charcoal" data-testid="mm-total-records">{totalRecords}</span>
                      <span className="text-xs text-warm-grey ml-2">indexed materials</span>
                    </div>
                    <div>
                      <span className="font-display text-3xl font-bold text-charcoal" data-testid="mm-total-categories">{totalCategories}</span>
                      <span className="text-xs text-warm-grey ml-2">categories</span>
                    </div>
                    <div>
                      <span className="font-display text-3xl font-bold text-charcoal" data-testid="mm-total-brands">{totalBrands}</span>
                      <span className="text-xs text-warm-grey ml-2">brands</span>
                    </div>
                  </div>
                </div>
                <span className="text-[11px] uppercase tracking-widest text-ochre font-semibold" data-testid="mm-coverage-status">
                  {globalMeta?.coverage_status || "Coverage expanding"}
                </span>
              </div>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3" data-testid="mm-category-grid">
                {tiles.map((t) => <CategoryTile key={t.category} tile={t} />)}
              </div>
            </LibrarySection>

            {/* MY LIBRARY — real user-uploadable catalogues.
                2026-02-01 (round 4): replaced the legacy /library/my
                filename-aggregation with a genuine upload+extract flow
                backed by ke_uploads/ke_records with catalogue_scope='user'. */}
            <LibrarySection
              number="02"
              icon={User}
              title="My Uploaded Catalogues"
              subtitle="Your private supplier PDFs. Uploaded once, extracted automatically, searchable across every project you create."
              status="beta"
              testid="library-section-mine"
            >
              <MyCatalogueSection />
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
                verified availability of every SKU. The MaterialMatch Library is a growing knowledge engine.
                Always confirm availability, price and colour with your vendor before final selection.
              </p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
