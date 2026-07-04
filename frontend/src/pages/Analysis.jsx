import React, { useCallback, useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import DemoModeBanner from "@/components/DemoModeBanner";
import ProductsSection from "@/components/analysis/ProductsSection";
import ShortlistSection from "@/components/analysis/ShortlistSection";
import MaterialsFirstSection from "@/components/analysis/MaterialsFirstSection";
import RegionSelector from "@/components/analysis/RegionSelector";
import api, { formatApiError, useConfig } from "@/lib/api";
import { ArrowLeft, Sparkles, RefreshCw, X, Focus, Layers } from "lucide-react";
import { toast } from "sonner";

function SummaryPanel({ summary, title, subtitle, ephemeral, onClear, cropPreview }) {
  const sections = [
    { key: "design_style", label: "Design Style", value: summary?.design_style || summary?.overall_style },
    { key: "material_palette", label: "Material Palette", value: summary?.material_palette || (summary?.palette && summary.palette.join(", ")) },
    { key: "key_finishes", label: "Primary Finishes", value: summary?.key_finishes },
  ].filter((s) => s.value);
  return (
    <aside
      className="bg-white border border-stone-border-soft rounded-2xl shadow-soft overflow-hidden sticky top-24"
      data-testid={ephemeral ? "intelligence-panel-zone" : "intelligence-panel-full"}
    >
      <div className="px-5 py-4 border-b border-stone-border-soft flex items-center justify-between bg-stone-panel/60">
        <div className="flex items-center gap-2">
          {ephemeral ? (
            <Focus className="w-3.5 h-3.5 text-charcoal" strokeWidth={1.75} />
          ) : (
            <Layers className="w-3.5 h-3.5 text-charcoal" strokeWidth={1.75} />
          )}
          <span className="text-overline">{ephemeral ? "Zone Intelligence" : "Reference Intelligence"}</span>
        </div>
        {ephemeral && onClear && (
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-1 text-[10px] text-warm-grey hover:text-charcoal"
            data-testid="intelligence-clear-zone"
          >
            <X className="w-3 h-3" strokeWidth={1.75} /> Full image
          </button>
        )}
      </div>
      <div className="p-5 space-y-4">
        {ephemeral && cropPreview && (
          <img
            src={cropPreview}
            alt="Selected region"
            className="w-full h-auto rounded-lg border border-stone-border-soft"
            data-testid="intelligence-crop-preview"
          />
        )}
        <div>
          <div className="font-display text-lg font-semibold text-charcoal leading-tight">
            {title || (ephemeral ? "Selected zone" : "Full image analysis")}
          </div>
          {subtitle && <p className="text-xs text-warm-grey mt-1 leading-relaxed">{subtitle}</p>}
        </div>
        {sections.length > 0 && (
          <div className="space-y-3 pt-1">
            {sections.map((s) => (
              <div key={s.key} data-testid={`intelligence-${s.key}`}>
                <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 mb-0.5">{s.label}</div>
                <p className="text-sm text-charcoal leading-relaxed">{s.value}</p>
              </div>
            ))}
          </div>
        )}
        {summary?.sourcing_note && (
          <div className="rounded-xl bg-ochre-soft/60 border border-ochre/30 p-3">
            <div className="text-[10px] uppercase tracking-widest text-ochre font-semibold mb-1">
              Indian Sourcing Summary
            </div>
            <p className="text-xs text-charcoal leading-relaxed" data-testid="intelligence-sourcing">
              {summary.sourcing_note}
            </p>
          </div>
        )}
        {!sections.length && !summary?.sourcing_note && (
          <p className="text-xs text-warm-grey italic">
            {ephemeral ? "Region analysis returned no summary." : "Generate specification to see reference intelligence."}
          </p>
        )}
      </div>
    </aside>
  );
}

export default function Analysis() {
  const { id } = useParams();
  const navigate = useNavigate();
  const config = useConfig();
  const realAnalysisActive = !!config?.enable_real_analysis;
  const [project, setProject] = useState(null);
  const [refImg, setRefImg] = useState(null);
  const [products, setProducts] = useState([]);
  const [shortlist, setShortlist] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [imgError, setImgError] = useState(false);
  const [regionResult, setRegionResult] = useState(null); // { rows, summary, crop_data_url }
  const [focusedIndex, setFocusedIndex] = useState(null); // syncs image pins ↔ material cards

  const fetchProject = useCallback(async () => {
    try {
      const [p, r, prod, sl] = await Promise.all([
        api.get(`/projects/${id}`),
        api.get(`/projects/${id}/reference-image`).catch(() => null),
        api.get(`/projects/${id}/products`).catch(() => null),
        api.get(`/projects/${id}/shortlist`).catch(() => null),
      ]);
      setProject(p.data);
      if (r) setRefImg(r.data.data_url);
      if (prod) setProducts(prod.data.products || []);
      if (sl) setShortlist(sl.data.items || []);
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
      if (Array.isArray(data.products)) setProducts(data.products);
      setRegionResult(null); // clear zone view on fresh full analysis
      toast.success("Specification generated");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const addToShortlist = async (payload) => {
    try {
      const { data } = await api.post(`/projects/${id}/shortlist`, payload);
      setShortlist((cur) => [...cur, data]);
      toast.success("Added to shortlist");
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const removeFromShortlist = async (item) => {
    try {
      await api.delete(`/projects/${id}/shortlist/${item.id}`);
      setShortlist((cur) => cur.filter((x) => x.id !== item.id));
      toast.success("Removed from shortlist");
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const addMaterialRowToShortlist = (row) => addToShortlist({
    name: row.material_type || row.material_name || row.zone,
    source_type: "spec",
    source: row.brands_to_check?.[0] || row.vendor_type || "Detected material",
    category: row.material_family || "material",
    zone: row.zone,
    notes: row._fromAlt
      ? `Alternative for ${row.zone}: ${row.alt_meta?.why || ""}`
      : (row.indian_alternative || row.finish || ""),
  });

  const addProductToShortlist = (p) => addToShortlist({
    name: p.product_name,
    source_type: "product",
    source: p.matched_affiliate?.platform || "Detected",
    category: p.category,
    external_url: p.matched_affiliate?.affiliate_url || (p.search_urls?.amazon_in || ""),
    notes: p.description || "",
  });

  // Sprint 2 Revision — a "closest catalogue match" is a first-class shortlist
  // item that carries the brand, catalogue and code straight to the vendor.
  const addCatalogueMatchToShortlist = (row, match) => addToShortlist({
    name: `${match.brand} · ${match.material_name}`,
    source_type: "spec",
    source: match.brand,
    category: match.material_family || match.category || "material",
    zone: row?.zone,
    notes: [
      match.material_code ? `Code: ${match.material_code}` : "Code unavailable in current database",
      match.catalogue ? `Catalogue: ${match.catalogue}` : null,
      match.page_number ? `Page: ${match.page_number}` : null,
      match.match_percent ? `Match: ${match.match_percent}%` : null,
    ].filter(Boolean).join(" · "),
  });

  const goToMatchZone = (row) => navigate(`/projects/${id}/match?zone=${encodeURIComponent(row.zone)}`);

  const projectRows = project?.mock_analysis?.rows || [];
  const projectSummary = project?.mock_analysis?.summary;
  const hasAnalysis = projectRows.length > 0;

  // Effective rows / summary — region-scoped when a zone selection is active.
  const activeRows = regionResult?.rows?.length ? regionResult.rows : projectRows;
  const activeSummary = regionResult?.summary || projectSummary;
  const activeEphemeral = !!regionResult;

  const shortlistedMaterialNames = new Set(
    shortlist.filter((x) => x.source_type === "spec").map((x) => x.name)
  );
  const shortlistedProductNames = new Set(
    shortlist.filter((x) => x.source_type === "product").map((x) => x.name)
  );
  // Catalogue-match shortlist items are named "Brand · Material Name" —
  // the CatalogueMatchRow looks these up by id so we build a stable id set.
  const shortlistedCatalogueIds = new Set(
    shortlist
      .filter((x) => x.source_type === "spec")
      .map((x) => x.name)
  );

  // Numbered pins overlaid on the reference image. Rows carrying a `pin`
  // dict use their coordinates; the rest get evenly distributed placeholders
  // so every card still has a visible image ↔ card link.
  const imagePins = activeRows.map((r, i) => {
    if (r?.pin && typeof r.pin.x === "number" && typeof r.pin.y === "number") {
      return { x: r.pin.x, y: r.pin.y, label: r.zone };
    }
    const cols = 3;
    const col = i % cols;
    const rowIdx = Math.floor(i / cols);
    return { x: 20 + col * 30, y: 20 + rowIdx * 22, label: r.zone };
  });

  return (
    <div className="min-h-screen bg-paper" data-testid="analysis-page">
      <Header />
      <main className="max-w-7xl mx-auto px-6 py-12">
        <div className="flex items-center justify-between mb-8 flex-wrap gap-4">
          <Link to="/dashboard" className="inline-flex items-center gap-2 text-sm text-warm-grey hover:text-charcoal" data-testid="back-to-dashboard">
            <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
            Back to dashboard
          </Link>
        </div>

        <div className="mb-10">
          <div className="text-overline mb-2">Specification · {project?.name || "—"}</div>
          <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight text-charcoal">
            {hasAnalysis ? "Specification generated." : "Ready to specify."}
          </h1>
          {project?.client_name && (
            <p className="text-warm-grey mt-2">Client: {project.client_name}</p>
          )}
        </div>

        {loading ? (
          <div className="space-y-8">
            <div className="h-40 rounded-2xl shimmer"></div>
            <div className="h-96 rounded-2xl shimmer"></div>
          </div>
        ) : (
          <div className="space-y-10">
            <DemoModeBanner />

            {/* Reference + Intelligence Panel */}
            <section className="grid lg:grid-cols-12 gap-6" data-testid="reference-intelligence-grid">
              <div className="lg:col-span-8 space-y-4">
                <div className="bg-white border border-stone-border-soft rounded-2xl shadow-soft p-5 sm:p-6" data-testid="reference-card">
                  <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
                    <div>
                      <div className="text-overline mb-1">Reference</div>
                      <h3 className="font-display text-xl font-semibold text-charcoal">{project?.name}</h3>
                      {project?.mock_analysis?.generated_at && (
                        <p className="text-[11px] text-warm-grey mt-1" data-testid="analysis-generated-at">
                          Specified {new Date(project.mock_analysis.generated_at).toLocaleString()}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <button onClick={analyse} disabled={busy}
                        className="inline-flex items-center justify-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-5 py-2.5 text-sm font-medium transition-colors disabled:opacity-60"
                        data-testid="analyse-materials-btn">
                        {hasAnalysis ? (
                          <><RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} strokeWidth={1.5} /> {busy ? "Reading zones…" : "Regenerate"}</>
                        ) : (
                          <><Sparkles className={`w-4 h-4 ${busy ? "animate-pulse" : ""}`} strokeWidth={1.5} /> {busy ? "Reading zones…" : "Generate specification"}</>
                        )}
                      </button>
                      <Link
                        to={`/projects/${id}/concept`}
                        className="inline-flex items-center justify-center gap-2 border border-stone-border hover:border-charcoal text-charcoal rounded-full px-4 py-2.5 text-sm font-medium transition-colors"
                        data-testid="open-concept-btn"
                        title="Concept Presentation is in beta"
                      >
                        <Sparkles className="w-4 h-4" strokeWidth={1.5} />
                        Concept
                        <span className="text-[9px] uppercase tracking-widest bg-ochre-soft text-ochre px-1.5 py-0.5 rounded-full font-semibold">
                          Beta
                        </span>
                      </Link>
                    </div>
                  </div>

                  {refImg && !imgError ? (
                    <RegionSelector
                      projectId={id}
                      imgSrc={refImg}
                      onAnalyzed={(result) => setRegionResult(result)}
                      pins={activeEphemeral ? [] : imagePins}
                      focusedPinIndex={focusedIndex}
                      onHoverPin={setFocusedIndex}
                    />
                  ) : (
                    <div className="aspect-video rounded-2xl bg-stone-panel border border-stone-border-soft grid place-items-center text-overline">
                      {imgError ? "Image unavailable" : "No reference"}
                    </div>
                  )}
                  {refImg && (
                    <img
                      src={refImg}
                      alt=""
                      className="hidden"
                      onError={() => setImgError(true)}
                    />
                  )}
                  <div className="mt-3 flex items-center justify-between flex-wrap gap-2">
                    <span className="text-[10px] uppercase tracking-widest text-warm-grey">
                      {realAnalysisActive ? "Live specification" : "Sample specification"}
                    </span>
                    {activeEphemeral && (
                      <button
                        type="button"
                        onClick={() => setRegionResult(null)}
                        className="inline-flex items-center gap-1 text-[11px] px-3 py-1.5 rounded-full border border-stone-border text-charcoal hover:border-charcoal transition-colors"
                        data-testid="clear-region-btn"
                      >
                        <X className="w-3 h-3" strokeWidth={2} /> Back to full image
                      </button>
                    )}
                  </div>
                </div>
              </div>

              <div className="lg:col-span-4">
                <SummaryPanel
                  summary={activeSummary}
                  title={activeEphemeral
                    ? `${regionResult.rows.length} material${regionResult.rows.length === 1 ? "" : "s"} in selected area`
                    : (hasAnalysis ? "Full image analysis" : "Ready to specify")}
                  subtitle={activeEphemeral
                    ? "Ephemeral zone analysis — not saved to the project."
                    : (hasAnalysis
                        ? "Specification-wide summary across all detected zones."
                        : "Hit Generate specification to detect surfaces and finishes.")}
                  ephemeral={activeEphemeral}
                  onClear={() => setRegionResult(null)}
                  cropPreview={regionResult?.crop_data_url}
                />
              </div>
            </section>

            {/* Materials → Alternatives → Catalogue */}
            {activeRows.length > 0 ? (
              <MaterialsFirstSection
                rows={activeRows}
                onAddToShortlist={addMaterialRowToShortlist}
                shortlistedNames={shortlistedMaterialNames}
                onMatchCatalogue={activeEphemeral ? null : goToMatchZone}
                matchResults={project?.match_results || {}}
                ephemeral={activeEphemeral}
                title={activeEphemeral
                  ? `Zone focus · ${regionResult.rows.length} material${regionResult.rows.length === 1 ? "" : "s"}`
                  : undefined}
                subtitle={activeEphemeral
                  ? "AI-detected materials just for the area you selected. Add any to your shortlist."
                  : undefined}
                onAddCatalogueToShortlist={addCatalogueMatchToShortlist}
                shortlistedCatalogueIds={shortlistedCatalogueIds}
                focusedIndex={focusedIndex}
                onHoverCard={setFocusedIndex}
              />
            ) : (
              <div className="bg-white border border-dashed border-stone-border rounded-2xl p-12 text-center" data-testid="analysis-empty">
                <Sparkles className="w-10 h-10 text-warm-grey mx-auto mb-4" strokeWidth={1.25} />
                <h2 className="font-display text-2xl font-semibold mb-2 text-charcoal">No specification yet</h2>
                <p className="text-sm text-warm-grey max-w-sm mx-auto">
                  Hit <span className="font-medium text-charcoal">Generate specification</span> to detect surfaces, finishes and India sourcing guidance for this reference.
                </p>
              </div>
            )}

            {/* Products → Product Alternatives */}
            <ProductsSection
              products={products}
              onAddToShortlist={addProductToShortlist}
              shortlistedNames={shortlistedProductNames}
            />

            {/* Sourceable Shortlist */}
            <ShortlistSection
              items={shortlist}
              onRemove={removeFromShortlist}
            />
          </div>
        )}
      </main>
    </div>
  );
}
