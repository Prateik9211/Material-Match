import React from "react";
import { AlertTriangle, Sparkles } from "lucide-react";
import MatchCard from "./MatchCard";

/**
 * Results section of the Match page. Handles all 3 states:
 *   - loading skeleton (3 placeholder rows)
 *   - empty (no matches yet — pre-run)
 *   - hasResults: warnings + cards (or no-results-met-threshold fallback)
 */
export default function MatchResults({ busy, hasResults, result }) {
  if (busy && !hasResults) {
    return (
      <div className="space-y-4" data-testid="match-loading">
        {[0, 1, 2].map((i) => (
          <div key={i} className="bg-white border border-black/5 rounded-2xl h-32 shimmer"></div>
        ))}
      </div>
    );
  }

  if (!hasResults) {
    return (
      <div
        className="bg-white border border-dashed border-black/10 rounded-2xl p-12 text-center"
        data-testid="match-empty"
      >
        <Sparkles className="w-10 h-10 text-neutral-300 mx-auto mb-4" strokeWidth={1.25} />
        <h2 className="font-display text-2xl font-semibold mb-2">No matches yet</h2>
        <p className="text-sm text-neutral-500 max-w-md mx-auto">
          Add an optional prompt or catalogue, then hit{" "}
          <span className="font-medium text-black">Run Match</span> to generate the top 5
          candidate products.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="match-results">
      <div className="flex items-baseline justify-between">
        <h2 className="font-display text-2xl font-semibold">
          Top {result.matches.length} matches
        </h2>
        {result.generated_at && (
          <span className="text-xs text-neutral-500">
            Generated {new Date(result.generated_at).toLocaleString()}
          </span>
        )}
      </div>

      {result.warnings && result.warnings.length > 0 && (
        <div
          className="bg-amber-50 border border-amber-100 text-amber-900 rounded-2xl px-4 py-3 text-sm space-y-1"
          data-testid="match-warnings"
        >
          {result.warnings.map((w, i) => (
            <div key={`w-${i}`} className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" strokeWidth={1.75} />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {result.matches.length === 0 && (
        <div
          className="bg-white border border-dashed border-black/10 rounded-2xl p-10 text-center"
          data-testid="match-no-results"
        >
          <AlertTriangle className="w-8 h-8 text-amber-500 mx-auto mb-3" strokeWidth={1.25} />
          <p className="text-sm text-neutral-500">
            No strong matches found. Try a more specific catalogue section or upload
            clean product images from the same material family.
          </p>
        </div>
      )}

      {result.matches.map((m, i) => (
        <MatchCard key={m.id} match={m} index={i} />
      ))}
    </div>
  );
}
