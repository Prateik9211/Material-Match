import React, { useEffect, useState, useRef } from "react";
import { ExternalLink, ShoppingBag, Loader2 } from "lucide-react";
import axios from "axios";

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * Shows visually-similar shoppable products for a detected fixture/furniture
 * item, sourced from SerpApi Google Lens.
 *
 * Honest UX framing per founder spec (2026-02-08):
 *   - Header always reads "Similar items · visually similar, not exact match"
 *   - Silent when the quality gate blocks the crop (busy/tiny/tall crops)
 *   - Silent when Google Lens returns zero shoppable results
 *   - Never implies SKU-level identification
 */
export default function SimilarItems({ projectId, productId, autoLoad = true }) {
  const [state, setState] = useState({ status: "idle", data: null });
  const abortRef = useRef(null);

  useEffect(() => {
    if (!autoLoad || !projectId || !productId) return;
    if (abortRef.current) abortRef.current.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setState({ status: "loading", data: null });
    axios
      .post(`${API}/api/projects/${projectId}/products/${productId}/similar`, {}, {
        signal: ctrl.signal,
        timeout: 55_000,
      })
      .then((r) => setState({ status: "loaded", data: r.data }))
      .catch((e) => {
        if (axios.isCancel(e) || e.name === "CanceledError") return;
        setState({ status: "error", data: { error: e.message || "request failed" } });
      });
    return () => ctrl.abort();
  }, [projectId, productId, autoLoad]);

  // Nothing to show → render nothing (silent gate/empty per founder spec).
  if (state.status === "idle") return null;
  if (state.status === "loading") {
    return (
      <div className="mt-3 pt-3 border-t border-black/5" data-testid={`similar-loading-${productId}`}>
        <div className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold mb-2 flex items-center gap-1.5">
          <Loader2 className="w-3 h-3 animate-spin" strokeWidth={2} />
          Finding similar items…
        </div>
      </div>
    );
  }

  const d = state.data || {};
  const items = d.similar_items || [];
  const gate = d.gate || {};
  const err = d.error || (state.status === "error" ? d.error : null);

  // Silence: gate blocked, or empty results, or hard error.
  if (!gate.passed || items.length === 0 || err) {
    return null;
  }

  return (
    <div
      className="mt-3 pt-3 border-t border-black/5"
      data-testid={`similar-items-${productId}`}
    >
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold flex items-center gap-1.5">
          <ShoppingBag className="w-3 h-3" strokeWidth={2} />
          Similar items
        </div>
        <span
          className="text-[9px] text-neutral-400 italic"
          title="Visually similar products found by Google Lens — not exact SKU matches"
        >
          visually similar, not exact match
        </span>
      </div>
      <ul className="space-y-2" data-testid={`similar-items-list-${productId}`}>
        {items.slice(0, 4).map((it, idx) => (
          <SimilarItemRow key={idx} item={it} index={idx} productId={productId} />
        ))}
      </ul>
      {d.cached && (
        <div className="mt-1.5 text-[9px] text-neutral-400" data-testid={`similar-cached-${productId}`}>
          (cached result — no search credit used)
        </div>
      )}
    </div>
  );
}

function SimilarItemRow({ item, index, productId }) {
  const price = item.price_display;
  return (
    <li
      className="flex items-start gap-2.5 py-1.5 group"
      data-testid={`similar-item-${productId}-${index}`}
    >
      {item.thumbnail ? (
        <img
          src={item.thumbnail}
          alt=""
          loading="lazy"
          className="w-11 h-11 object-cover rounded-md border border-black/5 flex-shrink-0 bg-neutral-100"
          onError={(e) => {
            e.currentTarget.style.visibility = "hidden";
          }}
        />
      ) : (
        <div className="w-11 h-11 rounded-md bg-neutral-100 border border-black/5 flex-shrink-0" />
      )}
      <div className="flex-1 min-w-0">
        <a
          href={item.link}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-neutral-800 hover:text-black line-clamp-2 leading-snug font-medium group-hover:underline"
          data-testid={`similar-item-link-${productId}-${index}`}
        >
          {item.title || "Untitled listing"}
          <ExternalLink
            className="inline-block w-2.5 h-2.5 ml-0.5 -translate-y-0.5 text-neutral-400"
            strokeWidth={2}
          />
        </a>
        <div className="flex items-center gap-1.5 mt-0.5 text-[10px]">
          {item.source && (
            <span
              className="text-neutral-500"
              data-testid={`similar-item-source-${productId}-${index}`}
            >
              {item.source}
            </span>
          )}
          {price && (
            <>
              <span className="text-neutral-300">·</span>
              <span
                className="font-semibold text-neutral-900"
                data-testid={`similar-item-price-${productId}-${index}`}
              >
                {price}
              </span>
            </>
          )}
        </div>
      </div>
    </li>
  );
}
