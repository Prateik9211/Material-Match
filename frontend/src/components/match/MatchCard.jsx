import React from "react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { scoreBadgeStyle } from "@/lib/match-utils";

/**
 * Single result card. Renders thumbnail tile, product header, reasons,
 * optional disqualifier, and optional Indian-market alternative pill.
 */
export default function MatchCard({ match, index }) {
  const m = match;
  const i = index;
  return (
    <article
      className="bg-white border border-black/5 rounded-2xl shadow-soft hover:shadow-hover transition-all duration-300 overflow-hidden"
      data-testid={`match-card-${i}`}
    >
      <div className="grid grid-cols-12 gap-0">
        {m.thumb_b64 ? (
          <div
            className="col-span-3 sm:col-span-2 bg-neutral-100 relative"
            data-testid={`match-thumb-${i}`}
          >
            <img
              src={m.thumb_b64}
              alt={`${m.catalogue_ref} page ${m.page_number}`}
              className="absolute inset-0 w-full h-full object-cover"
            />
            {m.page_number && (
              <span className="absolute bottom-1 right-1 text-[10px] font-mono bg-black/80 text-white px-1.5 py-0.5 rounded">
                p{m.page_number}
              </span>
            )}
          </div>
        ) : (
          <div
            className="col-span-3 sm:col-span-2 grain relative"
            style={{ background: m.thumbnail_color }}
            data-testid={`match-thumb-${i}`}
          >
            <div className="absolute inset-0 grid place-items-center">
              <span className="font-display font-bold text-white/70 text-2xl">
                {(m.product_name || "?")[0]}
              </span>
            </div>
          </div>
        )}

        <div className="col-span-9 sm:col-span-10 p-5 space-y-3">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <h3 className="font-display text-lg font-semibold truncate">{m.product_name}</h3>
              <p className="text-xs text-neutral-500 mt-0.5 truncate">
                {m.catalogue_ref}
                {m.page_number ? ` · page ${m.page_number}` : ""}
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span
                className={`${scoreBadgeStyle(m.score_label)} text-xs font-semibold px-2.5 py-1 rounded-full`}
                data-testid={`match-label-${i}`}
              >
                {m.score_label}
              </span>
              <span
                className="font-mono font-bold text-base bg-black text-white px-2.5 py-1 rounded-full"
                data-testid={`match-percent-${i}`}
              >
                {m.match_percent}%
              </span>
            </div>
          </div>

          <ul className="space-y-1.5" data-testid={`match-reasons-${i}`}>
            {(m.reasons || []).map((reason, j) => (
              <li key={`r-${j}`} className="flex items-start gap-2 text-sm text-neutral-700">
                <CheckCircle2
                  className="w-4 h-4 mt-0.5 text-emerald-600 shrink-0"
                  strokeWidth={1.75}
                />
                {reason}
              </li>
            ))}
          </ul>

          {m.disqualifier && (
            <div
              className="flex items-start gap-2 text-xs bg-amber-50 text-amber-900 border border-amber-100 rounded-lg px-3 py-2"
              data-testid={`match-disqualifier-${i}`}
            >
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" strokeWidth={1.75} />
              {m.disqualifier}
            </div>
          )}

          {m.indian_alternative && (
            <div
              className="text-xs italic text-amber-900 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 leading-snug"
              data-testid={`match-indian-alt-${i}`}
              title="AI-suggested Indian-market equivalent"
            >
              <span className="not-italic font-semibold mr-1">India alt:</span>
              {m.indian_alternative}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
