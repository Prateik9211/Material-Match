import React, { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { toast } from "sonner";

export default function NewProject() {
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.post("/projects", {
        name,
        client_name: client,
        notes,
      });
      toast.success("Project created");
      navigate(`/projects/${data.id}/upload`);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="new-project-page">
      <Header />
      <main className="max-w-2xl mx-auto px-6 py-12">
        <Link to="/dashboard" className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black mb-8" data-testid="back-to-dashboard">
          <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
          Back to dashboard
        </Link>

        <div className="text-overline mb-3">Step 1 of 3</div>
        <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight mb-3">New project.</h1>
        <p className="text-neutral-500 mb-10">Name your project and add optional client context.</p>

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
            <label className="text-overline">Client name</label>
            <input
              type="text"
              value={client}
              onChange={(e) => setClient(e.target.value)}
              placeholder="Optional"
              className="w-full bg-white border border-black/10 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
              data-testid="client-name-input"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-overline">Notes</label>
            <textarea
              rows={4}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Brief, mood, constraints…"
              className="w-full bg-white border border-black/10 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm resize-none"
              data-testid="project-notes-input"
            />
          </div>

          <button
            type="submit"
            disabled={busy || !name}
            className="w-full inline-flex items-center justify-center gap-2 bg-black text-white hover:bg-black/80 rounded-full py-3.5 font-medium transition-colors disabled:opacity-60"
            data-testid="create-project-btn"
          >
            {busy ? "Creating…" : "Continue to uploads"}
            {!busy && <ArrowRight className="w-4 h-4" strokeWidth={1.5} />}
          </button>
        </form>
      </main>
    </div>
  );
}
