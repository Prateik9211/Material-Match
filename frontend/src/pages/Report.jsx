import React, { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { ArrowLeft, Download, Printer } from "lucide-react";
import jsPDF from "jspdf";
import html2canvas from "html2canvas";
import { toast } from "sonner";

export default function Report() {
  const { id } = useParams();
  const [project, setProject] = useState(null);
  const [refImg, setRefImg] = useState(null);
  const [catImages, setCatImages] = useState({});
  const [busy, setBusy] = useState(false);
  const reportRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get(`/projects/${id}`);
        setProject(data);
        const ref = await api.get(`/projects/${id}/reference-image`);
        setRefImg(ref.data.data_url);

        const matches = (data?.analysis?.matches || []).slice(0, 8);
        for (const m of matches) {
          const ci = await api.get(`/projects/${id}/catalogue/${m.index}`);
          setCatImages((prev) => ({ ...prev, [m.index]: ci.data.data_url }));
        }
      } catch (e) {
        toast.error(formatApiError(e));
      }
    })();
  }, [id]);

  const downloadPDF = async () => {
    if (!reportRef.current) return;
    setBusy(true);
    try {
      const canvas = await html2canvas(reportRef.current, {
        scale: 2,
        useCORS: true,
        backgroundColor: "#ffffff",
        logging: false,
      });
      const imgData = canvas.toDataURL("image/jpeg", 0.92);
      const pdf = new jsPDF({ orientation: "p", unit: "pt", format: "a4" });
      const pdfWidth = pdf.internal.pageSize.getWidth();
      const pdfHeight = pdf.internal.pageSize.getHeight();
      const imgRatio = canvas.width / canvas.height;
      const pageRatio = pdfWidth / pdfHeight;

      let imgW = pdfWidth;
      let imgH = pdfWidth / imgRatio;

      if (imgH > pdfHeight) {
        // Multi-page
        const pageHeightPx = (canvas.width / pdfWidth) * pdfHeight;
        let y = 0;
        let page = 0;
        while (y < canvas.height) {
          const sliceCanvas = document.createElement("canvas");
          sliceCanvas.width = canvas.width;
          sliceCanvas.height = Math.min(pageHeightPx, canvas.height - y);
          const ctx = sliceCanvas.getContext("2d");
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, sliceCanvas.width, sliceCanvas.height);
          ctx.drawImage(canvas, 0, y, sliceCanvas.width, sliceCanvas.height, 0, 0, sliceCanvas.width, sliceCanvas.height);
          const slice = sliceCanvas.toDataURL("image/jpeg", 0.92);
          if (page > 0) pdf.addPage();
          const sliceH = (sliceCanvas.height / sliceCanvas.width) * pdfWidth;
          pdf.addImage(slice, "JPEG", 0, 0, pdfWidth, sliceH);
          y += pageHeightPx;
          page++;
        }
      } else {
        pdf.addImage(imgData, "JPEG", 0, 0, imgW, imgH);
      }

      pdf.save(`MaterialMatch-${(project?.name || "report").replace(/\s+/g, "_")}.pdf`);
    } catch (e) {
      console.error(e);
      toast.error("Failed to generate PDF");
    } finally {
      setBusy(false);
    }
  };

  const analysis = project?.analysis;

  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="report-page">
      <div className="no-print">
        <Header />
      </div>

      <main className="max-w-5xl mx-auto px-6 py-12">
        <div className="flex items-center justify-between mb-8 no-print flex-wrap gap-4">
          <Link to="/dashboard" className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black" data-testid="back-to-dashboard">
            <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
            Back to dashboard
          </Link>
          <div className="flex items-center gap-3">
            <button
              onClick={() => window.print()}
              className="inline-flex items-center gap-2 bg-white text-neutral-900 border border-black/10 hover:bg-black/5 rounded-full px-5 py-2.5 text-sm font-medium transition-colors"
              data-testid="print-btn"
            >
              <Printer className="w-4 h-4" strokeWidth={1.5} /> Print
            </button>
            <button
              onClick={downloadPDF}
              disabled={busy || !analysis}
              className="inline-flex items-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-5 py-2.5 text-sm font-medium transition-colors disabled:opacity-60"
              data-testid="download-pdf-btn"
            >
              <Download className="w-4 h-4" strokeWidth={1.5} />
              {busy ? "Generating…" : "Download PDF"}
            </button>
          </div>
        </div>

        {!analysis ? (
          <div className="bg-white border border-black/5 rounded-2xl p-12 text-center text-neutral-500" data-testid="report-pending">
            Report is generating. Please return to the analysis page.
          </div>
        ) : (
          <div ref={reportRef} className="bg-white rounded-3xl p-10 sm:p-14 shadow-soft" data-testid="report-content">
            {/* Header */}
            <div className="flex items-start justify-between pb-8 border-b border-black/10">
              <div>
                <div className="flex items-center gap-2 mb-4">
                  <div className="w-6 h-6 rounded-md bg-black grid place-items-center">
                    <div className="w-2.5 h-2.5 rounded-sm bg-white"></div>
                  </div>
                  <span className="font-display font-bold text-sm">MaterialMatch.AI</span>
                </div>
                <div className="text-overline mb-2">Material Match Report</div>
                <h1 className="font-display text-4xl font-bold tracking-tight">{project?.name}</h1>
                {project?.client_name && (
                  <p className="text-neutral-500 mt-2">Client: {project.client_name}</p>
                )}
              </div>
              <div className="text-right text-xs text-neutral-500">
                <div>Generated</div>
                <div className="font-medium text-neutral-900">{new Date().toLocaleDateString()}</div>
              </div>
            </div>

            {/* Reference */}
            <section className="py-10 grid sm:grid-cols-2 gap-8 border-b border-black/10">
              <div className="aspect-[4/5] rounded-2xl overflow-hidden bg-[#F3F2EE]">
                {refImg && <img src={refImg} alt="Reference" className="w-full h-full object-cover" crossOrigin="anonymous" />}
              </div>
              <div>
                <div className="text-overline mb-3">Reference</div>
                <h2 className="font-display text-2xl font-semibold mb-3">Brief & summary</h2>
                <p className="text-sm text-neutral-700 leading-relaxed mb-4">{analysis.summary}</p>
                {project?.notes && (
                  <div className="mb-4">
                    <div className="text-overline mb-1">Notes</div>
                    <p className="text-sm text-neutral-600 leading-relaxed">{project.notes}</p>
                  </div>
                )}
                {analysis.custom_prompt && (
                  <div className="mb-4">
                    <div className="text-overline mb-1">Prompt</div>
                    <p className="text-sm text-neutral-600 italic">"{analysis.custom_prompt}"</p>
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  {analysis.style_tags?.map((t, i) => (
                    <span key={i} className="text-xs px-2.5 py-1 rounded-full bg-[#F3F2EE] text-neutral-700">{t}</span>
                  ))}
                </div>
              </div>
            </section>

            {/* Color palette */}
            {analysis.color_palette?.length > 0 && (
              <section className="py-10 border-b border-black/10">
                <div className="text-overline mb-4">Color Palette</div>
                <div className="grid grid-cols-3 sm:grid-cols-6 gap-4">
                  {analysis.color_palette.map((c, i) => (
                    <div key={i}>
                      <div className="aspect-square rounded-xl border border-black/5" style={{ background: c.hex }}></div>
                      <div className="text-xs mt-2 font-medium">{c.name}</div>
                      <div className="text-[10px] text-neutral-500 font-mono">{c.hex}</div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Detected Materials */}
            {analysis.materials?.length > 0 && (
              <section className="py-10 border-b border-black/10">
                <div className="text-overline mb-4">Detected Materials</div>
                <div className="grid sm:grid-cols-2 gap-x-8 gap-y-4">
                  {analysis.materials.map((m, i) => (
                    <div key={i} className="flex items-start justify-between gap-4 pb-3 border-b border-black/5 last:border-0">
                      <div>
                        <div className="font-medium text-sm">{m.name}</div>
                        <div className="text-xs text-neutral-500">{m.category} · {m.finish}</div>
                        {m.location && <div className="text-xs text-neutral-400">{m.location}</div>}
                      </div>
                      <span className="text-xs font-mono text-neutral-500">{Math.round((m.confidence || 0) * 100)}%</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Matches */}
            <section className="py-10">
              <div className="text-overline mb-4">Top Matches from Catalogue</div>
              <div className="grid sm:grid-cols-2 gap-6">
                {(analysis.matches || []).slice(0, 8).map((m, i) => (
                  <div key={i} className="border border-black/5 rounded-2xl overflow-hidden">
                    <div className="aspect-[4/3] bg-[#F3F2EE]">
                      {catImages[m.index] && (
                        <img src={catImages[m.index]} alt={m.name} className="w-full h-full object-cover" crossOrigin="anonymous" />
                      )}
                    </div>
                    <div className="p-4">
                      <div className="flex items-start justify-between mb-2 gap-2">
                        <h4 className="font-display font-semibold text-sm truncate flex-1">{m.name}</h4>
                        <span className="text-xs font-mono font-bold bg-black text-white px-2 py-0.5 rounded-full">
                          {Math.round((m.score || 0) * 100)}%
                        </span>
                      </div>
                      {m.matched_material && (
                        <div className="text-[10px] text-neutral-500 mb-2">→ {m.matched_material}</div>
                      )}
                      <p className="text-xs text-neutral-600 leading-relaxed">{m.explanation}</p>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* Footer */}
            <div className="pt-8 border-t border-black/10 flex items-center justify-between text-xs text-neutral-500">
              <div>Generated by MaterialMatch.AI · Claude Sonnet 4.5</div>
              <div>Page 1</div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
