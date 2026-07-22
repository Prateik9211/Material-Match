import React, { useEffect, useState } from "react";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Star } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { useNavigate } from "react-router-dom";

/**
 * Public-facing review submission form.
 * Authenticated users only — sends to POST /reviews.
 * Kept intentionally minimal: name (from user), optional role, star
 * rating 1-5, comment. No photo, no threading, no rich text.
 */
export default function LeaveReview() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [rating, setRating] = useState(0);
  const [role, setRole] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!user) navigate("/auth");
  }, [user, navigate]);

  const submit = async (e) => {
    e.preventDefault();
    if (rating < 1) { toast.error("Please pick a rating"); return; }
    if (!comment.trim()) { toast.error("Please write a short comment"); return; }
    setBusy(true);
    try {
      await api.post("/reviews", {
        rating, comment: comment.trim(), role: role.trim() || null,
      });
      setSubmitted(true);
      toast.success("Thanks! Your review has been submitted.");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-paper" data-testid="page-leave-review">
      <Header />
      <main className="max-w-2xl mx-auto px-6 py-16">
        <div className="text-overline mb-3">Feedback</div>
        <h1 className="font-display text-4xl font-bold tracking-tight text-charcoal mb-2">
          Leave a review
        </h1>
        <p className="text-warm-grey mb-8">
          Help us grow — a quick line about your experience means a lot.
        </p>

        {submitted ? (
          <div
            className="bg-sage-soft border border-sage/30 rounded-2xl p-8 text-center"
            data-testid="review-submitted"
          >
            <div className="font-display text-2xl font-semibold text-charcoal mb-2">
              Thank you.
            </div>
            <p className="text-sm text-warm-grey">
              Your review is now live on the landing page.
            </p>
          </div>
        ) : (
          <form
            onSubmit={submit}
            className="bg-white border border-stone-border-soft rounded-2xl p-8 shadow-soft space-y-6"
            data-testid="review-form"
          >
            <div>
              <label className="text-xs uppercase tracking-widest text-warm-grey font-semibold mb-2 block">
                Your rating
              </label>
              <div className="flex items-center gap-1" data-testid="rating-picker">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setRating(n)}
                    aria-label={`Rate ${n} out of 5 stars`}
                    className="p-1"
                    data-testid={`star-${n}`}
                  >
                    <Star
                      className={`w-8 h-8 transition-colors ${
                        n <= rating ? "fill-ochre text-ochre" : "text-neutral-300"
                      }`}
                      strokeWidth={1.75}
                    />
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="text-xs uppercase tracking-widest text-warm-grey font-semibold mb-2 block">
                Your role (optional)
              </label>
              <input
                type="text"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="Interior Designer, Architect, Student…"
                maxLength={120}
                className="w-full px-4 py-3 rounded-xl border border-stone-border-soft focus:border-charcoal focus:outline-none text-sm bg-white"
                data-testid="review-role-input"
              />
            </div>

            <div>
              <label className="text-xs uppercase tracking-widest text-warm-grey font-semibold mb-2 block">
                Your comment
              </label>
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="What did you like? What could be better?"
                rows={5}
                maxLength={1000}
                className="w-full px-4 py-3 rounded-xl border border-stone-border-soft focus:border-charcoal focus:outline-none text-sm bg-white resize-none"
                data-testid="review-comment-input"
              />
              <div className="text-[10px] text-warm-grey mt-1 text-right">
                {comment.length} / 1000
              </div>
            </div>

            <button
              type="submit"
              disabled={busy}
              className="w-full bg-charcoal text-paper hover:bg-charcoal/85 disabled:bg-neutral-300 rounded-full px-6 py-3 font-medium transition-colors"
              data-testid="review-submit-btn"
            >
              {busy ? "Submitting…" : "Submit review"}
            </button>
          </form>
        )}
      </main>
    </div>
  );
}
