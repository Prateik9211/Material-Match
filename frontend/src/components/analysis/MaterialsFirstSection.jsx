import React, { useState } from "react";
import { ChevronDown, ChevronRight, MapPin, Sparkles, ListChecks, Check, BookOpen, ArrowUpRight } from "lucide-react";

const COST_STYLE = {
  budget: "bg-sage-soft text-sage border-sage/30",
  mid: "bg-sand/50 text-charcoal border-stone-border-soft",
  premium: "bg-ochre-soft text-ochre border-ochre/30",
};

function AlternativeRow({ alt, index, onAdd, added }) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-lg bg-white border border-stone-border-soft" data-testid={`material-alt-${index}`}>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <div className="font-medium text-charcoal text-sm">{alt.name}</div>
          {alt.cost_tier && (
            <span className={`text-[10px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold ${COST_STYLE[alt.cost_tier] || COST_STYLE.mid}`}>
              {alt.cost_tier}
            </span>
          )}
        </div>
        {alt.why && <p className="text-xs text-warm-grey mt-1 leading-relaxed">{alt.why}</p>}
        <div className="mt-1.5 flex flex-wrap gap-1 text-[10px] text-charcoal/70">
          {alt.durability && <span>Durability · {alt.durability}</span>}
          {alt.maintenance && <span>· Care · {alt.maintenance}</span>}
        </div>
        {alt.brands_to_check && alt.brands_to_check.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {alt.brands_to_check.slice(0, 4).map((b) => (
              <span key={b} className="text-[10px] px-1.5 py-0.5 rounded bg-stone-panel text-charcoal">{b}</span>
            ))}
          </div>
        )}
      </div>
      {onAdd && (
        <button
          type="button"
          onClick={() => onAdd(alt)}
          disabled={added}
          className={`text-[10px] px-2 py-1 rounded-full border transition-colors flex items-center gap-1 flex-shrink-0 ${
            added
              ? "bg-sage-soft text-sage border-sage/30 cursor-default"
              : "bg-charcoal text-paper border-charcoal hover:bg-charcoal/85"
          }`}
          data-testid={`material-alt-shortlist-${index}`}
        >
          {added ? <><Check className="w-3 h-3" strokeWidth={2} /> Added</> : <><ListChecks className="w-3 h-3" strokeWidth={2} /> Shortlist</>}
        </button>
      )}
    </div>
  );
}

function MaterialCard({ row, index, onAddToShortlist, shortlisted, onMatchCatalogue, hasCatalogueMatch, ephemeral }) {
  const [expanded, setExpanded] = useState(false);
  const alternatives = row.alternatives || [];
  const hasAlts = alternatives.length > 0;

  return (
    <article
      className="bg-white border border-stone-border-soft rounded-2xl overflow-hidden shadow-soft hover:shadow-hover transition-shadow"
      data-testid={`material-card-${index}`}
    >
      <div className="bg-stone-panel px-5 py-3 border-b border-stone-border-soft flex items-baseline justify-between">
        <div className="text-overline">{row.zone || `Zone ${index + 1}`}</div>
        {typeof row.confidence === "number" && (
          <span className="text-[10px] font-mono text-warm-grey">{row.confidence}%</span>
        )}
      </div>
      <div className="p-5 space-y-3">
        <div>
          <div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Material Type</div>
          <div className="font-display text-lg font-semibold text-charcoal leading-tight mt-0.5">
            {row.material_type || row.material_family || "Material"}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          {row.finish && <div><div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Finish</div><div className="text-charcoal">{row.finish}</div></div>}
          {row.color && <div><div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Color</div><div className="text-charcoal">{row.color}</div></div>}
          {row.texture && <div><div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Texture</div><div className="text-charcoal">{row.texture}</div></div>}
          {row.material_family && <div><div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Family</div><div className="text-charcoal">{row.material_family}</div></div>}
        </div>
        {row.indian_alternative && (
          <div className="rounded-xl bg-ochre-soft/60 border border-ochre/30 p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <MapPin className="w-3.5 h-3.5 text-ochre" strokeWidth={2} />
              <span className="text-[10px] uppercase tracking-widest text-ochre font-semibold">Recommended Indian Options</span>
            </div>
            <div className="text-sm text-charcoal leading-relaxed">{row.indian_alternative}</div>
            {row.brands_to_check && row.brands_to_check.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {row.brands_to_check.slice(0, 6).map((b) => (
                  <span key={b} className="text-[10px] px-2 py-0.5 rounded-full bg-paper text-charcoal border border-stone-border-soft">{b}</span>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="flex items-center gap-2 flex-wrap pt-1">
          {onAddToShortlist && (
            <button
              type="button"
              onClick={() => onAddToShortlist(row)}
              disabled={shortlisted}
              className={`inline-flex items-center gap-1 text-[11px] px-3 py-1.5 rounded-full border transition-colors ${
                shortlisted
                  ? "bg-sage-soft text-sage border-sage/30 cursor-default"
                  : "bg-charcoal text-paper border-charcoal hover:bg-charcoal/85"
              }`}
              data-testid={`material-shortlist-btn-${index}`}
            >
              {shortlisted ? <><Check className="w-3 h-3" strokeWidth={2} /> Shortlisted</> : <><ListChecks className="w-3 h-3" strokeWidth={2} /> Add to Shortlist</>}
            </button>
          )}
          {hasAlts && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="inline-flex items-center gap-1 text-[11px] px-3 py-1.5 rounded-full border border-stone-border text-charcoal hover:border-charcoal transition-colors"
              data-testid={`material-alts-toggle-${index}`}
            >
              {expanded ? <ChevronDown className="w-3 h-3" strokeWidth={2} /> : <ChevronRight className="w-3 h-3" strokeWidth={2} />}
              {alternatives.length} alternatives
            </button>
          )}
          {onMatchCatalogue && !ephemeral && (
            <button
              type="button"
              onClick={() => onMatchCatalogue(row)}
              className="inline-flex items-center gap-1 text-[11px] px-3 py-1.5 rounded-full bg-white text-charcoal border border-stone-border hover:border-charcoal transition-colors"
              data-testid={`material-match-catalogue-${index}`}
            >
              <BookOpen className="w-3 h-3" strokeWidth={2} />
              {hasCatalogueMatch ? "View catalogue match" : "Match with Catalogue"}
              <ArrowUpRight className="w-3 h-3" strokeWidth={2} />
            </button>
          )}
        </div>
        {expanded && hasAlts && (
          <div className="pt-3 mt-2 border-t border-stone-border-soft space-y-2" data-testid={`material-alts-${index}`}>
            <div className="text-overline mb-1">Material Alternatives</div>
            {alternatives.map((alt, i) => (
              <AlternativeRow
                key={`${index}-${i}`}
                alt={alt}
                index={`${index}-${i}`}
                onAdd={onAddToShortlist ? (a) => onAddToShortlist({ ...row, material_type: a.name, _fromAlt: true, alt_meta: a }) : null}
              />
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

/**
 * MaterialsFirstSection — Sprint 7 hero. Renders detected material zones as
 * cards with expandable Material Alternatives. Adds a materials-first summary
 * strip at the top.
 */
export default function MaterialsFirstSection({ rows, onAddToShortlist, shortlistedNames, onMatchCatalogue, matchResults, ephemeral, title, subtitle }) {
  if (!rows || rows.length === 0) return null;
  const shortlisted = shortlistedNames || new Set();
  const matches = matchResults || {};
  return (
    <section data-testid="materials-section">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-4">
        <div>
          <div className="text-overline mb-1">{ephemeral ? "Zone Focus" : "Materials First"}</div>
          <h2 className="font-display text-2xl sm:text-3xl font-semibold tracking-tight text-charcoal">
            {title || `${rows.length} specification ${rows.length === 1 ? "zone" : "zones"} detected`}
          </h2>
          <p className="text-sm text-warm-grey mt-1 max-w-2xl">
            {subtitle || "Each zone comes with realistic alternatives across cost tiers — a shortlist starter for vendor visits."}
          </p>
        </div>
        <div className="inline-flex items-center gap-2 text-xs text-warm-grey" data-testid="materials-summary">
          <Sparkles className="w-3.5 h-3.5" strokeWidth={1.5} />
          {ephemeral ? "Ephemeral · region-only" : "Materials before Products"}
        </div>
      </div>
      <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4" data-testid="materials-grid">
        {rows.map((r, i) => (
          <MaterialCard
            key={r.zone || i}
            row={r}
            index={i}
            onAddToShortlist={onAddToShortlist}
            shortlisted={shortlisted.has(r.material_type || r.material_name || r.zone)}
            onMatchCatalogue={onMatchCatalogue}
            hasCatalogueMatch={!!matches[r.zone]}
            ephemeral={ephemeral}
          />
        ))}
      </div>
    </section>
  );
}
