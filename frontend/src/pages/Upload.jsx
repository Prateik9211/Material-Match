import React, { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import Header from "@/components/Header";
import UploadZone from "@/components/UploadZone";
import api, { formatApiError } from "@/lib/api";
import { ArrowLeft, ArrowRight, Sparkles } from "lucide-react";
import { toast } from "sonner";

function attachPreview(file) {
  return Object.assign(file, { preview: URL.createObjectURL(file) });
}

export default function UploadPage() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState(null);
  const [refFile, setRefFile] = useState(null);
  const [catFiles, setCatFiles] = useState([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState("");

  useEffect(() => {
    api.get(`/projects/${id}`)
      .then((r) => setProject(r.data))
      .catch((e) => toast.error(formatApiError(e)));
  }, [id]);

  const handleRef = (files) => {
    const f = files[0];
    if (!f) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(f.type)) {
      toast.error("Reference must be JPEG / PNG / WEBP");
      return;
    }
    setRefFile(attachPreview(f));
  };

  const handleCatalogue = (files) => {
    const allowed = files.filter((f) =>
      ["image/jpeg", "image/png", "image/webp", "application/pdf"].includes(f.type)
    );
    if (allowed.length !== files.length) {
      toast.message("Only images and PDFs accepted; others skipped.");
    }
    const withPreview = allowed.map((f) => f.type.startsWith("image/") ? attachPreview(f) : f);
    setCatFiles((prev) => [...prev, ...withPreview]);
  };

  const startAnalysis = async () => {
    if (!refFile) return toast.error("Please add a reference image");
    if (catFiles.length === 0) return toast.error("Please add at least one catalogue file");
    setBusy(true);
    try {
      setStep("Uploading reference image…");
      const fdRef = new FormData();
      fdRef.append("file", refFile);
      await api.post(`/projects/${id}/reference`, fdRef, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      setStep(`Uploading catalogue (${catFiles.length} files)…`);
      const fdCat = new FormData();
      catFiles.forEach((f) => fdCat.append("files", f));
      const catRes = await api.post(`/projects/${id}/catalogue`, fdCat, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      setStep("Starting AI analysis…");
      const fdAnal = new FormData();
      fdAnal.append("prompt", prompt);
      await api.post(`/projects/${id}/analyze`, fdAnal, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      toast.success(`Analysis started — ${catRes.data.count} catalogue items queued`);
      navigate(`/projects/${id}/analysis`);
    } catch (e) {
      toast.error(formatApiError(e));
      setBusy(false);
      setStep("");
    }
  };

  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="upload-page">
      <Header />
      <main className="max-w-6xl mx-auto px-6 py-12">
        <Link to="/dashboard" className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black mb-8" data-testid="back-to-dashboard">
          <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
          Back to dashboard
        </Link>

        <div className="text-overline mb-3">Step 2 of 3 · {project?.name || "Loading…"}</div>
        <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight mb-3">Add your visuals.</h1>
        <p className="text-neutral-500 mb-12 max-w-xl">Drop in the inspiration image and your catalogue. We'll handle the rest.</p>

        <div className="grid lg:grid-cols-2 gap-8 mb-10">
          <div className="bg-white border border-black/5 rounded-2xl p-6 shadow-soft">
            <UploadZone
              label="Reference inspiration"
              description="A single Pinterest-style or interior photograph"
              accept="image/jpeg,image/png,image/webp"
              files={refFile ? [refFile] : []}
              onFiles={handleRef}
              onRemove={() => setRefFile(null)}
              testid="upload-reference-image"
            />
          </div>

          <div className="bg-white border border-black/5 rounded-2xl p-6 shadow-soft">
            <UploadZone
              label="Catalogue / Products"
              description="PDF catalogue or product images (multiple allowed)"
              accept="image/jpeg,image/png,image/webp,application/pdf"
              multiple
              files={catFiles}
              onFiles={handleCatalogue}
              onRemove={(i) => setCatFiles((prev) => prev.filter((_, idx) => idx !== i))}
              testid="upload-catalogue"
            />
          </div>
        </div>

        <div className="bg-white border border-black/5 rounded-2xl p-6 shadow-soft mb-10">
          <label className="text-overline">Optional prompt</label>
          <textarea
            rows={3}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. focus on the floor and wall finishes; prefer sustainable materials"
            className="mt-2 w-full bg-white border border-black/10 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm resize-none"
            data-testid="prompt-input"
          />
        </div>

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 justify-end">
          {step && (
            <div className="text-sm text-neutral-600 mr-auto" data-testid="upload-step">
              {step}
            </div>
          )}
          <button
            onClick={startAnalysis}
            disabled={busy}
            className="inline-flex items-center justify-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-7 py-3.5 font-medium transition-colors disabled:opacity-60"
            data-testid="start-analysis-btn"
          >
            <Sparkles className="w-4 h-4" strokeWidth={1.5} />
            {busy ? "Working…" : "Start AI analysis"}
            {!busy && <ArrowRight className="w-4 h-4" strokeWidth={1.5} />}
          </button>
        </div>
      </main>
    </div>
  );
}
