import React, { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { ArrowLeft, Sparkles, RefreshCw } from "lucide-react";
import { toast } from "sonner";

function confidenceColor(c) {
  if (c >= 0.9) return "bg-emerald-600";
  if (c >= 0.8) return "bg-emerald-500";
  if (c >= 0.7) return "bg-amber-500";
  return "bg-neutral-400";
}

export default function Analysis() {
  const { id } = useParams();
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
      const { data } = await api.post(`/projects/${id}/mock-analyze`);
      setProject((prev) => ({ ...(prev || {}), mock_analysis: data, status: "completed" }));
      toast.success("Materials analysed");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

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
          <div className="grid lg:grid-cols-12 gap-8">
            <div className="lg:col-span-5 aspect-[4/5] rounded-2xl shimmer"></div>
            <div className="lg:col-span-7 h-96 rounded-2xl shimmer"></div>
          </div>
        ) : (
          <div className="grid lg:grid-cols-12 gap-8">
            {/* Reference image card */}
            <aside className="lg:col-span-5">
              <div className="lg:sticky lg:top-24 space-y-6">
                <div className="bg-white border border-black/5 rounded-2xl overflow-hidden shadow-soft" data-testid="reference-card">
                  {refImg ? (
                    <img src={refImg} alt="Reference" className="w-full aspect-[4/5] object-cover" />
                  ) : (
                    <div className="w-full aspect-[4/5] bg-[#F3F2EE] grid place-items-center text-overline">No reference</div>
                  )}
                  <div className="p-5">
                    <div className="text-overline mb-2">Reference</div>
                    <p className="text-sm text-neutral-700">
                      Your uploaded inspiration image.
                    </p>
                    {project?.mock_analysis?.generated_at && (
                      <p className="text-xs text-neutral-400 mt-2" data-testid="analysis-generated-at">
                        Analysed {new Date(project.mock_analysis.generated_at).toLocaleString()}
                      </p>
                    )}
                  </div>
                </div>

                <button
                  onClick={analyse}
                  disabled={busy}
                  className="w-full inline-flex items-center justify-center gap-2 bg-black text-white hover:bg-black/80 rounded-full py-3.5 font-medium transition-colors disabled:opacity-60"
                  data-testid="analyse-materials-btn"
                >
                  {hasAnalysis ? (
                    <><RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} strokeWidth={1.5} /> {busy ? "Re-analysing…" : "Re-analyse materials"}</>
                  ) : (
                    <><Sparkles className={`w-4 h-4 ${busy ? "animate-pulse" : ""}`} strokeWidth={1.5} /> {busy ? "Analysing…" : "Analyse Materials"}</>
                  )}
                </button>
                <p className="text-xs text-neutral-400 text-center">Demo mode · mock analysis</p>
              </div>
            </aside>

            {/* Results table */}
            <section className="lg:col-span-7">
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
                          <th className="px-6 py-3 text-overline font-semibold text-right">Conf.</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-black/5">
                        {rows.map((r, i) => (
                          <tr key={`row-${r.zone}-${i}`} className="hover:bg-[#F3F2EE]/30 transition-colors" data-testid={`analysis-row-${i}`}>
                            <td className="px-6 py-4 font-medium text-neutral-900 whitespace-nowrap">{r.zone}</td>
                            <td className="px-3 py-4 text-neutral-700">{r.material_type}</td>
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
                            <td className="px-6 py-4 text-right whitespace-nowrap">
                              <span className={`inline-flex items-center gap-1.5 ${confidenceColor(r.confidence)} text-white text-xs font-mono font-semibold px-2.5 py-1 rounded-full`}>
                                {Math.round((r.confidence || 0) * 100)}%
                              </span>
                            </td>
                          </tr>
                        ))}
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
