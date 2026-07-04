import React, { useState } from "react";
import { ChevronDown, ChevronRight, MapPin, ListChecks, Check, BookOpen, ArrowUpRight, Layers, Search, Target } from "lucide-react";

const CLASSIFICATION_STYLE = {
  "Material Surface": "bg-sand/60 text-charcoal border-stone-border-soft",
  "Product": "bg-ochre-soft text-ochre border-ochre/30",
  "Fixture": "bg-sage-soft text-sage border-sage/30",
  "Decor": "bg-white text-charcoal border-stone-border-soft",
  "Mixed": "bg-charcoal text-paper border-charcoal",
  "Unclear": "bg-stone-panel text-warm-grey border-stone-border-soft",
};

function SimilarityBar({ label, value }) {
  const pct = Math.max(0, Math.min(100, value || 0));
  return (
    <div className="flex items-center gap-2" data-testid={`similarity-${label.toLowerCase()}`}>
      <span className="text-[9px] uppercase tracking-widest text-warm-grey w-14 shrink-0">{label}</span>
      <div className="flex-1 h-1 rounded-full bg-stone-panel overflow-hidden">
        <span className="block h-full bg-charcoal/70" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-charcoal w-8 text-right">{pct}%</span>
    </div>
  );
}

function CatalogueMatchRow({ match, index, onAddToShortlist, shortlisted }) {
  const [openBreakdown, setOpenBreakdown] = useState(false);
  const sim = match.similarity || {};
  const shortlistName = `${match.brand} · ${match.material_name}`;
  const isShortlisted = shortlisted instanceof Set ? shortlisted.has(shortlistName) : !!shortlisted;
  return (
    <div
      className="rounded-xl border border-stone-border-soft bg-white p-3 hover:border-charcoal/30 transition-colors"
      data-testid={`catalogue-match-${index}`}
    >
      <div className="flex items-start gap-3">
        <div
          className="w-14 h-14 rounded-lg shrink-0 border border-stone-border-soft shadow-inner"
          style={{ backgroundColor: match.color_hex || "#B7ADA0" }}
          title={match.color_name || ""}
          data-testid={`catalogue-swatch-${index}`}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 truncate">
                {match.brand} <span className="text-warm-grey/40">·</span> {match.catalogue}
              </div>
              <div className="text-sm font-semibold text-charcoal leading-tight truncate" data-testid={`catalogue-name-${index}`}>
                {match.material_name}
              </div>
            </div>
            <span
              className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded-full bg-charcoal text-paper shrink-0"
              data-testid={`catalogue-match-percent-${index}`}
            >
              {match.match_percent}%
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-warm-grey">
            <span data-testid={`catalogue-code-${index}`}>
              {match.material_code
                ? <>Code · <span className="font-mono text-charcoal">{match.material_code}</span></>
                : <span className="italic">Code unavailable in current database</span>}
            </span>
            <span data-testid={`catalogue-page-${index}`}>
              {match.page_number ? <>Page · <span className="font-mono text-charcoal">{match.page_number}</span></> : "Page unavailable"}
            </span>
            {match.finish && <span>Finish · <span className="text-charcoal">{match.finish}</span></span>}
            {match.material_family && <span>Family · <span className="text-charcoal">{match.material_family}</span></span>}
          </div>
          <div className="mt-1.5 flex items-center gap-2 flex-wrap">
            <span className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full bg-stone-panel text-warm-grey border border-stone-border-soft">
              {match.source || "Global Library"}
            </span>
            <button
              type="button"
              onClick={() => setOpenBreakdown((v) => !v)}
              className="text-[10px] text-warm-grey hover:text-charcoal underline-offset-2 hover:underline"
              data-testid={`catalogue-breakdown-toggle-${index}`}
            >
              {openBreakdown ? "Hide similarity" : "Similarity breakdown"}
            </button>
            {onAddToShortlist && (
              <button
                type="button"
                onClick={() => onAddToShortlist(match)}
                disabled={isShortlisted}
                className={`ml-auto inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-full border transition-colors ${
                  isShortlisted
                    ? "bg-sage-soft text-sage border-sage/30 cursor-default"
                    : "bg-white text-charcoal border-stone-border hover:border-charcoal"
                }`}
                data-testid={`catalogue-shortlist-btn-${index}`}
              >
                {isShortlisted ? <><Check className="w-3 h-3" strokeWidth={2} /> Added</> : <><ListChecks className="w-3 h-3" strokeWidth={2} /> Shortlist</>}
              </button>
            )}
          </div>
          {openBreakdown && (
            <div className="mt-2 space-y-1 pt-2 border-t border-stone-border-soft" data-testid={`catalogue-breakdown-${index}`}>
              <SimilarityBar label="Visual" value={sim.visual} />
              <SimilarityBar label="Colour" value={sim.color} />
              <SimilarityBar label="Finish" value={sim.finish} />
              <SimilarityBar label="Texture" value={sim.texture} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AlternativeSystemChip({ alt, index }) {
  return (
    <div
      className="rounded-lg border border-stone-border-soft bg-stone-panel/60 p-2.5"
      data-testid={`alt-system-${index}`}
    >
      <div className="text-xs font-medium text-charcoal">{alt.name}</div>
      {alt.why && <div className="text-[10px] text-warm-grey mt-0.5 leading-relaxed">{alt.why}</div>}
    </div>
  );
}

function MaterialCard({ row, index, onAddToShortlist, shortlisted, onMatchCatalogue, hasCatalogueMatch, ephemeral, onAddCatalogueToShortlist, shortlistedCatalogueIds, focused, onHoverCard }) {
  const [catalogueOpen, setCatalogueOpen] = useState(true);
  const [altsOpen, setAltsOpen] = useState(false);
  const matches = row.catalogue_matches || [];
  const altSystems = row.alternative_systems || [];
  const classification = row.classification || "Material Surface";
  const shortlistedIds = shortlistedCatalogueIds || new Set();
  const isProductClass = classification === "Product" || classification === "Fixture" || classification === "Mixed";

  return (
    <article
      className={`bg-white border rounded-2xl overflow-hidden shadow-soft hover:shadow-hover transition-all ${
        focused ? "border-charcoal ring-2 ring-charcoal/10" : "border-stone-border-soft"
      }`}
      data-testid={`material-card-${index}`}
      onMouseEnter={() => onHoverCard && onHoverCard(index)}
      onMouseLeave={() => onHoverCard && onHoverCard(null)}
    >
      <div className="bg-stone-panel px-5 py-3 border-b border-stone-border-soft flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="w-6 h-6 rounded-full bg-charcoal text-paper text-[10px] font-mono grid place-items-center shrink-0"
            data-testid={`material-pin-${index}`}
          >
            {index + 1}
          </span>
          <div className="text-overline truncate">{row.zone || `Zone ${index + 1}`}</div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span
            className={`text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold ${CLASSIFICATION_STYLE[classification] || CLASSIFICATION_STYLE["Material Surface"]}`}
            data-testid={`material-classification-${index}`}
          >
            {classification}
          </span>
          {typeof row.confidence === "number" && (
            <span className="text-[10px] font-mono text-warm-grey">{row.confidence}%</span>
          )}
        </div>
      </div>
      <div className="p-5 space-y-4">
        {/* Detected Appearance */}
        <div data-testid={`material-appearance-${index}`}>
          <div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Detected appearance</div>
          <div className="font-display text-lg font-semibold text-charcoal leading-tight mt-0.5">
            {row.material_type || row.material_family || "Detected material"}
          </div>
          <div className="text-xs text-warm-grey mt-1 leading-relaxed">
            {[row.color, row.texture, row.finish].filter(Boolean).join(" · ")}
          </div>
        </div>

        {/* Top Catalogue Matches */}
        {matches.length > 0 && (
          <div className="space-y-2" data-testid={`catalogue-matches-${index}`}>
            <button
              type="button"
              onClick={() => setCatalogueOpen((v) => !v)}
              className="flex items-center gap-1.5 text-overline hover:text-charcoal transition-colors"
              data-testid={`catalogue-toggle-${index}`}
            >
              <Search className="w-3 h-3" strokeWidth={2} />
              {isProductClass ? "Closest Product Matches" : "Closest Catalogue Matches"} · {matches.length}
              {catalogueOpen ? <ChevronDown className="w-3 h-3" strokeWidth={2} /> : <ChevronRight className="w-3 h-3" strokeWidth={2} />}
            </button>
            {catalogueOpen && (
              <div className="space-y-2">
                {matches.map((m, i) => (
                  <CatalogueMatchRow
                    key={m.id || i}
                    match={m}
                    index={`${index}-${i}`}
                    onAddToShortlist={onAddCatalogueToShortlist
                      ? (mm) => onAddCatalogueToShortlist(row, mm)
                      : null}
                    shortlisted={shortlistedIds}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Alternative Material Systems */}
        {altSystems.length > 0 && (
          <div className="space-y-2 pt-1" data-testid={`alt-systems-${index}`}>
            <button
              type="button"
              onClick={() => setAltsOpen((v) => !v)}
              className="flex items-center gap-1.5 text-overline hover:text-charcoal transition-colors"
              data-testid={`alt-systems-toggle-${index}`}
            >
              <Layers className="w-3 h-3" strokeWidth={2} />
              Alternative Material Systems · {altSystems.length}
              {altsOpen ? <ChevronDown className="w-3 h-3" strokeWidth={2} /> : <ChevronRight className="w-3 h-3" strokeWidth={2} />}
            </button>
            {altsOpen && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                {altSystems.map((alt, i) => (
                  <AlternativeSystemChip key={i} alt={alt} index={`${index}-${i}`} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Indian sourcing quick note */}
        {row.indian_alternative && (
          <div className="rounded-xl bg-ochre-soft/40 border border-ochre/20 p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <MapPin className="w-3.5 h-3.5 text-ochre" strokeWidth={2} />
              <span className="text-[10px] uppercase tracking-widest text-ochre font-semibold">Indian sourcing note</span>
            </div>
            <div className="text-xs text-charcoal leading-relaxed">{row.indian_alternative}</div>
          </div>
        )}

        {/* Card actions */}
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
              {shortlisted ? <><Check className="w-3 h-3" strokeWidth={2} /> Shortlisted</> : <><ListChecks className="w-3 h-3" strokeWidth={2} /> Add zone to shortlist</>}
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
              {hasCatalogueMatch ? "Open PDF catalogue match" : "Match against uploaded PDFs"}
              <ArrowUpRight className="w-3 h-3" strokeWidth={2} />
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

/**
 * MaterialsFirstSection — Sprint 2 Revision (Catalogue-First).
 * Each detected zone renders:
 *   1. Classification badge
 *   2. Detected appearance (short label)
 *   3. Top catalogue matches (5–10) with similarity breakdown
 *   4. Alternative material systems (category-level swaps)
 * Every card has a numeric pin badge so the reference image can overlay
 * numbered dots at row.pin coordinates.
 */
export default function MaterialsFirstSection({ rows, onAddToShortlist, shortlistedNames, onMatchCatalogue, matchResults, ephemeral, title, subtitle, onAddCatalogueToShortlist, shortlistedCatalogueIds, focusedIndex, onHoverCard }) {
  if (!rows || rows.length === 0) return null;
  const shortlisted = shortlistedNames || new Set();
  const matches = matchResults || {};
  return (
    <section data-testid="materials-section">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-4">
        <div>
          <div className="text-overline mb-1">{ephemeral ? "Zone Focus · Catalogue Search" : "Catalogue Search"}</div>
          <h2 className="font-display text-2xl sm:text-3xl font-semibold tracking-tight text-charcoal">
            {title || `${rows.length} zone${rows.length === 1 ? "" : "s"} · closest catalogue matches`}
          </h2>
          <p className="text-sm text-warm-grey mt-1 max-w-2xl">
            {subtitle || "For every detected zone, MaterialMatch searches the seeded catalogue and returns the closest available materials — with brand, code, page, match % and similarity breakdown."}
          </p>
        </div>
        <div className="inline-flex items-center gap-2 text-xs text-warm-grey" data-testid="materials-summary">
          <Target className="w-3.5 h-3.5" strokeWidth={1.5} />
          {ephemeral ? "Ephemeral · region-only" : "Detect → Search → Compare → Decide"}
        </div>
      </div>
      <div className="grid lg:grid-cols-2 gap-4" data-testid="materials-grid">
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
            onAddCatalogueToShortlist={onAddCatalogueToShortlist}
            shortlistedCatalogueIds={shortlistedCatalogueIds}
            focused={focusedIndex === i}
            onHoverCard={onHoverCard}
          />
        ))}
      </div>
    </section>
  );
}
