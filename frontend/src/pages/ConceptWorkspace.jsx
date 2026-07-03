import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Header from "@/components/Header";
import RoomEditor from "@/components/concept/RoomEditor";
import RoomPresentation from "@/components/concept/RoomPresentation";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { ArrowLeft, Plus, Trash2, Share2, Printer, Eye, Link as LinkIcon, X, Copy } from "lucide-react";

function ShareModal({ room, onClose, onShareChange }) {
  const shareUrl = useMemo(
    () => `${window.location.origin}/share/rooms/${room.share_slug}`,
    [room.share_slug]
  );
  const printUrl = `${shareUrl}?print=1`;
  const toggle = async (enabled) => {
    try {
      const { data } = await api.post(`/rooms/${room.id}/share`, { enabled });
      onShareChange({ share_enabled: data.share_enabled, share_slug: data.share_slug });
      toast.success(enabled ? "Share link is live" : "Share link disabled");
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };
  const copy = () => {
    navigator.clipboard.writeText(shareUrl);
    toast.success("Copied to clipboard");
  };
  return (
    <div className="fixed inset-0 z-50 bg-black/50 grid place-items-center p-4" data-testid="share-modal">
      <div className="bg-white rounded-2xl shadow-hover w-full max-w-lg overflow-hidden">
        <div className="flex items-center justify-between p-5 border-b border-black/5">
          <div>
            <div className="text-overline">Share</div>
            <h3 className="font-display text-xl font-semibold">Share {room.name}</h3>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-neutral-100" data-testid="share-modal-close">
            <X className="w-4 h-4" strokeWidth={1.5} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div className="flex items-center justify-between p-4 rounded-xl border border-black/5 bg-[#F5F1EC]/40">
            <div>
              <div className="text-sm font-medium">Public link</div>
              <div className="text-xs text-neutral-500">Anyone with the link can view — no login.</div>
            </div>
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={!!room.share_enabled}
                onChange={(e) => toggle(e.target.checked)}
                className="w-4 h-4"
                data-testid="share-toggle"
              />
              <span className="text-sm font-medium">{room.share_enabled ? "On" : "Off"}</span>
            </label>
          </div>

          {room.share_enabled && (
            <>
              <div className="flex items-center gap-2 border border-neutral-200 rounded-lg px-3 py-2">
                <LinkIcon className="w-4 h-4 text-neutral-400" strokeWidth={1.5} />
                <input readOnly value={shareUrl} className="flex-1 text-xs focus:outline-none bg-transparent" data-testid="share-url-input" />
                <button
                  onClick={copy}
                  className="inline-flex items-center gap-1 text-xs bg-black text-white rounded-full px-3 py-1 hover:bg-black/80"
                  data-testid="share-copy-btn"
                >
                  <Copy className="w-3 h-3" strokeWidth={2} /> Copy
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <a
                  href={shareUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 text-sm border border-neutral-200 rounded-full px-4 py-1.5 hover:bg-neutral-50"
                  data-testid="share-open-view"
                >
                  <Eye className="w-3.5 h-3.5" strokeWidth={1.5} /> Open client view
                </a>
                <a
                  href={printUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 text-sm border border-neutral-200 rounded-full px-4 py-1.5 hover:bg-neutral-50"
                  data-testid="share-open-print"
                >
                  <Printer className="w-3.5 h-3.5" strokeWidth={1.5} /> Print view
                </a>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function AddRoomModal({ onClose, onCreate }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("living");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return toast.error("Give this room a name");
    setBusy(true);
    try {
      const room = await onCreate(name.trim(), type);
      if (room) onClose();
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 bg-black/50 grid place-items-center p-4" data-testid="add-room-modal">
      <form onSubmit={submit} className="bg-white rounded-2xl shadow-hover w-full max-w-md overflow-hidden">
        <div className="flex items-center justify-between p-5 border-b border-black/5">
          <h3 className="font-display text-xl font-semibold">Add a room</h3>
          <button type="button" onClick={onClose} className="p-1.5 rounded-full hover:bg-neutral-100" data-testid="add-room-close">
            <X className="w-4 h-4" strokeWidth={1.5} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Name</label>
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Master Bedroom"
              className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black"
              data-testid="add-room-name"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Type</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value)}
              className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black bg-white"
              data-testid="add-room-type"
            >
              {[
                ["living", "Living"], ["bedroom", "Bedroom"], ["kitchen", "Kitchen"],
                ["bath", "Bath"], ["dining", "Dining"], ["office", "Office"],
                ["kids", "Kids"], ["outdoor", "Outdoor"], ["hallway", "Hallway"], ["custom", "Custom"],
              ].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 p-5 border-t border-black/5">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm rounded-full border border-neutral-200 hover:bg-neutral-50">
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="px-5 py-2 text-sm rounded-full bg-black text-white hover:bg-black/80 disabled:opacity-60"
            data-testid="add-room-submit"
          >
            {busy ? "Adding…" : "Add room"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function ConceptWorkspace() {
  const { id: projectId } = useParams();
  const [project, setProject] = useState(null);
  const [rooms, setRooms] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [showShare, setShowShare] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [activeRoomFull, setActiveRoomFull] = useState(null); // full room doc

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, r, prods] = await Promise.all([
        api.get(`/projects/${projectId}`),
        api.get(`/projects/${projectId}/rooms`),
        api.get(`/projects/${projectId}/products`).catch(() => ({ data: { products: [] } })),
      ]);
      setProject({ ...p.data, _products: prods.data.products || [] });
      setRooms(r.data);
      if (r.data.length && !activeId) setActiveId(r.data[0].id);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  // Load full room when active changes
  useEffect(() => {
    if (!activeId) { setActiveRoomFull(null); return; }
    let cancel = false;
    api.get(`/rooms/${activeId}`).then(({ data }) => {
      if (!cancel) setActiveRoomFull(data);
    }).catch((e) => toast.error(formatApiError(e)));
    return () => { cancel = true; };
  }, [activeId]);

  const createRoom = async (name, room_type) => {
    try {
      const { data } = await api.post(`/projects/${projectId}/rooms`, { name, room_type });
      setRooms((cur) => [...cur, data]);
      setActiveId(data.id);
      toast.success("Room added");
      return data;
    } catch (e) {
      toast.error(formatApiError(e));
      return null;
    }
  };

  const deleteRoom = async (room) => {
    if (!window.confirm(`Delete "${room.name}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/rooms/${room.id}`);
      setRooms((cur) => cur.filter((r) => r.id !== room.id));
      if (activeId === room.id) setActiveId(null);
      toast.success("Room deleted");
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const onRoomChanged = (updates) => {
    setActiveRoomFull((cur) => ({ ...cur, ...updates }));
    setRooms((cur) => cur.map((r) => r.id === updates.id ? { ...r, ...updates } : r));
  };

  // Resolve room image url from the private endpoint (for in-app preview)
  const resolveEditorImageUrl = async (img, kind) => {
    try {
      const { data } = await api.get(`/rooms/${activeRoomFull.id}/images/${kind}/${img.id}`);
      return data.data_url;
    } catch {
      return null;
    }
  };

  const projectSpecs = (project?.mock_analysis?.rows) || [];
  const projectProducts = project?._products || [];
  const projectCatalogueMatches = (project?.match_results?.top_matches) || [];
  const pinnedRowIds = new Set(activeRoomFull?.pinned_material_row_ids || []);
  const pinnedProdIds = new Set(activeRoomFull?.pinned_product_ids || []);
  const pinnedSpecs = projectSpecs.filter((r) => pinnedRowIds.has(String(r.id || r.row_id || r.zone || "")));
  const pinnedProducts = projectProducts.filter((p) => pinnedProdIds.has(String(p.id || "")));

  return (
    <div className="min-h-screen bg-[#FAF8F5]" data-testid="concept-workspace">
      <Header />
      <main className="max-w-7xl mx-auto px-6 py-10">
        <Link
          to={`/projects/${projectId}/analysis`}
          className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black mb-6"
          data-testid="back-to-analysis"
        >
          <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
          Back to analysis
        </Link>

        <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4 mb-10">
          <div>
            <div className="text-overline mb-2">Concept Presentation</div>
            <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
              {project?.name || "Loading…"}
            </h1>
            <p className="text-neutral-500 mt-2 max-w-2xl">
              Organize rooms, tell a visual story, and share a client-ready presentation.
            </p>
          </div>
          {activeRoomFull && (
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => setShowPreview(true)}
                className="inline-flex items-center gap-2 text-sm border border-neutral-200 rounded-full px-4 py-2 hover:bg-neutral-50"
                data-testid="preview-btn"
              >
                <Eye className="w-4 h-4" strokeWidth={1.5} /> Preview
              </button>
              <button
                onClick={() => setShowShare(true)}
                className="inline-flex items-center gap-2 bg-black text-white text-sm rounded-full px-5 py-2 hover:bg-black/80"
                data-testid="share-btn"
              >
                <Share2 className="w-4 h-4" strokeWidth={1.5} /> Share
              </button>
            </div>
          )}
        </div>

        {loading ? (
          <div className="text-center text-sm text-neutral-500 py-16">Loading…</div>
        ) : (
          <div className="grid lg:grid-cols-[260px_1fr] gap-8">
            {/* Sidebar */}
            <aside className="bg-white border border-black/5 rounded-2xl shadow-soft p-4 h-fit lg:sticky lg:top-24" data-testid="rooms-sidebar">
              <div className="flex items-center justify-between mb-3 px-2">
                <span className="text-overline">Rooms · {rooms.length}</span>
                <button
                  onClick={() => setShowAdd(true)}
                  className="p-1.5 rounded-full bg-black text-white hover:bg-black/80"
                  title="Add room"
                  data-testid="add-room-btn"
                >
                  <Plus className="w-3.5 h-3.5" strokeWidth={2} />
                </button>
              </div>
              {rooms.length === 0 ? (
                <p className="text-xs text-neutral-500 p-2">
                  No rooms yet. Click <span className="font-medium text-black">+</span> to add one.
                </p>
              ) : (
                <ul className="space-y-1">
                  {rooms.map((r) => (
                    <li key={r.id}>
                      <div
                        className={`group flex items-center justify-between gap-1 rounded-lg px-3 py-2 cursor-pointer ${
                          activeId === r.id ? "bg-neutral-900 text-white" : "hover:bg-neutral-100"
                        }`}
                        onClick={() => setActiveId(r.id)}
                        data-testid={`room-item-${r.id}`}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium truncate">{r.name}</div>
                          <div className={`text-[10px] uppercase tracking-widest ${activeId === r.id ? "text-white/60" : "text-neutral-500"}`}>
                            {r.room_type}
                            {r.share_enabled && " · shared"}
                          </div>
                        </div>
                        <button
                          onClick={(e) => { e.stopPropagation(); deleteRoom(r); }}
                          className={`opacity-0 group-hover:opacity-100 p-1 rounded ${activeId === r.id ? "hover:bg-white/10" : "hover:bg-black/5"}`}
                          data-testid={`delete-room-${r.id}`}
                          title="Delete room"
                        >
                          <Trash2 className="w-3.5 h-3.5" strokeWidth={1.5} />
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </aside>

            {/* Main content */}
            <div>
              {!activeRoomFull ? (
                <div className="bg-white border border-dashed border-black/10 rounded-2xl p-12 text-center" data-testid="rooms-empty">
                  <p className="text-neutral-500 mb-4">
                    {rooms.length === 0 ? "No rooms yet." : "Select a room to edit."}
                  </p>
                  {rooms.length === 0 && (
                    <button
                      onClick={() => setShowAdd(true)}
                      className="inline-flex items-center gap-2 bg-black text-white rounded-full px-5 py-2.5 text-sm font-medium hover:bg-black/80"
                      data-testid="empty-add-room-btn"
                    >
                      <Plus className="w-4 h-4" strokeWidth={1.5} /> Add your first room
                    </button>
                  )}
                </div>
              ) : (
                <RoomEditor
                  room={activeRoomFull}
                  specs={projectSpecs}
                  products={projectProducts}
                  onChange={onRoomChanged}
                />
              )}
            </div>
          </div>
        )}
      </main>

      {showAdd && <AddRoomModal onClose={() => setShowAdd(false)} onCreate={createRoom} />}
      {showShare && activeRoomFull && (
        <ShareModal
          room={activeRoomFull}
          onClose={() => setShowShare(false)}
          onShareChange={(updates) => setActiveRoomFull((cur) => ({ ...cur, ...updates }))}
        />
      )}
      {showPreview && activeRoomFull && (
        <div className="fixed inset-0 z-50 bg-black/60 overflow-y-auto p-4" data-testid="preview-modal">
          <div className="max-w-5xl mx-auto">
            <div className="flex items-center justify-end mb-3">
              <button
                onClick={() => setShowPreview(false)}
                className="inline-flex items-center gap-2 bg-white rounded-full px-4 py-2 text-sm hover:bg-neutral-100"
                data-testid="preview-close"
              >
                <X className="w-4 h-4" strokeWidth={1.5} /> Close preview
              </button>
            </div>
            <div className="bg-white rounded-2xl overflow-hidden">
              <RoomPresentation
                room={activeRoomFull}
                specs={pinnedSpecs}
                products={pinnedProducts}
                catalogueMatches={projectCatalogueMatches}
                resolveImageUrl={resolveEditorImageUrl}
                projectName={project?.name}
                clientName={project?.client_name}
                showCover={false}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
