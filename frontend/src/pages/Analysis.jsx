import React, { useCallback, useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import DemoModeBanner from "@/components/DemoModeBanner";
import api, { formatApiError, useConfig } from "@/lib/api";
import { ArrowLeft, Sparkles, RefreshCw, ArrowUpRight, ChevronDown, ChevronRight } from "lucide-react";
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

function difficultyStyle(d) {
  if (d === "Easy") return "bg-emerald-50 text-emerald-800 border-emerald-100";
  if (d === "Medium") return "bg-amber-50 text-amber-800 border-amber-100";
  if (d === "Difficult") return "bg-rose-50 text-rose-800 border-rose-100";
  return "bg-neutral-50 text-neutral-600 border-neutral-100";
}

function AnalysisSummaryCard({ summary }) {
  if (!summary) return null;
  const hasAnything = summary.design_style || summary.material_palette ||
    summary.key_finishes || summary.sourcing_note;
  if (!hasAnything) return null;
  const sections = [
    { key: "design_style", label: "Design Style", value: summary.design_style },
    { key: "material_palette", label: "Material Palette", value: summary.material_palette },
    { key: "key_finishes", label: "Primary Finishes", value: summary.key_finishes },
  ].filter((s) => s.value);
  return (
    <div className="bg-white border border-black/5 rounded-2xl shadow-soft overflow-hidden" data-testid="analysis-summary-card">
      <div className="px-6 sm:px-8 pt-6 pb-4 border-b border-black/5">
        <div className="text-overline text-neutral-500">Specification Overview</div>
      </div>
      <div className="grid sm:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x divide-black/5">
        {sections.map((s) => (
          <div key={s.key} className="p-6 sm:p-8" data-testid={`summary-section-${s.key}`}>
            <div className="text-[10px] uppercase tracking-widest text-neutral-500 mb-2">{s.label}</div>
            <p className="text-sm text-neutral-900 leading-relaxed">{s.value}</p>
          </div>
        ))}
      </div>
      {summary.sourcing_note && (
        <div className="px-6 sm:px-8 py-6 bg-[#FCFBF7] border-t border-amber-100" data-testid="summary-section-sourcing">
          <div className="text-[10px] uppercase tracking-widest text-amber-900/70 mb-2">
            Indian Sourcing Summary
          </div>
          <p className="text-sm text-amber-900 leading-relaxed" data-testid="analysis-summary-sourcing">
            {summary.sourcing_note}
          </p>
        </div>
      )}
    </div>
  );
}

function RowDetails({ row, i }) {
  const hasAny = row.brands_to_check?.length || row.vendor_type || row.sourcing_keywords?.length ||
    row.indian_alternative || row.procurement_difficulty;
  if (!hasAny) return null;
  return (
    <div className="rounded-xl bg-[#F9F7F2] border border-amber-100 p-4 space-y-3 mt-3" data-testid={`analysis-row-details-${i}`}>
      {row.indian_alternative && (
        <div>
          <div className="text-[10px] uppercase tracking-widest text-amber-900/70 mb-1">Indian alternative</div>
          <p className="text-sm text-amber-900" data-testid={`analysis-indian-alt-${i}`}>{row.indian_alternative}</p>
        </div>
      )}
      {row.brands_to_check?.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-widest text-amber-900/70 mb-1">Brands to check</div>
          <div className="flex flex-wrap gap-1.5" data-testid={`analysis-brands-${i}`}>
            {row.brands_to_check.map((b) => (
              <span key={b} className="inline-flex text-[11px] px-2.5 py-1 rounded-full bg-white border border-amber-200 text-amber-900 font-medium">{b}</span>
            ))}
          </div>
        </div>
      )}
      {row.vendor_type && (
        <div>
          <div className="text-[10px] uppercase tracking-widest text-amber-900/70 mb-1">Vendor category</div>
          <p className="text-sm text-amber-900" data-testid={`analysis-vendor-${i}`}>{row.vendor_type}</p>
        </div>
      )}
      {row.sourcing_keywords?.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-widest text-amber-900/70 mb-1">Sourcing keywords</div>
          <div className="flex flex-wrap gap-1.5" data-testid={`analysis-sourcing-kw-${i}`}>
            {row.sourcing_keywords.map((k) => (
              <span key={k} className="inline-flex text-[11px] px-2.5 py-1 rounded-md bg-white border border-amber-200 text-amber-800">{k}</span>
            ))}
          </div>
        </div>
      )}
      {row.procurement_difficulty && (
        <div>
          <div className="text-[10px] uppercase tracking-widest text-amber-900/70 mb-1">Procurement difficulty</div>
          <span className={`inline-flex text-xs px-2.5 py-1 rounded-full border font-medium ${difficultyStyle(row.procurement_difficulty)}`} data-testid={`analysis-difficulty-${i}`}>
            {row.procurement_difficulty}
          </span>
        </div>
      )}
    </div>
  );
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
  const [expandedRow, setExpandedRow] = useState(null);

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
      toast.success("Specification generated");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const [imgError, setImgError] = useState(false);
  const rows = project?.mock_analysis?.rows || [];
  const summary = project?.mock_analysis?.summary;
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
          <div className="text-overline mb-2">Specification · {project?.name || "—"}</div>
          <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
            {hasAnalysis ? "Specification generated." : "Ready to specify."}
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

            {/* Reference image hero */}
            <div className="bg-white border border-black/5 rounded-2xl shadow-soft overflow-hidden" data-testid="reference-card">
              <div className="grid sm:grid-cols-12 gap-0">
                <div className="sm:col-span-4 lg:col-span-3 bg-[#F3F2EE] aspect-square sm:aspect-auto relative">
                  {refImg && !imgError ? (
                    <img src={refImg} alt="Reference" className="absolute inset-0 w-full h-full object-cover" onError={() => setImgError(true)} />
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
                        Specified {new Date(project.mock_analysis.generated_at).toLocaleString()}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-3 flex-wrap">
                    <button onClick={analyse} disabled={busy}
                      className="inline-flex items-center justify-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-6 py-3 text-sm font-medium transition-colors disabled:opacity-60"
                      data-testid="analyse-materials-btn">
                      {hasAnalysis ? (
                        <><RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} strokeWidth={1.5} /> {busy ? "Regenerating…" : "Regenerate specification"}</>
                      ) : (
                        <><Sparkles className={`w-4 h-4 ${busy ? "animate-pulse" : ""}`} strokeWidth={1.5} /> {busy ? "Analysing…" : "Generate specification"}</>
                      )}
                    </button>
                    <span className="text-xs text-neutral-400" data-testid="analysis-mode-label">
                      {realAnalysisActive ? "Live specification" : "Sample specification"}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <AnalysisSummaryCard summary={summary} />

            <section>
              {!hasAnalysis ? (
                <div className="bg-white border border-dashed border-black/10 rounded-2xl p-12 text-center" data-testid="analysis-empty">
                  <Sparkles className="w-10 h-10 text-neutral-300 mx-auto mb-4" strokeWidth={1.25} />
                  <h2 className="font-display text-2xl font-semibold mb-2">No specification yet</h2>
                  <p className="text-sm text-neutral-500 max-w-sm mx-auto">
                    Hit <span className="font-medium text-black">Generate specification</span> to detect surfaces, finishes and India sourcing guidance for this reference.
                  </p>
                </div>
              ) : (
                <div className="bg-white border border-black/5 rounded-2xl shadow-soft overflow-hidden" data-testid="analysis-table">
                  <div className="p-6 pb-4 flex items-baseline justify-between">
                    <h2 className="font-display text-2xl font-semibold">Specification zones</h2>
                    <span className="text-xs text-neutral-500">{rows.length} entries · click a row to expand</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-[#F3F2EE]/60">
                        <tr className="text-left">
                          <th className="w-8 px-2 py-3"></th>
                          <th className="px-3 py-3 text-overline font-semibold">Zone / Surface</th>
                          <th className="px-3 py-3 text-overline font-semibold">Material</th>
                          <th className="px-3 py-3 text-overline font-semibold">Color</th>
                          <th className="px-3 py-3 text-overline font-semibold">Finish</th>
                          <th className="px-3 py-3 text-overline font-semibold">Style</th>
                          <th className="px-3 py-3 text-overline font-semibold text-right">Confidence</th>
                          <th className="px-6 py-3 text-overline font-semibold text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-black/5">
                        {rows.map((r, i) => {
                          const savedMatch = project?.match_results?.[r.zone];
                          const expanded = expandedRow === i;
                          const toggle = () => setExpandedRow(expanded ? null : i);
                          return (
                            <React.Fragment key={`row-${r.zone}-${i}`}>
                              <tr className="hover:bg-[#F3F2EE]/30 transition-colors cursor-pointer" data-testid={`analysis-row-${i}`} onClick={toggle}>
                                <td className="px-2 py-4 text-neutral-400">
                                  {expanded ? <ChevronDown className="w-4 h-4" strokeWidth={1.5} /> : <ChevronRight className="w-4 h-4" strokeWidth={1.5} />}
                                </td>
                                <td className="px-3 py-4 font-medium text-neutral-900">
                                  <div>{r.zone}</div>
                                  {r.material_family && (
                                    <span className="inline-flex text-[10px] mt-1 px-2 py-0.5 rounded-full bg-black text-white uppercase tracking-wider">{r.material_family}</span>
                                  )}
                                </td>
                                <td className="px-3 py-4 text-neutral-700">
                                  <div>{r.material_type}</div>
                                  {r.texture && <div className="text-xs text-neutral-400 mt-0.5">{r.texture}</div>}
                                </td>
                                <td className="px-3 py-4 text-neutral-700">{r.color}</td>
                                <td className="px-3 py-4 text-neutral-700">{r.finish}</td>
                                <td className="px-3 py-4 text-neutral-700">{r.design_style}</td>
                                <td className="px-3 py-4 text-right whitespace-nowrap">
                                  <span className={`inline-flex items-center gap-1.5 ${confidenceColor(r.confidence || 0)} text-white text-xs font-mono font-semibold px-2.5 py-1 rounded-full`}>
                                    {r.confidence || 0}%
                                  </span>
                                </td>
                                <td className="px-6 py-4 text-right whitespace-nowrap">
                                  <button
                                    onClick={(e) => { e.stopPropagation(); navigate(`/projects/${id}/match?zone=${encodeURIComponent(r.zone)}`); }}
                                    className="inline-flex items-center gap-1.5 bg-black text-white hover:bg-black/80 rounded-full px-3 py-1.5 text-xs font-medium transition-colors"
                                    data-testid={`find-matches-btn-${i}`}
                                  >
                                    {savedMatch ? "View matches" : "Match With Catalogue"}
                                    <ArrowUpRight className="w-3 h-3" strokeWidth={1.75} />
                                  </button>
                                </td>
                              </tr>
                              {expanded && (
                                <tr className="bg-[#FCFBF7]">
                                  <td></td>
                                  <td colSpan={7} className="px-3 pb-5">
                                    <RowDetails row={r} i={i} />
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
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
