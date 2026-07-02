import React, { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import Header from "@/components/Header";
import DemoModeBanner from "@/components/DemoModeBanner";
import MatchSidebar from "@/components/match/MatchSidebar";
import MatchControls from "@/components/match/MatchControls";
import MatchResults from "@/components/match/MatchResults";
import MatchStepFlow from "@/components/match/MatchStepFlow";
import api, { formatApiError, useConfig } from "@/lib/api";
import { ArrowLeft, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { attachPreview, PDF_MIME_TYPES, IMG_MIME_TYPES } from "@/lib/match-utils";

export default function Match() {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const zone = searchParams.get("zone") || "";
  const config = useConfig();
  const realMatchActive = !!config?.enable_real_match;

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

  const addPdfs = (files) => {
    const allowed = files.filter((f) => PDF_MIME_TYPES.includes(f.type));
    if (allowed.length !== files.length) {
      toast.message("Only PDF files accepted in this slot; others ignored.");
    }
    setPdfFiles((prev) => [...prev, ...allowed]);
  };

  const addImgs = (files) => {
    const allowed = files.filter((f) => IMG_MIME_TYPES.includes(f.type));
    if (allowed.length !== files.length) {
      toast.message("Only JPEG / PNG / WEBP accepted; others ignored.");
    }
    setImgFiles((prev) => [...prev, ...allowed.map(attachPreview)]);
  };

  const removeAt = (setter) => (i) => setter((prev) => prev.filter((_, idx) => idx !== i));

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

      setProgressStep("Scanning catalogue pages…");
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

  const hasResults = !!result && (result.matches?.length > 0 || (result.warnings && result.warnings.length > 0));

  if (loading) {
    return (
      <div className="min-h-screen bg-[#FAF8F5]" data-testid="match-page">
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
      <div className="min-h-screen bg-[#FAF8F5]" data-testid="match-page">
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
    <div className="min-h-screen bg-[#FAF8F5]" data-testid="match-page">
      <Header />
      <main className="max-w-7xl mx-auto px-6 py-12">
        <Link to={`/projects/${id}/analysis`} className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black mb-6" data-testid="back-to-analysis">
          <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
          Back to analysis
        </Link>

        <div className="mb-10">
          <div className="text-overline mb-2">Catalogue Match · {project?.name || "—"}</div>
          <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
            Match catalogues for <span className="text-neutral-400">{selected.zone}</span>.
          </h1>
          {project?.client_name && (
            <p className="text-neutral-500 mt-2">Client: {project.client_name}</p>
          )}
        </div>

        <div className="grid lg:grid-cols-12 gap-8">
          <MatchSidebar
            project={project}
            refImg={refImg}
            imgError={imgError}
            onImgError={() => setImgError(true)}
            selected={selected}
          />

          <section className="lg:col-span-7 space-y-6">
            <DemoModeBanner />

            <MatchStepFlow
              current={
                busy ? 2 : hasResults ? 3 : (pdfFiles.length + imgFiles.length > 0 ? 2 : 1)
              }
            />

            <MatchControls
              prompt={prompt}
              onPromptChange={setPrompt}
              pdfFiles={pdfFiles}
              imgFiles={imgFiles}
              onAddPdfs={addPdfs}
              onRemovePdf={removeAt(setPdfFiles)}
              onAddImgs={addImgs}
              onRemoveImg={removeAt(setImgFiles)}
              busy={busy}
              progressStep={progressStep}
              hasResults={hasResults}
              realMatchActive={realMatchActive}
              onRunMatch={runMatch}
            />

            <MatchResults busy={busy} hasResults={hasResults} result={result} />
          </section>
        </div>
      </main>
    </div>
  );
}
