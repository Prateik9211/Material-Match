import React, { useState } from "react";
import { ChevronDown, ChevronRight, MapPin, ListChecks, Check, Star, Layers, Search, Target, Sparkles, Info } from "lucide-react";

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
      <span className="text-[9px] uppercase tracking-widest text-warm-grey w-20 shrink-0">{label}</span>
      <div className="flex-1 h-1 rounded-full bg-stone-panel overflow-hidden">
        <span className="block h-full bg-charcoal/70" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-charcoal w-8 text-right">{pct}%</span>
    </div>
  );
}

/** Compute a friendly "Why recommended" reason list from similarity numbers. */
function reasonsForRecommendation(match) {
  const sim = match.similarity || {};
  const out = [];
  if ((sim.color || 0) >= 80) out.push("Close colour match");
  if ((sim.visual || 0) >= 70) out.push("Similar visual pattern");
  if ((sim.finish || 0) >= 70) out.push("Similar finish");
  if ((sim.texture || 0) >= 70) out.push("Similar texture/grain");
  if (out.length < 2) out.push("Family match");
  return out.slice(0, 4);
}

function CatalogueMatchRow({ match, index, onAddToShortlist, shortlisted, isPaint, primary }) {
  const [openBreakdown, setOpenBreakdown] = useState(false);
  const sim = match.similarity || {};
  const shortlistName = `${match.brand} · ${match.material_name}`;
  const isShortlisted = shortlisted instanceof Set ? shortlisted.has(shortlistName) : !!shortlisted;
  const hasCode = !!match.material_code;
  const hasPage = !!match.page_number;
  return (
    <div
      className={`rounded-xl border p-3 transition-colors ${
        primary
          ? "bg-white border-charcoal/50 shadow-hover"
          : "bg-white border-stone-border-soft hover:border-charcoal/30"
      }`}
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
            {hasCode && (
              <span data-testid={`catalogue-code-${index}`}>Code · <span className="font-mono text-charcoal">{match.material_code}</span></span>
            )}
            {hasPage && (
              <span data-testid={`catalogue-page-${index}`}>Page · <span className="font-mono text-charcoal">{match.page_number}</span></span>
            )}
            {match.finish && <span>Finish · <span className="text-charcoal">{match.finish}</span></span>}
            {match.material_family && <span>Family · <span className="text-charcoal">{match.material_family}</span></span>}
          </div>
          <div className="mt-1.5 flex items-center gap-2 flex-wrap">
            <span className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full bg-stone-panel text-warm-grey border border-stone-border-soft">
              {match.source || "MaterialMatch Library"}
            </span>
            <button
              type="button"
              onClick={() => setOpenBreakdown((v) => !v)}
              className="text-[10px] text-warm-grey hover:text-charcoal underline-offset-2 hover:underline"
              data-testid={`catalogue-breakdown-toggle-${index}`}
            >
              {openBreakdown ? "Hide match details" : "View match details"}
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
              {isPaint && (
                <p className="text-[10px] text-warm-grey italic pt-1 leading-relaxed">
                  Approximate visual match. Confirm with a physical shade card before specification.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RecommendedCard({ match, index, onAddToShortlist, shortlisted, isPaint }) {
  const reasons = reasonsForRecommendation(match);
  const shortlistName = `${match.brand} · ${match.material_name}`;
  const isShortlisted = shortlisted instanceof Set ? shortlisted.has(shortlistName) : !!shortlisted;
  const hasCode = !!match.material_code;
  const hasPage = !!match.page_number;
  return (
    <div
      className="rounded-2xl border-2 border-ochre/40 bg-gradient-to-br from-ochre-soft/40 to-white p-4 shadow-hover"
      data-testid={`recommended-match-${index}`}
    >
      <div className="flex items-center gap-1.5 mb-3">
        <Star className="w-3.5 h-3.5 text-ochre fill-ochre" strokeWidth={1.75} />
        <span className="text-[10px] uppercase tracking-widest text-ochre font-semibold">Recommended Match</span>
      </div>
      <div className="flex items-start gap-3">
        <div
          className="w-20 h-20 rounded-xl shrink-0 border border-stone-border-soft shadow-inner"
          style={{ backgroundColor: match.color_hex || "#B7ADA0" }}
          data-testid={`recommended-swatch-${index}`}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-widest text-warm-grey/80">
                {match.brand} <span className="text-warm-grey/40">·</span> {match.catalogue}
              </div>
              <div className="font-display text-lg font-semibold text-charcoal leading-tight" data-testid={`recommended-name-${index}`}>
                {match.material_name}
              </div>
            </div>
            <span className="text-sm font-mono font-bold px-2.5 py-1 rounded-full bg-charcoal text-paper shrink-0" data-testid={`recommended-percent-${index}`}>
              {match.match_percent}%
            </span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-warm-grey">
            {hasCode && (<span>Code · <span className="font-mono text-charcoal">{match.material_code}</span></span>)}
            {hasPage && (<span>Page · <span className="font-mono text-charcoal">{match.page_number}</span></span>)}
            {match.finish && <span>Finish · <span className="text-charcoal">{match.finish}</span></span>}
            {match.material_family && <span>Family · <span className="text-charcoal">{match.material_family}</span></span>}
          </div>
          <div className="mt-2.5" data-testid={`recommended-why-${index}`}>
            <div className="text-[10px] uppercase tracking-widest text-charcoal/70 mb-1 font-semibold">Why recommended</div>
            <ul className="text-xs text-charcoal space-y-0.5 leading-snug">
              {reasons.map((r) => (
                <li key={r} className="flex items-start gap-1.5">
                  <Check className="w-3 h-3 text-sage mt-0.5 shrink-0" strokeWidth={2.5} />
                  <span>{r}</span>
                </li>
              ))}
            </ul>
            {isPaint && (
              <p className="mt-2 text-[10px] italic text-warm-grey leading-relaxed">
                Approximate visual match. Confirm with a physical shade card before specification.
              </p>
            )}
          </div>
          {onAddToShortlist && (
            <button
              type="button"
              onClick={() => onAddToShortlist(match)}
              disabled={isShortlisted}
              className={`mt-3 inline-flex items-center gap-1 text-[11px] px-3 py-1.5 rounded-full border transition-colors ${
                isShortlisted
                  ? "bg-sage-soft text-sage border-sage/30 cursor-default"
                  : "bg-charcoal text-paper border-charcoal hover:bg-charcoal/85"
              }`}
              data-testid={`recommended-shortlist-btn-${index}`}
            >
              {isShortlisted ? <><Check className="w-3 h-3" strokeWidth={2} /> Shortlisted</> : <><ListChecks className="w-3 h-3" strokeWidth={2} /> Add recommendation to shortlist</>}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function LikelySystemChip({ alt, index }) {
  return (
    <div
      className="rounded-lg border border-stone-border-soft bg-stone-panel/60 px-2.5 py-1.5"
      data-testid={`alt-system-${index}`}
    >
      <div className="text-xs font-medium text-charcoal leading-tight">{alt.name}</div>
      {alt.why && <div className="text-[10px] text-warm-grey mt-0.5 leading-snug">{alt.why}</div>}
    </div>
  );
}

function BrainReasoning({ brain, index }) {
  const [open, setOpen] = useState(false);
  const excluded = brain.excluded_libraries || [];
  const systems = brain.possible_construction_systems || [];
  return (
    <div className="rounded-xl border border-stone-border-soft bg-white" data-testid={`brain-reasoning-${index}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-left"
        data-testid={`brain-reasoning-toggle-${index}`}
      >
        <div className="flex items-center gap-1.5">
          <Info className="w-3 h-3 text-warm-grey" strokeWidth={1.75} />
          <span className="text-[10px] uppercase tracking-widest text-warm-grey/80 font-semibold">
            Why MaterialMatch searched this library
          </span>
        </div>
        {open ? <ChevronDown className="w-3 h-3 text-warm-grey" strokeWidth={2} /> : <ChevronRight className="w-3 h-3 text-warm-grey" strokeWidth={2} />}
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-2.5 text-xs text-charcoal border-t border-stone-border-soft" data-testid={`brain-reasoning-body-${index}`}>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 pt-2.5">
            <div>
              <div className="text-[9px] uppercase tracking-widest text-warm-grey/70">Classification</div>
              <div className="font-medium" data-testid={`brain-classification-${index}`}>{brain.classification}</div>
            </div>
            <div>
              <div className="text-[9px] uppercase tracking-widest text-warm-grey/70">Application</div>
              <div className="font-medium capitalize" data-testid={`brain-application-${index}`}>{brain.application_context}</div>
            </div>
            <div>
              <div className="text-[9px] uppercase tracking-widest text-warm-grey/70">Detected finish</div>
              <div className="font-medium" data-testid={`brain-finish-${index}`}>{brain.detected_finish}</div>
            </div>
            <div>
              <div className="text-[9px] uppercase tracking-widest text-warm-grey/70">Likely family</div>
              <div className="font-medium" data-testid={`brain-family-${index}`}>{brain.likely_material_family}</div>
            </div>
          </div>
          {systems.length > 0 && (
            <div>
              <div className="text-[9px] uppercase tracking-widest text-warm-grey/70 mb-1">Possible construction systems</div>
              <ol className="list-decimal list-inside space-y-0.5 text-[11px] leading-snug" data-testid={`brain-systems-${index}`}>
                {systems.map((s, i) => (
                  <li key={i}>
                    <span className="font-medium">{s.name}</span>
                    {s.note && <span className="text-warm-grey"> · {s.note}</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}
          {excluded.length > 0 && (
            <div>
              <div className="text-[9px] uppercase tracking-widest text-warm-grey/70 mb-1">Excluded libraries</div>
              <div className="flex flex-wrap gap-1" data-testid={`brain-excluded-${index}`}>
                {excluded.map((e) => (
                  <span key={e} className="text-[10px] px-1.5 py-0.5 rounded-full bg-stone-panel border border-stone-border-soft text-warm-grey">
                    {e}
                  </span>
                ))}
              </div>
            </div>
          )}
          {brain.reasoning_notes && (
            <div>
              <div className="text-[9px] uppercase tracking-widest text-warm-grey/70 mb-1">Reasoning</div>
              <p className="text-[11px] leading-relaxed text-charcoal/80" data-testid={`brain-reasoning-notes-${index}`}>
                {brain.reasoning_notes}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MaterialCard({ row, index, onAddToShortlist, shortlisted, onAddCatalogueToShortlist, shortlistedCatalogueIds, focused, onHoverCard }) {
  const [showMore, setShowMore] = useState(false);
  const [showAlts, setShowAlts] = useState(false);
  const classification = row.classification || "Material Surface";
  const isPaint = (row.material_family || "").toLowerCase() === "paint"
    || (row.zone || "").toLowerCase().includes("paint");
  const buckets = row.match_buckets || { best: [], possible: [], low: [] };
  // Sprint 8.2 — quality first. Never surface a "possible" or "low" match as
  // the recommended card just to fill space. Show recommendations only when
  // we have at least one match ≥ 75 (Best tier is ≥ 80; Possible ≥ 65). If
  // nothing clears the bar, the card renders "No high-confidence catalogue
  // match found." — better an honest empty state than a misleading suggestion.
  const HIGH_CONF_MIN = 75;
  const hasBest = Array.isArray(buckets.best) && buckets.best.length > 0;
  const strong = (row.catalogue_matches || []).filter((m) => (m.match_percent || 0) >= HIGH_CONF_MIN);
  const bestMatches = hasBest ? buckets.best.slice(0, 3) : strong.slice(0, 3);
  const recommended = bestMatches[0];
  const alternatives = bestMatches.slice(1, isPaint ? 4 : 4);
  const possible = buckets.possible || [];
  const shortlistedIds = shortlistedCatalogueIds || new Set();
  const likelySystems = row.alternative_systems || [];
  const visualLine = row.visual_reference || row.material_type;
  const likelyFamily = row.likely_family || row.material_family;

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
        </div>
      </div>
      <div className="p-5 space-y-4">
        {/* Visual Reference Analysis */}
        <div data-testid={`material-appearance-${index}`}>
          <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 flex items-center gap-1">
            <Sparkles className="w-3 h-3" strokeWidth={2} /> Visual Reference Analysis
          </div>
          <div className="font-display text-lg font-semibold text-charcoal leading-tight mt-0.5">
            {visualLine || "Detected material"}
          </div>
          {likelyFamily && (
            <div className="text-xs text-warm-grey mt-1">
              <span className="italic">Likely material family:</span> {likelyFamily}
            </div>
          )}
          {[row.color, row.texture, row.finish].filter(Boolean).length > 0 && (
            <div className="text-[11px] text-warm-grey/80 mt-0.5">
              {[row.color, row.texture, row.finish].filter(Boolean).join(" · ")}
            </div>
          )}
        </div>

        {/* Searched Libraries — trust signal explaining which library was searched */}
        {(row.searched_libraries && row.searched_libraries.length > 0) && (
          <div className="rounded-xl bg-stone-panel/70 border border-stone-border-soft px-3 py-2" data-testid={`searched-libraries-${index}`}>
            <div className="text-[9px] uppercase tracking-widest text-warm-grey/70 font-semibold mb-0.5">Searching</div>
            <div className="text-xs text-charcoal leading-snug">
              <span className="font-semibold">MaterialMatch Library</span> → {row.searched_libraries.join(" + ")}
            </div>
          </div>
        )}
        {(row.searched_libraries && row.searched_libraries.length === 0) && (
          <div className="rounded-xl bg-stone-panel/70 border border-stone-border-soft px-3 py-2 text-[11px] text-warm-grey italic" data-testid={`searched-libraries-${index}`}>
            No library confidently matches this region — try uploading a supplier PDF.
          </div>
        )}

        {/* Sprint 4 — MaterialMatch Brain Reasoning collapsible */}
        {row.brain && <BrainReasoning brain={row.brain} index={index} />}

        {/* Likely / Possible systems */}
        {likelySystems.length > 0 && (
          <div className="space-y-2" data-testid={`alt-systems-${index}`}>
            <button
              type="button"
              onClick={() => setShowAlts((v) => !v)}
              className="flex items-center gap-1.5 text-overline hover:text-charcoal transition-colors"
              data-testid={`alt-systems-toggle-${index}`}
            >
              <Layers className="w-3 h-3" strokeWidth={2} />
              Possible systems · {likelySystems.length}
              {showAlts ? <ChevronDown className="w-3 h-3" strokeWidth={2} /> : <ChevronRight className="w-3 h-3" strokeWidth={2} />}
            </button>
            {showAlts && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                {likelySystems.map((alt, i) => (
                  <LikelySystemChip key={i} alt={alt} index={`${index}-${i}`} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Recommended Match + Alternatives */}
        {recommended && (
          <div className="space-y-2.5" data-testid={`catalogue-matches-${index}`}>
            <div className="flex items-center gap-1.5 text-overline">
              <Search className="w-3 h-3" strokeWidth={2} />
              {isPaint ? "Closest Shade Matches" : "Closest Catalogue Matches"}
            </div>
            <RecommendedCard
              match={recommended}
              index={`${index}-0`}
              onAddToShortlist={onAddCatalogueToShortlist ? (mm) => onAddCatalogueToShortlist(row, mm) : null}
              shortlisted={shortlistedIds}
              isPaint={isPaint}
            />
            {alternatives.length > 0 && (
              <div className="space-y-2 pt-1">
                <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 font-semibold">
                  {isPaint ? "Also worth checking" : "Best alternatives"}
                </div>
                {alternatives.map((m, i) => (
                  <CatalogueMatchRow
                    key={m.id || i}
                    match={m}
                    index={`${index}-${i + 1}`}
                    onAddToShortlist={onAddCatalogueToShortlist ? (mm) => onAddCatalogueToShortlist(row, mm) : null}
                    shortlisted={shortlistedIds}
                    isPaint={isPaint}
                  />
                ))}
              </div>
            )}
            {possible.length > 0 && (
              <div>
                <button
                  type="button"
                  onClick={() => setShowMore((v) => !v)}
                  className="text-[11px] text-warm-grey hover:text-charcoal inline-flex items-center gap-1 pt-1"
                  data-testid={`view-more-matches-${index}`}
                >
                  {showMore ? <ChevronDown className="w-3 h-3" strokeWidth={2} /> : <ChevronRight className="w-3 h-3" strokeWidth={2} />}
                  {showMore ? "Hide possible alternatives" : `View more possible matches · ${possible.length}`}
                </button>
                {showMore && (
                  <div className="space-y-2 mt-2" data-testid={`possible-matches-${index}`}>
                    {possible.map((m, i) => (
                      <CatalogueMatchRow
                        key={m.id || `p-${i}`}
                        match={m}
                        index={`${index}-p-${i}`}
                        onAddToShortlist={onAddCatalogueToShortlist ? (mm) => onAddCatalogueToShortlist(row, mm) : null}
                        shortlisted={shortlistedIds}
                        isPaint={isPaint}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        {!recommended && (
          <div className="rounded-xl border border-dashed border-stone-border p-3 text-xs text-warm-grey flex items-start gap-2" data-testid={`no-strong-match-${index}`}>
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" strokeWidth={1.75} />
            <span>No high-confidence catalogue match found. Try uploading a related supplier PDF to enrich this zone.</span>
          </div>
        )}

        {/* Indian sourcing note */}
        {row.indian_alternative && (
          <div className="rounded-xl bg-ochre-soft/40 border border-ochre/20 p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <MapPin className="w-3.5 h-3.5 text-ochre" strokeWidth={2} />
              <span className="text-[10px] uppercase tracking-widest text-ochre font-semibold">Indian sourcing note</span>
            </div>
            <div className="text-xs text-charcoal leading-relaxed">{row.indian_alternative}</div>
          </div>
        )}

        {/* Card action — shortlist the zone-level spec */}
        {onAddToShortlist && (
          <div className="flex items-center gap-2 flex-wrap pt-1">
            <button
              type="button"
              onClick={() => onAddToShortlist(row)}
              disabled={shortlisted}
              className={`inline-flex items-center gap-1 text-[11px] px-3 py-1.5 rounded-full border transition-colors ${
                shortlisted
                  ? "bg-sage-soft text-sage border-sage/30 cursor-default"
                  : "bg-white text-charcoal border-stone-border hover:border-charcoal"
              }`}
              data-testid={`material-shortlist-btn-${index}`}
            >
              {shortlisted ? <><Check className="w-3 h-3" strokeWidth={2} /> Zone shortlisted</> : <><ListChecks className="w-3 h-3" strokeWidth={2} /> Add zone to shortlist</>}
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

/**
 * MaterialsFirstSection — Sprint 2 Refinement.
 * Each zone renders a Recommended Match on top of 2–3 best alternatives,
 * with weaker "possible" matches hidden behind a toggle. Language stays soft
 * ("marble-look", "wood-look") unless the seeded catalogue confirms.
 */
export default function MaterialsFirstSection({ rows, onAddToShortlist, shortlistedNames, ephemeral, title, subtitle, onAddCatalogueToShortlist, shortlistedCatalogueIds, focusedIndex, onHoverCard }) {
  if (!rows || rows.length === 0) return null;
  const shortlisted = shortlistedNames || new Set();
  return (
    <section data-testid="materials-section">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-4">
        <div>
          <div className="text-overline mb-1">{ephemeral ? "Zone Focus · Catalogue Search" : "Catalogue Search"}</div>
          <h2 className="font-display text-2xl sm:text-3xl font-semibold tracking-tight text-charcoal">
            {title || `${rows.length} zone${rows.length === 1 ? "" : "s"} · closest catalogue matches`}
          </h2>
          <p className="text-sm text-warm-grey mt-1 max-w-2xl">
            {subtitle || "For every detected zone we surface the 3–4 strongest catalogue matches. Similarity is fuzzy on purpose — MaterialMatch helps you specify, not certify."}
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
