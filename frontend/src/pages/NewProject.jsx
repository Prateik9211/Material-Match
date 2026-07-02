import React, { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import Header from "@/components/Header";
import UploadZone from "@/components/UploadZone";
import api, { formatApiError } from "@/lib/api";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { toast } from "sonner";

function attachPreview(file) {
  return Object.assign(file, { preview: URL.createObjectURL(file) });
}

export default function NewProject() {
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [refFile, setRefFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState("");
  const navigate = useNavigate();

  const handleRef = (files) => {
    const f = files[0];
    if (!f) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(f.type)) {
      toast.error("Reference must be JPEG / PNG / WEBP");
      return;
    }
    setRefFile(attachPreview(f));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!refFile) {
      toast.error("Please add a reference image");
      return;
    }
    setBusy(true);
    try {
      setStep("Creating project…");
      const { data: project } = await api.post("/projects", {
        name,
        client_name: client,
        notes: "",
      });

      setStep("Uploading reference image…");
      const fd = new FormData();
      fd.append("file", refFile);
      await api.post(`/projects/${project.id}/reference`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      toast.success("Project created");
      navigate(`/projects/${project.id}/analysis`);
    } catch (err) {
      toast.error(formatApiError(err));
      setBusy(false);
      setStep("");
    }
  };

  return (
    <div className="min-h-screen bg-[#FAF8F5]" data-testid="new-project-page">
      <Header />
      <main className="max-w-3xl mx-auto px-6 py-12">
        <Link to="/dashboard" className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black mb-8" data-testid="back-to-dashboard">
          <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
          Back to dashboard
        </Link>

        <div className="text-overline mb-3">New project</div>
        <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight mb-3">Start a project.</h1>
        <p className="text-neutral-500 mb-10">Add the project details and drop in your reference image.</p>

        <form onSubmit={submit} className="bg-white border border-black/5 rounded-2xl p-8 space-y-6 shadow-soft" data-testid="new-project-form">
          <div className="space-y-1.5">
            <label className="text-overline">Project name *</label>
            <input
              required
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Riverside Loft — Living Room"
              className="w-full bg-white border border-black/10 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
              data-testid="project-name-input"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-overline">Client name (optional)</label>
            <input
              type="text"
              value={client}
              onChange={(e) => setClient(e.target.value)}
              placeholder="Acme Studio"
              className="w-full bg-white border border-black/10 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
              data-testid="client-name-input"
            />
          </div>

          <UploadZone
            label="Reference image *"
            description="A single Pinterest-style or interior photograph (JPEG / PNG / WEBP)"
            accept="image/jpeg,image/png,image/webp"
            files={refFile ? [refFile] : []}
            onFiles={handleRef}
            onRemove={() => setRefFile(null)}
            testid="upload-reference-image"
          />

          <div className="flex items-center gap-3 pt-2">
            {step && (
              <div className="text-sm text-neutral-600 mr-auto" data-testid="new-project-step">{step}</div>
            )}
            <button
              type="submit"
              disabled={busy || !name || !refFile}
              className="ml-auto inline-flex items-center justify-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-7 py-3.5 font-medium transition-colors disabled:opacity-60"
              data-testid="create-project-btn"
            >
              {busy ? "Working…" : "Create & continue"}
              {!busy && <ArrowRight className="w-4 h-4" strokeWidth={1.5} />}
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}
