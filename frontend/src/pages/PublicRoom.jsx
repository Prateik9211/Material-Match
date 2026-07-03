import React, { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import axios from "axios";
import RoomPresentation from "@/components/concept/RoomPresentation";
import { Printer } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const publicApi = axios.create({ baseURL: `${BACKEND_URL}/api` });

/**
 * Public read-only Concept Presentation.
 * URL:  /share/rooms/:slug        -> full presentation with a small bar
 *       /share/rooms/:slug?print=1 -> print-friendly (no chrome, page-break CSS)
 */
export default function PublicRoom() {
  const { slug } = useParams();
  const [searchParams] = useSearchParams();
  const printMode = searchParams.get("print") === "1";
  const [room, setRoom] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const resolveUrl = useCallback(async (img, kind) => {
    try {
      const { data } = await publicApi.get(`/public/rooms/${slug}/images/${kind}/${img.id}`);
      return data.data_url;
    } catch {
      return null;
    }
  }, [slug]);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const { data } = await publicApi.get(`/public/rooms/${slug}`);
        if (!cancel) setRoom(data);
      } catch {
        if (!cancel) setError("This concept is no longer available.");
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, [slug]);

  // Auto-open print dialog after content loads in print mode.
  useEffect(() => {
    if (printMode && !loading && room) {
      const t = setTimeout(() => window.print(), 800);
      return () => clearTimeout(t);
    }
  }, [printMode, loading, room]);

  if (loading) {
    return (
      <div className="min-h-screen grid place-items-center text-sm text-neutral-500" data-testid="public-loading">
        Preparing your design story…
      </div>
    );
  }
  if (error || !room) {
    return (
      <div className="min-h-screen grid place-items-center bg-[#FAF8F5] p-6" data-testid="public-error">
        <div className="text-center max-w-md">
          <div className="text-overline mb-2">Presentation unavailable</div>
          <h1 className="font-display text-3xl font-semibold mb-3">This link isn&rsquo;t active.</h1>
          <p className="text-neutral-500 text-sm">
            {error || "The designer may have disabled sharing. Please contact them for access."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={printMode ? "bg-paper" : "min-h-screen bg-[#FAF8F5]"} data-testid="public-room-page">
      {!printMode && (
        <div className="sticky top-0 z-40 bg-paper/90 backdrop-blur border-b border-stone-border-soft">
          <div className="max-w-5xl mx-auto px-6 py-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-6 h-6 rounded-full bg-charcoal" />
              <span className="text-sm font-medium text-charcoal">MaterialMatch<span className="text-warm-grey">.ai</span></span>
            </div>
            <a
              href={`?print=1`}
              className="inline-flex items-center gap-2 text-sm border border-stone-border rounded-full px-4 py-1.5 hover:bg-stone-panel text-charcoal"
              data-testid="public-print-btn"
            >
              <Printer className="w-3.5 h-3.5" strokeWidth={1.5} /> Print / Save PDF
            </a>
          </div>
        </div>
      )}
      <RoomPresentation
        room={room}
        specs={room.pinned_material_rows || []}
        products={room.pinned_products || []}
        catalogueMatches={room.catalogue_matches || []}
        resolveImageUrl={resolveUrl}
        printMode={printMode}
        projectName={room.project_name}
        clientName={room.client_name}
        designerName={room.designer_name}
      />
      <style>{`
        @media print {
          @page { margin: 18mm 14mm; }
          body { background: #FAF8F5 !important; }
          .print-story section { break-inside: avoid; page-break-inside: avoid; }
          .print-story header, .print-story footer { break-inside: avoid; }
          .print-story .page-break-after { break-after: page; page-break-after: always; }
        }
      `}</style>
    </div>
  );
}
