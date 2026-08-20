import React, { useState, useEffect } from "react";
import { ListChecks, X, ExternalLink, BookOpen, ShoppingBag, Sparkles } from "lucide-react";

const SOURCE_ICON = {
  catalogue_match: BookOpen,
  product: ShoppingBag,
  spec: Sparkles,
  custom: ListChecks,
};

const SOURCE_LABEL = {
  catalogue_match: "Catalogue",
  product: "Product",
  spec: "Specification",
  custom: "Custom",
};

/**
 * ShortlistSection — a per-project sourceable shortlist. Bridges detection
 * and physical verification. Designer curates items from match results,
 * product suggestions or specification zones.
 */
export default function ShortlistSection({ items, onRemove }) {
  // Round 8 — click-to-enlarge lightbox for shortlisted swatch previews.
  // Users wanted a proper preview before trusting a thumbnail; opens on
  // click, dismisses on backdrop / X / ESC.
  // 2026-02-14 — lightbox now also carries `material_src` (Nano Banana
  // photorealistic render). When present, a "Catalogue view / Material
  // view" toggle appears; absence quietly falls back to catalogue view.
  const [preview, setPreview] = useState(null);  // { src, material_src, name, hex, code }
  const [view, setView] = useState("catalogue");

  useEffect(() => {
    if (!preview) return;
    const onKey = (e) => { if (e.key === "Escape") setPreview(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview]);

  if (!items || items.length === 0) {
    return (
      <section data-testid="shortlist-section">
        <div className="flex items-baseline justify-between flex-wrap gap-2 mb-4">
          <div>
            <div className="text-overline mb-1">Sourceable Shortlist</div>
            <h2 className="font-display text-2xl sm:text-3xl font-semibold tracking-tight text-charcoal">
              Build a shortlist before visiting vendors
            </h2>
            <p className="text-sm text-warm-grey mt-1 max-w-2xl">
              Add materials, catalogue matches or products you want to source. This becomes your
              vendor-visit checklist.
            </p>
          </div>
        </div>
        <div
          className="rounded-2xl border border-dashed border-stone-border bg-stone-panel/40 p-8 text-center"
          data-testid="shortlist-empty"
        >
          <ListChecks className="w-6 h-6 text-warm-grey mx-auto mb-3" strokeWidth={1.25} />
          <p className="text-sm text-charcoal font-medium">Your shortlist is empty.</p>
          <p className="text-xs text-warm-grey mt-2 max-w-md mx-auto">
            Click <span className="font-medium text-charcoal">Add to Shortlist</span> on any material
            zone, catalogue match, or product card to start building.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section data-testid="shortlist-section">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-4">
        <div>
          <div className="text-overline mb-1">Sourceable Shortlist</div>
          <h2 className="font-display text-2xl sm:text-3xl font-semibold tracking-tight text-charcoal">
            {items.length} {items.length === 1 ? "item" : "items"} ready to source
          </h2>
          <p className="text-sm text-warm-grey mt-1 max-w-2xl">
            Take this into your next vendor meeting.
          </p>
        </div>
      </div>
      <div className="bg-white border border-stone-border-soft rounded-2xl divide-y divide-stone-border-soft shadow-soft overflow-hidden" data-testid="shortlist-list">
        {items.map((it, idx) => {
          const Icon = SOURCE_ICON[it.source_type] || ListChecks;
          return (
            <div
              key={it.id}
              className="flex items-start justify-between gap-3 p-4 sm:p-5 hover:bg-stone-panel/40 transition-colors"
              data-testid={`shortlist-item-${idx}`}
            >
              <div className="flex items-start gap-3 min-w-0 flex-1">
                {(() => {
                  // Round 8 — swatch thumbnail (real image or hex block).
                  // Click enlarges into the lightbox below.
                  const swatchSrc = it.swatch_crop_b64
                    ? (it.swatch_crop_b64.startsWith("data:")
                        ? it.swatch_crop_b64
                        : `data:image/jpeg;base64,${it.swatch_crop_b64}`)
                    : null;
                  const materialSrc = it.material_view_b64
                    ? (it.material_view_b64.startsWith("data:")
                        ? it.material_view_b64
                        : `data:image/jpeg;base64,${it.material_view_b64}`)
                    : null;
                  const canEnlarge = !!(swatchSrc || it.color_hex);
                  const onOpen = () => {
                    if (!canEnlarge) return;
                    setView(materialSrc ? "material" : "catalogue");
                    setPreview({
                      src: swatchSrc,
                      material_src: materialSrc,
                      name: it.name,
                      hex: it.color_hex || null,
                      code: it.material_code || null,
                      zone: it.zone || null,
                      match_percent: typeof it.match_percent === "number" ? it.match_percent : null,
                    });
                  };
                  if (swatchSrc) {
                    return (
                      <button type="button" onClick={onOpen}
                        className="w-12 h-12 rounded-lg overflow-hidden border border-stone-border-soft flex-shrink-0 mt-0.5 hover:ring-2 hover:ring-charcoal/40 transition-all"
                        data-testid={`shortlist-swatch-${idx}`}
                        title="Click to enlarge">
                        <img src={swatchSrc} alt="" className="w-full h-full object-cover" />
                      </button>
                    );
                  }
                  if (it.color_hex) {
                    return (
                      <button type="button" onClick={onOpen}
                        className="w-12 h-12 rounded-lg border border-stone-border-soft flex-shrink-0 mt-0.5 hover:ring-2 hover:ring-charcoal/40 transition-all"
                        style={{ backgroundColor: it.color_hex }}
                        data-testid={`shortlist-swatch-${idx}`}
                        title="Click to enlarge" />
                    );
                  }
                  return (
                    <div className="w-9 h-9 rounded-lg bg-stone-panel grid place-items-center flex-shrink-0 mt-0.5">
                      <Icon className="w-4 h-4 text-charcoal" strokeWidth={1.5} />
                    </div>
                  );
                })()}
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-[10px] uppercase tracking-widest text-warm-grey font-semibold">
                      {SOURCE_LABEL[it.source_type] || it.source_type}
                    </span>
                    {it.zone && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-sand/40 text-charcoal">
                        {it.zone}
                      </span>
                    )}
                    {it.category && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-stone-panel text-charcoal">
                        {it.category}
                      </span>
                    )}
                    {typeof it.match_percent === "number" && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-sage-soft text-sage font-mono">
                        {it.match_percent}%
                      </span>
                    )}
                  </div>
                  <div className="font-display font-semibold text-charcoal text-base mt-0.5 leading-tight" data-testid={`shortlist-item-name-${idx}`}>
                    {it.name}
                  </div>
                  {it.source && (
                    <div className="text-xs text-warm-grey mt-1">
                      Source · {it.source}
                    </div>
                  )}
                  {it.notes && (
                    <p className="text-xs text-charcoal/70 mt-1.5 leading-relaxed italic line-clamp-2">
                      {it.notes}
                    </p>
                  )}
                  {it.external_url && (
                    <a
                      href={it.external_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-charcoal underline decoration-stone-border underline-offset-2 mt-2"
                    >
                      Open link <ExternalLink className="w-3 h-3" strokeWidth={2} />
                    </a>
                  )}
                </div>
              </div>
              <button
                type="button"
                onClick={() => onRemove(it)}
                className="text-warm-grey hover:text-charcoal p-1.5 rounded-full hover:bg-stone-panel flex-shrink-0"
                title="Remove from shortlist"
                data-testid={`shortlist-remove-${idx}`}
              >
                <X className="w-3.5 h-3.5" strokeWidth={1.5} />
              </button>
            </div>
          );
        })}
      </div>
      {/* Round 8 — Lightbox for enlarged swatch preview. */}
      {preview && (
        <div
          className="fixed inset-0 z-50 bg-charcoal/80 grid place-items-center p-4 backdrop-blur-sm animate-fade-in-up"
          onClick={() => setPreview(null)}
          data-testid="shortlist-swatch-lightbox"
        >
          <div
            className="bg-paper rounded-3xl shadow-hover w-full max-w-lg overflow-hidden border border-stone-border-soft"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-3 border-b border-stone-border-soft flex items-center justify-between bg-white">
              <div className="text-overline">Swatch preview</div>
              <button type="button" onClick={() => setPreview(null)}
                className="p-1.5 rounded-full hover:bg-stone-panel"
                data-testid="shortlist-lightbox-close" aria-label="Close">
                <X className="w-4 h-4" strokeWidth={1.5} />
              </button>
            </div>
            <div className="p-6">
              {preview.material_src && (
                <div
                  className="mb-4 inline-flex items-center gap-0.5 p-0.5 bg-stone-panel rounded-full text-[11px] font-medium"
                  role="tablist"
                  aria-label="Preview mode"
                  data-testid="shortlist-material-view-toggle"
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === "catalogue"}
                    onClick={() => setView("catalogue")}
                    className={`px-3 py-1.5 rounded-full transition-colors ${
                      view === "catalogue"
                        ? "bg-white text-charcoal shadow-soft"
                        : "text-warm-grey hover:text-charcoal"
                    }`}
                    data-testid="shortlist-toggle-catalogue-view"
                  >
                    Catalogue view
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === "material"}
                    onClick={() => setView("material")}
                    className={`px-3 py-1.5 rounded-full transition-colors ${
                      view === "material"
                        ? "bg-white text-charcoal shadow-soft"
                        : "text-warm-grey hover:text-charcoal"
                    }`}
                    title="Photorealistic render of the physical material"
                    data-testid="shortlist-toggle-material-view"
                  >
                    Material view
                  </button>
                </div>
              )}
              {(view === "material" && preview.material_src) ? (
                <img src={preview.material_src} alt={preview.name}
                  className="w-full aspect-square object-cover rounded-2xl border border-stone-border-soft"
                  data-testid="shortlist-lightbox-material-image" />
              ) : preview.src ? (
                <img src={preview.src} alt={preview.name}
                  className="w-full aspect-square object-cover rounded-2xl border border-stone-border-soft"
                  data-testid="shortlist-lightbox-image" />
              ) : (
                <div className="w-full aspect-square rounded-2xl border border-stone-border-soft"
                  style={{ backgroundColor: preview.hex || "#f5f2ec" }}
                  data-testid="shortlist-lightbox-hex" />
              )}
              {preview.material_src && view === "material" && (
                <div className="mt-2 text-[10px] uppercase tracking-widest text-warm-grey/70">
                  AI-rendered from the catalogue swatch
                </div>
              )}
              <div className="mt-4 space-y-1">
                <div className="font-display text-xl font-semibold text-charcoal leading-tight">
                  {preview.name}
                </div>
                <div className="flex flex-wrap gap-2 mt-2">
                  {preview.zone && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-sand/40 text-charcoal">{preview.zone}</span>
                  )}
                  {preview.code && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-stone-panel text-charcoal font-mono">Code · {preview.code}</span>
                  )}
                  {preview.hex && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-stone-panel text-charcoal font-mono">{preview.hex}</span>
                  )}
                  {typeof preview.match_percent === "number" && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-sage-soft text-sage font-mono">{preview.match_percent}% match</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
