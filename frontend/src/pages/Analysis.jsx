import React, { useCallback, useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import DemoModeBanner from "@/components/DemoModeBanner";
import api, { formatApiError, useConfig } from "@/lib/api";
import { ArrowLeft, Sparkles, RefreshCw, ArrowUpRight } from "lucide-react";
import { toast } from "sonner";

// Confidence-threshold bands for color coding the confidence pill.
const CONFIDENCE_STRONG = 90;
const CONFIDENCE_GOOD = 80;
const CONFIDENCE_PARTIAL = 70;

function confidenceColor(c) {
  if (c >= CONFIDENCE_STRONG) return "bg-emerald-600";
  if (c >= CONFIDENCE_GOOD) return "bg-emerald-500";
  if (c >= CONFIDENCE_PARTIAL) return "bg-amber-500";
  return "bg-neutral-400";
}

export default function Analysis() {
  const { id } = useParams();
  const navigate = useNavigate();
  const config = useConfig();
  const realAnalysisActive = !!config?.enable_real_analysis;
  const [project, setProject] = useState(null);
  const [refImg, setRefImg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchProject = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([
        api.get(`/projects/${id}`),
        api.get(`/projects/${id}/reference-image`).catch(() => null),
      ]);
      setProject(p.data);
      if (r) setRefImg(r.data.data_url);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { fetchProject(); }, [fetchProject]);

  const analyse = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/projects/${id}/analyze`);
      setProject((prev) => ({ ...(prev || {}), mock_analysis: data, status: "completed" }));
      toast.success("Materials analysed");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const [imgError, setImgError] = useState(false);
  const rows = project?.mock_analysis?.rows || [];
  const hasAnalysis = rows.length > 0;

  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="analysis-page">
      <Header />
      <main className="max-w-7xl mx-auto px-6 py-12">
        <div className="flex items-center justify-between mb-8 flex-wrap gap-4">
          <Link to="/dashboard" className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black" data-testid="back-to-dashboard">
            <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
            Back to dashboard
          </Link>
        </div>

        <div className="mb-10">
          <div className="text-overline mb-2">Material Analysis · {project?.name || "—"}</div>
          <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
            {hasAnalysis ? "Materials detected." : "Ready to analyse."}
          </h1>
          {project?.client_name && (
            <p className="text-neutral-500 mt-2">Client: {project.client_name}</p>
          )}
        </div>

        {loading ? (
          <div className="space-y-8">
            <div className="h-40 rounded-2xl shimmer"></div>
            <div className="h-96 rounded-2xl shimmer"></div>
          </div>
        ) : (
          <div className="space-y-8">
            <DemoModeBanner />

            {/* Reference image — horizontal hero card */}
            <div className="bg-white border border-black/5 rounded-2xl shadow-soft overflow-hidden" data-testid="reference-card">
              <div className="grid sm:grid-cols-12 gap-0">
                <div className="sm:col-span-4 lg:col-span-3 bg-[#F3F2EE] aspect-square sm:aspect-auto relative">
                  {refImg && !imgError ? (
                    <img
                      src={refImg}
                      alt="Reference"
                      className="absolute inset-0 w-full h-full object-cover"
                      onError={() => setImgError(true)}
                    />
                  ) : (
                    <div className="absolute inset-0 grid place-items-center text-overline">
                      {imgError ? "Image unavailable" : "No reference"}
                    </div>
                  )}
                </div>
                <div className="sm:col-span-8 lg:col-span-9 p-6 sm:p-8 flex flex-col justify-between gap-6">
                  <div>
                    <div className="text-overline mb-2">Reference</div>
                    <h3 className="font-display text-2xl font-semibold mb-1">{project?.name}</h3>
                    {project?.client_name && (
                      <p className="text-sm text-neutral-500">{project.client_name}</p>
                    )}
                    {project?.mock_analysis?.generated_at && (
                      <p className="text-xs text-neutral-400 mt-2" data-testid="analysis-generated-at">
                        Analysed {new Date(project.mock_analysis.generated_at).toLocaleString()}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-3 flex-wrap">
                    <button
                      onClick={analyse}
                      disabled={busy}
                      className="inline-flex items-center justify-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-6 py-3 text-sm font-medium transition-colors disabled:opacity-60"
                      data-testid="analyse-materials-btn"
                    >
                      {hasAnalysis ? (
                        <><RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} strokeWidth={1.5} /> {busy ? "Re-analysing…" : "Re-analyse materials"}</>
                      ) : (
                        <><Sparkles className={`w-4 h-4 ${busy ? "animate-pulse" : ""}`} strokeWidth={1.5} /> {busy ? "Analysing…" : "Analyse Materials"}</>
                      )}
                    </button>
                    <span className="text-xs text-neutral-400" data-testid="analysis-mode-label">
                      {realAnalysisActive ? "AI analysis" : "Mock analysis"}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Results table — full width */}
            <section>
              {!hasAnalysis ? (
                <div className="bg-white border border-dashed border-black/10 rounded-2xl p-12 text-center" data-testid="analysis-empty">
                  <Sparkles className="w-10 h-10 text-neutral-300 mx-auto mb-4" strokeWidth={1.25} />
                  <h2 className="font-display text-2xl font-semibold mb-2">No analysis yet</h2>
                  <p className="text-sm text-neutral-500 max-w-sm mx-auto">
                    Hit <span className="font-medium text-black">Analyse Materials</span> to detect materials, finishes, and design style for this reference.
                  </p>
                </div>
              ) : (
                <div className="bg-white border border-black/5 rounded-2xl shadow-soft overflow-hidden" data-testid="analysis-table">
                  <div className="p-6 pb-4 flex items-baseline justify-between">
                    <h2 className="font-display text-2xl font-semibold">Detected materials</h2>
                    <span className="text-xs text-neutral-500">{rows.length} entries</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-[#F3F2EE]/60">
                        <tr className="text-left">
                          <th className="px-6 py-3 text-overline font-semibold">Zone / Area</th>
                          <th className="px-3 py-3 text-overline font-semibold">Material</th>
                          <th className="px-3 py-3 text-overline font-semibold">Color</th>
                          <th className="px-3 py-3 text-overline font-semibold">Texture</th>
                          <th className="px-3 py-3 text-overline font-semibold">Finish</th>
                          <th className="px-3 py-3 text-overline font-semibold">Style</th>
                          <th className="px-3 py-3 text-overline font-semibold">Keywords</th>
                          <th className="px-3 py-3 text-overline font-semibold text-right">Confidence</th>
                          <th className="px-6 py-3 text-overline font-semibold text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-black/5">
                        {rows.map((r, i) => {
                          const savedMatch = project?.match_results?.[r.zone];
                          return (
                          <tr key={`row-${r.zone}-${i}`} className="hover:bg-[#F3F2EE]/30 transition-colors" data-testid={`analysis-row-${i}`}>
                            <td className="px-6 py-4 font-medium text-neutral-900 whitespace-nowrap">{r.zone}</td>
                            <td className="px-3 py-4 text-neutral-700">
                              <div className="space-y-1">
                                <div>{r.material_type}</div>
                                {r.material_family && (
                                  <span className="inline-flex text-[10px] px-2 py-0.5 rounded-full bg-black text-white uppercase tracking-wider">
                                    {r.material_family}
                                  </span>
                                )}
                                {r.indian_alternative && (
                                  <div
                                    className="mt-1 text-[11px] italic text-amber-800 bg-amber-50 border border-amber-100 rounded-md px-2 py-1 leading-snug"
                                    data-testid={`analysis-indian-alt-${i}`}
                                    title="AI-suggested Indian-market equivalent"
                                  >
                                    IN: {r.indian_alternative}
                                  </div>
                                )}
                              </div>
                            </td>
                            <td className="px-3 py-4 text-neutral-700">{r.color}</td>
                            <td className="px-3 py-4 text-neutral-700">{r.texture}</td>
                            <td className="px-3 py-4 text-neutral-700">{r.finish}</td>
                            <td className="px-3 py-4 text-neutral-700">{r.design_style}</td>
                            <td className="px-3 py-4">
                              <div className="flex flex-wrap gap-1 max-w-[200px]">
                                {(r.keywords || []).slice(0, 4).map((k, j) => (
                                  <span key={`kw-${k}-${j}`} className="inline-flex px-2 py-0.5 text-[10px] rounded-full bg-[#F3F2EE] text-neutral-600">{k}</span>
                                ))}
                              </div>
                            </td>
                            <td className="px-3 py-4 text-right whitespace-nowrap">
                              <span className={`inline-flex items-center gap-1.5 ${confidenceColor(r.confidence || 0)} text-white text-xs font-mono font-semibold px-2.5 py-1 rounded-full`}>
                                {r.confidence || 0}%
                              </span>
                            </td>
                            <td className="px-6 py-4 text-right whitespace-nowrap">
                              <button
                                onClick={() => navigate(`/projects/${id}/match?zone=${encodeURIComponent(r.zone)}`)}
                                className="inline-flex items-center gap-1.5 bg-black text-white hover:bg-black/80 rounded-full px-3 py-1.5 text-xs font-medium transition-colors"
                                data-testid={`find-matches-btn-${i}`}
                              >
                                {savedMatch ? "View matches" : "Find Matches"}
                                <ArrowUpRight className="w-3 h-3" strokeWidth={1.75} />
                              </button>
                            </td>
                          </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
