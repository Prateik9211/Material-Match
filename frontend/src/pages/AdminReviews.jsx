import React, { useCallback, useEffect, useState } from "react";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Star, Eye, EyeOff } from "lucide-react";

/**
 * Admin-only reviews inbox.
 * List all submitted reviews, most recent first. Founder can toggle
 * each row's `approved` flag to control which reviews are eligible for
 * future public display.
 */
export default function AdminReviews() {
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchReviews = useCallback(async () => {
    try {
      const { data } = await api.get("/admin/reviews");
      setReviews(data.reviews || []);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchReviews(); }, [fetchReviews]);

  const toggle = async (r) => {
    const next = !r.approved;
    // Optimistic update — revert on error.
    setReviews((cur) => cur.map((x) => x.id === r.id ? { ...x, approved: next } : x));
    try {
      await api.patch(`/admin/reviews/${r.id}`, { approved: next });
      toast.success(next ? "Review approved" : "Review hidden");
    } catch (err) {
      setReviews((cur) => cur.map((x) => x.id === r.id ? { ...x, approved: r.approved } : x));
      toast.error(formatApiError(err));
    }
  };

  const fmt = (iso) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  return (
    <div className="min-h-screen bg-paper" data-testid="page-admin-reviews">
      <Header />
      <main className="max-w-5xl mx-auto px-6 py-12">
        <div className="text-overline mb-2">Admin</div>
        <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight mb-2">
          Reviews
        </h1>
        <p className="text-warm-grey mb-8">
          User-submitted testimonials, most recent first. Toggle
          <span className="font-medium"> Approve</span> to mark a review as
          eligible for future public display.
        </p>

        {loading ? (
          <div className="text-warm-grey" data-testid="reviews-loading">Loading…</div>
        ) : reviews.length === 0 ? (
          <div className="bg-white border border-stone-border-soft rounded-2xl p-10 text-center text-warm-grey" data-testid="reviews-empty">
            No reviews submitted yet.
          </div>
        ) : (
          <div className="space-y-3" data-testid="reviews-list">
            {reviews.map((r, i) => (
              <article
                key={r.id}
                className="bg-white border border-stone-border-soft rounded-2xl p-5 shadow-soft"
                data-testid={`review-row-${i}`}
              >
                <div className="flex items-start justify-between gap-4 mb-2">
                  <div className="min-w-0">
                    <div className="font-semibold text-charcoal" data-testid={`review-name-${i}`}>
                      {r.user_name || r.user_email}
                      {r.role && <span className="ml-2 text-warm-grey font-normal">· {r.role}</span>}
                    </div>
                    <div className="text-[11px] text-warm-grey">
                      {r.user_email} · <span data-testid={`review-date-${i}`}>{fmt(r.created_at)}</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => toggle(r)}
                    aria-pressed={r.approved}
                    className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border transition-colors flex-shrink-0 ${
                      r.approved
                        ? "bg-sage text-white border-sage"
                        : "bg-white text-warm-grey border-stone-border-soft hover:border-charcoal"
                    }`}
                    data-testid={`review-toggle-${i}`}
                  >
                    {r.approved ? <><Eye className="w-3.5 h-3.5" strokeWidth={2} /> Approved</>
                                : <><EyeOff className="w-3.5 h-3.5" strokeWidth={2} /> Hidden</>}
                  </button>
                </div>

                <div className="flex items-center gap-0.5 mb-2" data-testid={`review-rating-${i}`} aria-label={`${r.rating} of 5 stars`}>
                  {[1,2,3,4,5].map((n) => (
                    <Star
                      key={n}
                      className={`w-4 h-4 ${n <= (r.rating || 0) ? "fill-ochre text-ochre" : "text-neutral-200"}`}
                      strokeWidth={1.75}
                    />
                  ))}
                </div>

                <p className="text-sm text-charcoal leading-relaxed whitespace-pre-line" data-testid={`review-comment-${i}`}>
                  {r.comment}
                </p>
              </article>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
