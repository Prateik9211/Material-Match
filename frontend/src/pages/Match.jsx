import React, { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import Header from "@/components/Header";
import UploadZone from "@/components/UploadZone";
import DemoModeBanner from "@/components/DemoModeBanner";
import api, { formatApiError } from "@/lib/api";
import { ArrowLeft, Sparkles, RefreshCw, AlertTriangle, CheckCircle2, Layers } from "lucide-react";
import { toast } from "sonner";

function scoreBadgeStyle(label) {
  switch (label) {
    case "Strong Match":  return "bg-emerald-600 text-white";
    case "Good Match":    return "bg-emerald-500 text-white";
    case "Partial Match": return "bg-amber-500 text-white";
    default:              return "bg-neutral-400 text-white";
  }
}

function attachPreview(file) {
  if (file.type?.startsWith("image/")) {
    return Object.assign(file, { preview: URL.createObjectURL(file) });
  }
  return file;
}

export default function Match() {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const zone = searchParams.get("zone") || "";

  const [project, setProject] = useState(null);
  const [refImg, setRefImg] = useState(null);
  const [loading, setLoading] = useState(true);

  const [prompt, setPrompt] = useState("");
  const [pdfFiles, setPdfFiles] = useState([]);
  const [imgFiles, setImgFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [progressStep, setProgressStep] = useState("");
  const [result, setResult] = useState(null);

  const [imgError, setImgError] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([
        api.get(`/projects/${id}`),
        api.get(`/projects/${id}/reference-image`).catch(() => null),
      ]);
      setProject(p.data);
      if (r) setRefImg(r.data.data_url);
      const saved = p.data?.match_results?.[zone];
      if (saved) {
        setResult(saved);
        setPrompt(saved.manual_prompt || "");
      }
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [id, zone]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const selected =
    result?.selected_material ||
    (project?.mock_analysis?.rows || []).find((r) => r.zone === zone) ||
    null;

  const handlePdfs = (files) => {
    const allowed = files.filter((f) => f.type === "application/pdf");
    if (allowed.length !== files.length) {
      toast.message("Only PDF files accepted in this slot; others ignored.");
    }
    setPdfFiles((prev) => [...prev, ...allowed]);
  };

  const handleImgs = (files) => {
    const allowed = files.filter((f) => ["image/jpeg", "image/png", "image/webp"].includes(f.type));
    if (allowed.length !== files.length) {
      toast.message("Only JPEG / PNG / WEBP accepted; others ignored.");
    }
    setImgFiles((prev) => [...prev, ...allowed.map(attachPreview)]);
  };

  const runMatch = async () => {
    if (!zone) {
      toast.error("No material zone selected");
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      setProgressStep("Preparing catalogue…");
      const fd = new FormData();
      fd.append("zone", zone);
      fd.append("manual_prompt", prompt || "");
      [...pdfFiles, ...imgFiles].forEach((f) => fd.append("catalogue", f));

      setProgressStep("Running match engine…");
      const { data } = await api.post(`/projects/${id}/match`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setResult(data);
      toast.success("Top 5 matches generated");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setBusy(false);
      setProgressStep("");
    }
  };

  const hasResults = result?.matches?.length > 0;

  if (loading) {
    return (
      <div className="min-h-screen bg-[#F9F9F8]" data-testid="match-page">
        <Header />
        <main className="max-w-7xl mx-auto px-6 py-12">
          <div className="grid lg:grid-cols-12 gap-8">
            <div className="lg:col-span-5 aspect-[4/5] rounded-2xl shimmer"></div>
            <div className="lg:col-span-7 h-96 rounded-2xl shimmer"></div>
          </div>
        </main>
      </div>
    );
  }

  if (!zone || !selected) {
    return (
      <div className="min-h-screen bg-[#F9F9F8]" data-testid="match-page">
        <Header />
        <main className="max-w-3xl mx-auto px-6 py-12">
          <Link to={`/projects/${id}/analysis`} className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black mb-8" data-testid="back-to-analysis">
            <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
            Back to analysis
          </Link>
          <div className="bg-white border border-dashed border-black/10 rounded-2xl p-12 text-center" data-testid="match-no-zone">
            <AlertTriangle className="w-8 h-8 text-amber-500 mx-auto mb-3" strokeWidth={1.25} />
            <h2 className="font-display text-2xl font-semibold mb-2">No material selected</h2>
            <p className="text-sm text-neutral-500">
              Open a material's "Find Matches" button from the analysis table to start.
            </p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="match-page">
      <Header />
      <main className="max-w-7xl mx-auto px-6 py-12">
        <Link to={`/projects/${id}/analysis`} className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black mb-6" data-testid="back-to-analysis">
          <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
          Back to analysis
        </Link>

        <div className="mb-10">
          <div className="text-overline mb-2">Match Engine · {project?.name || "—"}</div>
          <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
            Find products for <span className="text-neutral-400">{selected.zone}</span>.
          </h1>
          {project?.client_name && (
            <p className="text-neutral-500 mt-2">Client: {project.client_name}</p>
          )}
        </div>

        <div className="grid lg:grid-cols-12 gap-8">
          {/* LEFT — context */}
          <aside className="lg:col-span-5">
            <div className="lg:sticky lg:top-24 space-y-6">
              <div className="bg-white border border-black/5 rounded-2xl overflow-hidden shadow-soft" data-testid="match-reference-card">
                {refImg && !imgError ? (
                  <img
                    src={refImg}
                    alt="Reference"
                    className="w-full aspect-[4/3] object-cover"
                    onError={() => setImgError(true)}
                  />
                ) : (
                  <div className="w-full aspect-[4/3] bg-[#F3F2EE] grid place-items-center text-overline">
                    {imgError ? "Image unavailable" : "No reference"}
                  </div>
                )}
                <div className="p-5">
                  <div className="text-overline mb-2">Reference</div>
                  <h3 className="font-display font-semibold">{project?.name}</h3>
                  {project?.client_name && (
                    <p className="text-xs text-neutral-500 mt-0.5">{project.client_name}</p>
                  )}
                </div>
              </div>

              <div className="bg-white border border-black/5 rounded-2xl p-5 shadow-soft" data-testid="match-selected-material">
                <div className="flex items-center gap-2 mb-4">
                  <Layers className="w-4 h-4 text-neutral-700" strokeWidth={1.5} />
                  <div className="text-overline">Selected material</div>
                </div>
                <h3 className="font-display text-xl font-semibold mb-3">{selected.zone}</h3>
                <dl className="space-y-2 text-sm">
                  {[
                    ["Material", selected.material_type],
                    ["Color", selected.color],
                    ["Texture", selected.texture],
                    ["Finish", selected.finish],
                    ["Style", selected.design_style],
                  ].map(([label, value]) => (
                    <div key={label} className="flex items-start justify-between gap-4 pb-2 border-b border-black/5 last:border-0">
                      <dt className="text-neutral-500 text-xs">{label}</dt>
                      <dd className="text-neutral-900 text-right">{value || "—"}</dd>
                    </div>
                  ))}
                </dl>
                {selected.keywords?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-4">
                    {selected.keywords.slice(0, 6).map((k, i) => (
                      <span key={`k-${k}-${i}`} className="text-[10px] px-2 py-0.5 rounded-full bg-[#F3F2EE] text-neutral-600">{k}</span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </aside>

          {/* RIGHT — controls + results */}
          <section className="lg:col-span-7 space-y-6">
            <DemoModeBanner />

            <div className="bg-white border border-black/5 rounded-2xl p-6 shadow-soft space-y-6" data-testid="match-controls-panel">
              <div>
                <label className="text-overline">Optional prompt</label>
                <textarea
                  rows={3}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="e.g. Prefer FSC-certified options under £80/m². Avoid glossy finishes."
                  className="mt-2 w-full bg-white border border-black/10 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm resize-none"
                  data-testid="match-prompt-input"
                />
              </div>

              <div className="grid sm:grid-cols-2 gap-6">
                <UploadZone
                  label="Catalogue PDF"
                  description="Optional · PDF only"
                  accept="application/pdf"
                  multiple
                  files={pdfFiles}
                  onFiles={handlePdfs}
                  onRemove={(i) => setPdfFiles((prev) => prev.filter((_, idx) => idx !== i))}
                  testid="match-upload-pdf"
                />
                <UploadZone
                  label="Product images"
                  description="Optional · JPEG / PNG / WEBP"
                  accept="image/jpeg,image/png,image/webp"
                  multiple
                  files={imgFiles}
                  onFiles={handleImgs}
                  onRemove={(i) => setImgFiles((prev) => prev.filter((_, idx) => idx !== i))}
                  testid="match-upload-images"
                />
              </div>

              <div className="flex items-center gap-3 pt-2 border-t border-black/5">
                <button
                  onClick={runMatch}
                  disabled={busy}
                  className="inline-flex items-center justify-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-7 py-3.5 font-medium transition-colors disabled:opacity-60"
                  data-testid="run-match-btn"
                >
                  {hasResults ? (
                    <><RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} strokeWidth={1.5} /> {busy ? "Re-matching…" : "Re-run match"}</>
                  ) : (
                    <><Sparkles className={`w-4 h-4 ${busy ? "animate-pulse" : ""}`} strokeWidth={1.5} /> {busy ? "Matching…" : "Run Match"}</>
                  )}
                </button>
                {busy && progressStep && (
                  <div className="text-sm text-neutral-500" data-testid="match-progress">{progressStep}</div>
                )}
                <span className="text-xs text-neutral-400 ml-auto">Mock matching</span>
              </div>
            </div>

            {/* Loading skeleton */}
            {busy && !hasResults && (
              <div className="space-y-4" data-testid="match-loading">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="bg-white border border-black/5 rounded-2xl h-32 shimmer"></div>
                ))}
              </div>
            )}

            {/* Empty state */}
            {!busy && !hasResults && (
              <div className="bg-white border border-dashed border-black/10 rounded-2xl p-12 text-center" data-testid="match-empty">
                <Sparkles className="w-10 h-10 text-neutral-300 mx-auto mb-4" strokeWidth={1.25} />
                <h2 className="font-display text-2xl font-semibold mb-2">No matches yet</h2>
                <p className="text-sm text-neutral-500 max-w-md mx-auto">
                  Add an optional prompt or catalogue, then hit <span className="font-medium text-black">Run Match</span> to generate the top 5 candidate products.
                </p>
              </div>
            )}

            {/* Results */}
            {hasResults && (
              <div className="space-y-4" data-testid="match-results">
                <div className="flex items-baseline justify-between">
                  <h2 className="font-display text-2xl font-semibold">Top {result.matches.length} matches</h2>
                  {result.generated_at && (
                    <span className="text-xs text-neutral-500">
                      Generated {new Date(result.generated_at).toLocaleString()}
                    </span>
                  )}
                </div>

                {result.matches.map((m, i) => (
                  <article
                    key={m.id}
                    className="bg-white border border-black/5 rounded-2xl shadow-soft hover:shadow-hover transition-all duration-300 overflow-hidden"
                    data-testid={`match-card-${i}`}
                  >
                    <div className="grid grid-cols-12 gap-0">
                      {/* Thumbnail placeholder */}
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

                      <div className="col-span-9 sm:col-span-10 p-5 space-y-3">
                        <div className="flex items-start justify-between gap-4 flex-wrap">
                          <div className="min-w-0">
                            <h3 className="font-display text-lg font-semibold truncate">
                              {m.product_name}
                            </h3>
                            <p className="text-xs text-neutral-500 mt-0.5 truncate">{m.catalogue_ref}</p>
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
                              <CheckCircle2 className="w-4 h-4 mt-0.5 text-emerald-600 shrink-0" strokeWidth={1.75} />
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
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
