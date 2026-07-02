import React from "react";
import { ExternalLink, Sparkles, ShoppingBag, Package, Search } from "lucide-react";

const CATEGORY_LABEL = {
  lighting: "Lighting",
  furniture: "Furniture",
  decor: "Decor",
  art: "Art",
  "textile-decor": "Textile Decor",
  fixture: "Fixture",
  "plant-planter": "Plant / Planter",
  electronics: "Electronics",
  other: "Other",
};

function CategoryBadge({ category }) {
  return (
    <span className="inline-flex text-[10px] px-2 py-0.5 rounded-full bg-black text-white uppercase tracking-wider font-medium">
      {CATEGORY_LABEL[category] || category || "Other"}
    </span>
  );
}

function ConfidencePill({ value }) {
  const v = value || 0;
  const cls = v >= 85
    ? "bg-emerald-600"
    : v >= 70
      ? "bg-emerald-500"
      : v >= 55
        ? "bg-amber-500"
        : "bg-neutral-400";
  return (
    <span className={`inline-flex items-center gap-1 ${cls} text-white text-[11px] font-mono font-semibold px-2 py-0.5 rounded-full`}>
      {v}%
    </span>
  );
}

function KeywordChips({ items, testid }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1" data-testid={testid}>
      {items.slice(0, 4).map((k) => (
        <span key={k} className="inline-flex text-[10px] px-2 py-0.5 rounded-md bg-neutral-100 text-neutral-700 border border-neutral-200">
          {k}
        </span>
      ))}
    </div>
  );
}

function ProductCard({ product, index }) {
  const matched = product.matched_affiliate;
  const search = product.search_urls || {};
  return (
    <div
      className="bg-white border border-black/5 rounded-2xl shadow-soft hover:shadow-hover transition-all duration-300 hover:-translate-y-0.5 overflow-hidden flex flex-col"
      data-testid={`product-card-${index}`}
    >
      {/* Header strip with icon + category */}
      <div className="bg-[#F3F2EE] p-4 flex items-center justify-between border-b border-black/5">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-white border border-black/5 grid place-items-center">
            <Package className="w-4 h-4 text-neutral-700" strokeWidth={1.5} />
          </div>
          <CategoryBadge category={product.category} />
        </div>
        <ConfidencePill value={product.confidence} />
      </div>

      {/* Body */}
      <div className="p-5 flex-1 flex flex-col gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-neutral-900 leading-tight" data-testid={`product-name-${index}`}>
            {product.product_name}
          </h3>
          {product.description && (
            <p className="text-xs text-neutral-500 mt-1 leading-relaxed line-clamp-2">
              {product.description}
            </p>
          )}
        </div>

        {product.estimated_price_inr && (
          <div className="text-sm text-neutral-900 font-semibold" data-testid={`product-price-${index}`}>
            Est. {product.estimated_price_inr}
          </div>
        )}

        <div className="space-y-2">
          <KeywordChips items={product.style_keywords} testid={`product-style-${index}`} />
          {product.material_keywords?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {product.material_keywords.slice(0, 3).map((k) => (
                <span key={k} className="inline-flex text-[10px] px-2 py-0.5 rounded-md bg-amber-50 text-amber-800 border border-amber-100">
                  {k}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Curated match (if any) */}
        {matched && (
          <div className="mt-2 rounded-xl bg-emerald-50 border border-emerald-100 p-3" data-testid={`product-curated-${index}`}>
            <div className="flex items-center gap-1.5 mb-1.5">
              <Sparkles className="w-3.5 h-3.5 text-emerald-700" strokeWidth={2} />
              <span className="text-[10px] uppercase tracking-widest text-emerald-800 font-semibold">
                Curated recommendation
              </span>
            </div>
            <div className="text-sm text-emerald-900 font-medium leading-tight" data-testid={`product-curated-name-${index}`}>
              {matched.product_name}
            </div>
            <div className="flex items-center gap-2 mt-1 text-xs text-emerald-800">
              {matched.price_inr && <span className="font-semibold">{matched.price_inr}</span>}
              {matched.platform && <span>· {matched.platform}</span>}
            </div>
            <a
              href={matched.affiliate_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-emerald-900 hover:text-emerald-950 underline decoration-emerald-300 underline-offset-2"
              data-testid={`product-curated-link-${index}`}
            >
              View on {matched.platform || "store"}
              <ExternalLink className="w-3 h-3" strokeWidth={2} />
            </a>
          </div>
        )}

        {/* Search fallbacks */}
        <div className="mt-auto pt-2 flex items-center gap-2 flex-wrap border-t border-black/5">
          {search.amazon_in && (
            <a
              href={search.amazon_in}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-neutral-700 hover:text-black px-2.5 py-1 rounded-full border border-neutral-200 hover:border-neutral-400 transition-colors"
              data-testid={`product-search-amazon-${index}`}
            >
              <ShoppingBag className="w-3 h-3" strokeWidth={2} />
              Amazon.in
            </a>
          )}
          {search.google && (
            <a
              href={search.google}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-neutral-700 hover:text-black px-2.5 py-1 rounded-full border border-neutral-200 hover:border-neutral-400 transition-colors"
              data-testid={`product-search-google-${index}`}
            >
              <Search className="w-3 h-3" strokeWidth={2} />
              Google Shopping
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ProductsSection({ products }) {
  if (!products || products.length === 0) return null;
  const withCurated = products.filter((p) => p.matched_affiliate).length;
  return (
    <section data-testid="products-section" className="space-y-4">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <div>
          <div className="text-overline mb-1">Sprint 2</div>
          <h2 className="font-display text-2xl sm:text-3xl font-semibold tracking-tight">
            Products &amp; Fixtures
          </h2>
          <p className="text-sm text-neutral-500 mt-1">
            Shoppable products detected in this reference, matched to our curated Indian affiliate database.
          </p>
        </div>
        <div className="text-xs text-neutral-500" data-testid="products-count">
          {products.length} products · {withCurated} curated {withCurated === 1 ? "match" : "matches"}
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {products.map((p, i) => (
          <ProductCard key={p.id || `p-${i}`} product={p} index={i} />
        ))}
      </div>
    </section>
  );
}
