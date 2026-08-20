import React, { useCallback, useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import DemoModeBanner from "@/components/DemoModeBanner";
import ProductsSection from "@/components/analysis/ProductsSection";
import ShortlistSection from "@/components/analysis/ShortlistSection";
import MaterialsFirstSection from "@/components/analysis/MaterialsFirstSection";
import RegionSelector from "@/components/analysis/RegionSelector";
import api, { formatApiError, useConfig } from "@/lib/api";
import { ArrowLeft, Sparkles, RefreshCw, X, Focus, Layers, ImagePlus, BookOpen, User as UserIcon, UploadCloud } from "lucide-react";
import { toast } from "sonner";

function SummaryPanel({ summary, title, subtitle, ephemeral, onClear, cropPreview }) {
  const sections = [
    { key: "design_style", label: "Design Style", value: summary?.design_style || summary?.overall_style },
    { key: "material_palette", label: "Material Palette", value: summary?.material_palette || (summary?.palette && summary.palette.join(", ")) },
    { key: "key_finishes", label: "Primary Finishes", value: summary?.key_finishes },
  ].filter((s) => s.value);
  return (
    <aside
      className="bg-white border border-stone-border-soft rounded-2xl shadow-soft overflow-hidden"
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
              Regional Sourcing Summary
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
  const [focusedProductIndex, setFocusedProductIndex] = useState(null); // syncs product pins ↔ product cards
  const [replacing, setReplacing] = useState(false);
  const replaceInputRef = React.useRef(null);
  // 2026-02-01 (round 4) — user-uploadable catalogues. Track which
  // library the specification was generated against so the two-button
  // scope choice can be surfaced honestly in the UI (never silently
  // merged with the other scope).
  const [libraryScope, setLibraryScope] = useState("admin");

  // Sync libraryScope from the persisted analysis if the user returns
  // to a project mid-flow.
  useEffect(() => {
    const persisted = project?.mock_analysis?.library_scope;
    if (persisted === "admin" || persisted === "own") setLibraryScope(persisted);
  }, [project?.mock_analysis?.library_scope]);

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

  const analyse = async (scope = "admin") => {
    setBusy(true);
    try {
      const { data } = await api.post(
        `/projects/${id}/analyze`,
        null,
        { params: { library_scope: scope } },
      );
      // Stamp the scope onto the analysis payload so the UI can show
      // which library the user chose for this run.
      const stamped = { ...data, library_scope: scope };
      setProject((prev) => ({ ...(prev || {}), mock_analysis: stamped, status: "completed" }));
      setLibraryScope(scope);
      if (Array.isArray(data.products)) setProducts(data.products);
      setRegionResult(null);
      toast.success(
        scope === "own"
          ? "Specification generated against your uploaded catalogue"
          : "Specification generated against the Admin Library"
      );
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
    // Round 8 — carry the swatch preview (either the cropped catalogue
    // image or a hex swatch) so ShortlistSection can render a
    // click-to-enlarge preview instead of the founder-reported "too
    // small to trust before shortlisting" thumbnail.
    swatch_crop_b64: match.swatch_crop_b64 || null,
    material_view_b64: match.material_view_b64 || null,
    color_hex: match.color_hex || null,
    material_code: match.material_code || null,
    match_percent: match.match_percent || null,
    notes: [
      match.material_code ? `Code: ${match.material_code}` : "Code unavailable in current database",
      match.catalogue ? `Catalogue: ${match.catalogue}` : null,
      match.page_number ? `Page: ${match.page_number}` : null,
      match.match_percent ? `Match: ${match.match_percent}%` : null,
    ].filter(Boolean).join(" · "),
  });

  const goToMatchZone = (row) => navigate(`/projects/${id}/match?zone=${encodeURIComponent(row.zone)}`);

  // 2026-02-01 — Replace-image flow.
  // Backend `POST /projects/{id}/reference` already overwrites the image
  // with $set AND $unsets the stale analysis / mock_analysis /
  // products_detected fields so the UI can't display outdated pins
  // against a photo the user no longer has.
  const openReplacePicker = () => {
    if (replacing) return;
    const proceed = window.confirm(
      "Replace the reference image?\n\nThis will delete the current photo and clear the existing specification, product detections and pins. You'll need to regenerate the specification for the new image."
    );
    if (!proceed) return;
    replaceInputRef.current?.click();
  };

  const handleReplaceFile = async (e) => {
    const file = e.target?.files?.[0];
    // Always reset the input so the same file can be re-selected later.
    if (e.target) e.target.value = "";
    if (!file) return;
    setReplacing(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      await api.post(`/projects/${id}/reference`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      // Local state cleanup — mirror the backend $unset so the UI
      // reflects the empty state immediately, then re-fetch fresh data.
      setRegionResult(null);
      setProducts([]);
      setFocusedIndex(null);
      setFocusedProductIndex(null);
      setImgError(false);
      setProject((prev) => prev ? { ...prev, mock_analysis: undefined, products_detected: undefined, analysis: undefined, status: "draft" } : prev);
      await fetchProject();
      toast.success("Reference image replaced. Generate specification to analyse the new photo.");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setReplacing(false);
    }
  };

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

  // Sprint 5 — Only render pins the LLM actually anchored (row.pin present).
  // If no coordinate is available, we deliberately show NO marker (better
  // than a misleading random position). Ephemeral / region-based rows never
  // carry pins.
  const imagePins = activeRows
    .map((r, i) => {
      if (r?.pin && typeof r.pin.x === "number" && typeof r.pin.y === "number") {
        return { x: r.pin.x, y: r.pin.y, label: r.zone, rowIndex: i };
      }
      return null;
    })
    .filter(Boolean);

  // 2026-02-27 (round 5) — product pins.  Only shown on the full-image
  // view (never for ephemeral zone results, since region analysis
  // doesn't re-run the products pipeline).
  const productPins = (activeEphemeral ? [] : products)
    .map((p, i) => {
      if (p?.pin && typeof p.pin.x === "number" && typeof p.pin.y === "number") {
        return { x: p.pin.x, y: p.pin.y, label: p.product_name, index: i };
      }
      return null;
    })
    .filter(Boolean);

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
          <div className="space-y-6">
            <DemoModeBanner />

            {/* Side-by-side layout — image + intelligence stick to the
                left while materials, products and shortlist scroll on
                the right. Eliminates the constant scroll-up / scroll-down
                to check the reference against a zone card. */}
            <div className="grid lg:grid-cols-12 gap-6" data-testid="analysis-split-layout">
              <div className="lg:col-span-5 lg:sticky lg:top-6 lg:self-start space-y-4 lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto lg:pr-1" data-testid="analysis-left-column">
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
                      {/* 2026-02-01 (round 4) — two-button scope
                          choice. Never silently merged (see
                          server.py `_find_catalogue_matches`). The
                          previously-used scope is highlighted so the
                          user sees which library the current spec was
                          built against. */}
                      <button
                        onClick={() => analyse("admin")}
                        disabled={busy}
                        className={`inline-flex items-center justify-center gap-2 rounded-full px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-60 ${
                          hasAnalysis && libraryScope === "admin"
                            ? "bg-charcoal text-paper hover:bg-charcoal/85"
                            : "border border-charcoal text-charcoal hover:bg-charcoal hover:text-paper"
                        }`}
                        data-testid="analyse-admin-btn"
                        title="Search the platform's global MaterialMatch library"
                      >
                        {busy && libraryScope === "admin" ? (
                          <><RefreshCw className="w-4 h-4 animate-spin" strokeWidth={1.5} /> Reading…</>
                        ) : (
                          <>
                            <BookOpen className="w-4 h-4" strokeWidth={1.5} />
                            {hasAnalysis && libraryScope === "admin" ? "Regenerate · Admin Library" : "Check Admin Library"}
                          </>
                        )}
                      </button>
                      <button
                        onClick={() => analyse("own")}
                        disabled={busy}
                        className={`inline-flex items-center justify-center gap-2 rounded-full px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-60 ${
                          hasAnalysis && libraryScope === "own"
                            ? "bg-charcoal text-paper hover:bg-charcoal/85"
                            : "border border-charcoal text-charcoal hover:bg-charcoal hover:text-paper"
                        }`}
                        data-testid="analyse-own-btn"
                        title="Search only the catalogues you've uploaded"
                      >
                        {busy && libraryScope === "own" ? (
                          <><RefreshCw className="w-4 h-4 animate-spin" strokeWidth={1.5} /> Reading…</>
                        ) : (
                          <>
                            <UserIcon className="w-4 h-4" strokeWidth={1.5} />
                            {hasAnalysis && libraryScope === "own" ? "Regenerate · My Catalogue" : "Check My Catalogue"}
                          </>
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
                      <button
                        type="button"
                        onClick={openReplacePicker}
                        disabled={replacing || busy}
                        className="inline-flex items-center justify-center gap-2 border border-stone-border hover:border-charcoal text-charcoal rounded-full px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-60"
                        data-testid="replace-reference-btn"
                        title="Replace the reference image and clear stale analysis"
                      >
                        <ImagePlus className={`w-4 h-4 ${replacing ? "animate-pulse" : ""}`} strokeWidth={1.5} />
                        {replacing ? "Replacing…" : "Replace image"}
                      </button>
                      <input
                        ref={replaceInputRef}
                        type="file"
                        accept="image/jpeg,image/png,image/webp"
                        className="hidden"
                        onChange={handleReplaceFile}
                        data-testid="replace-reference-input"
                      />
                    </div>
                  </div>

                  {refImg && !imgError ? (
                    <RegionSelector
                      projectId={id}
                      imgSrc={refImg}
                      onAnalyzed={(result) => setRegionResult(result)}
                      pins={imagePins}
                      focusedPinIndex={focusedIndex}
                      onHoverPin={setFocusedIndex}
                      productPins={productPins}
                      focusedProductIndex={focusedProductIndex}
                      onHoverProductPin={setFocusedProductIndex}
                      libraryScope={libraryScope}
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

              <div className="lg:col-span-7 space-y-8" data-testid="analysis-right-column">
                {/* 2026-02-01 (round 4) — zero-results-in-admin-scope
                    CTA. When the user searched Admin Library and every
                    detected material row came back with an empty
                    catalogue_matches list, prompt them to upload their
                    own supplier PDF and try their private catalogue. */}
                {hasAnalysis && libraryScope === "admin" && activeRows.length > 0 &&
                  activeRows.every((r) => !(r?.catalogue_matches || []).length) && (
                    <div
                      className="bg-ochre-soft/50 border border-ochre/30 rounded-2xl p-5 flex items-start gap-4"
                      data-testid="analysis-zero-admin-cta"
                    >
                      <div className="w-10 h-10 rounded-full bg-ochre/20 grid place-items-center flex-shrink-0">
                        <UploadCloud className="w-5 h-5 text-ochre" strokeWidth={1.5} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="font-display text-base font-semibold text-charcoal mb-1">
                          No matches in the Admin Library
                        </div>
                        <p className="text-sm text-charcoal/80 mb-3">
                          We couldn&apos;t find any strong matches in the platform library for the surfaces in this photo. Try uploading your own supplier PDF — we&apos;ll extract every swatch and re-search against just your catalogue.
                        </p>
                        <div className="flex items-center gap-2 flex-wrap">
                          <button
                            type="button"
                            onClick={() => navigate("/library")}
                            className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-4 py-2 text-sm font-medium transition-colors"
                            data-testid="analysis-zero-upload-cta"
                          >
                            <UploadCloud className="w-4 h-4" strokeWidth={1.75} />
                            Upload a supplier PDF
                          </button>
                          <button
                            type="button"
                            onClick={() => analyse("own")}
                            disabled={busy}
                            className="inline-flex items-center gap-2 border border-charcoal text-charcoal hover:bg-charcoal hover:text-paper rounded-full px-4 py-2 text-sm font-medium transition-colors disabled:opacity-60"
                            data-testid="analysis-zero-try-own-cta"
                          >
                            <UserIcon className="w-4 h-4" strokeWidth={1.75} />
                            Try My Catalogue instead
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
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
                      Hit <span className="font-medium text-charcoal">Check Admin Library</span> or <span className="font-medium text-charcoal">Check My Catalogue</span> to detect surfaces, finishes and sourcing guidance for this reference.
                    </p>
                  </div>
                )}

                {/* Products → Product Alternatives */}
                <ProductsSection
                  products={products}
                  projectId={id}
                  onAddToShortlist={addProductToShortlist}
                  shortlistedNames={shortlistedProductNames}
                  focusedProductIndex={focusedProductIndex}
                  onHoverProductCard={setFocusedProductIndex}
                />

                {/* Sourceable Shortlist */}
                <ShortlistSection
                  items={shortlist}
                  onRemove={removeFromShortlist}
                />
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
