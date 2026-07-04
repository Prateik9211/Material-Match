import React from "react";
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
                <div className="w-9 h-9 rounded-lg bg-stone-panel grid place-items-center flex-shrink-0 mt-0.5">
                  <Icon className="w-4 h-4 text-charcoal" strokeWidth={1.5} />
                </div>
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
    </section>
  );
}
