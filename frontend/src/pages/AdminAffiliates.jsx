import React, { useCallback, useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import Header from "@/components/Header";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { ArrowLeft, Plus, Trash2, Edit2, X, ExternalLink } from "lucide-react";

const PRODUCT_CATEGORIES = [
  "lighting", "furniture", "decor", "art", "textile-decor",
  "fixture", "plant-planter", "electronics", "other",
];

const PLATFORMS = [
  "Pepperfry", "Urban Ladder", "IKEA India", "WoodenStreet",
  "Hafele India", "Amazon India", "Jaipur Rugs", "Fabindia", "Other",
];

const EMPTY = {
  product_name: "",
  product_category: "furniture",
  style_keywords: "",
  color_keywords: "",
  material_keywords: "",
  finish_keywords: "",
  affiliate_url: "",
  platform: "Pepperfry",
  product_image_url: "",
  price_inr: "",
  notes: "",
};

function toArray(v) {
  return (v || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}
function fromArray(a) {
  return (a || []).join(", ");
}

function AffiliateFormModal({ initial, onClose, onSave }) {
  const [form, setForm] = useState(() => {
    if (!initial) return EMPTY;
    return {
      ...EMPTY,
      ...initial,
      style_keywords: fromArray(initial.style_keywords),
      color_keywords: fromArray(initial.color_keywords),
      material_keywords: fromArray(initial.material_keywords),
      finish_keywords: fromArray(initial.finish_keywords),
    };
  });
  const [saving, setSaving] = useState(false);
  const isEdit = !!initial;

  const submit = async (e) => {
    e.preventDefault();
    if (!form.product_name.trim() || !form.affiliate_url.trim()) {
      toast.error("Name and affiliate URL are required");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        product_name: form.product_name.trim(),
        product_category: form.product_category,
        style_keywords: toArray(form.style_keywords),
        color_keywords: toArray(form.color_keywords),
        material_keywords: toArray(form.material_keywords),
        finish_keywords: toArray(form.finish_keywords),
        affiliate_url: form.affiliate_url.trim(),
        platform: form.platform,
        product_image_url: form.product_image_url.trim(),
        price_inr: form.price_inr.trim(),
        notes: form.notes.trim(),
      };
      let res;
      if (isEdit) {
        res = await api.put(`/admin/affiliates/${initial.id}`, payload);
      } else {
        res = await api.post("/admin/affiliates", payload);
      }
      toast.success(isEdit ? "Affiliate updated" : "Affiliate added");
      onSave(res.data);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSaving(false);
    }
  };

  const setField = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="fixed inset-0 z-50 bg-black/50 grid place-items-center p-4" data-testid="affiliate-modal">
      <form
        onSubmit={submit}
        className="bg-white rounded-2xl shadow-hover w-full max-w-2xl max-h-[90vh] overflow-y-auto"
      >
        <div className="sticky top-0 bg-white z-10 flex items-center justify-between p-5 border-b border-black/5">
          <h3 className="font-display text-xl font-semibold">
            {isEdit ? "Edit affiliate product" : "Add affiliate product"}
          </h3>
          <button type="button" onClick={onClose} className="p-1.5 hover:bg-neutral-100 rounded-full" data-testid="affiliate-modal-close">
            <X className="w-4 h-4" strokeWidth={1.5} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Product name *</label>
            <input
              value={form.product_name}
              onChange={setField("product_name")}
              className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black"
              placeholder="Brass Pendant Light – Fluted Glass"
              data-testid="affiliate-input-name"
              required
            />
          </div>
          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Category</label>
              <select
                value={form.product_category}
                onChange={setField("product_category")}
                className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black bg-white"
                data-testid="affiliate-input-category"
              >
                {PRODUCT_CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Platform</label>
              <select
                value={form.platform}
                onChange={setField("platform")}
                className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black bg-white"
                data-testid="affiliate-input-platform"
              >
                {PLATFORMS.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Affiliate URL *</label>
            <input
              value={form.affiliate_url}
              onChange={setField("affiliate_url")}
              className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black"
              placeholder="https://www.pepperfry.com/product/..."
              data-testid="affiliate-input-url"
              required
            />
          </div>
          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Product image URL</label>
              <input
                value={form.product_image_url}
                onChange={setField("product_image_url")}
                className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black"
                placeholder="https://..."
                data-testid="affiliate-input-image"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Price (INR)</label>
              <input
                value={form.price_inr}
                onChange={setField("price_inr")}
                className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black"
                placeholder="₹5,499"
                data-testid="affiliate-input-price"
              />
            </div>
          </div>
          {[
            ["style_keywords", "Style keywords (comma-separated)"],
            ["color_keywords", "Color keywords"],
            ["material_keywords", "Material keywords"],
            ["finish_keywords", "Finish keywords"],
          ].map(([k, label]) => (
            <div key={k}>
              <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">{label}</label>
              <input
                value={form[k]}
                onChange={setField(k)}
                className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black"
                placeholder="modern, minimalist, warm"
                data-testid={`affiliate-input-${k}`}
              />
            </div>
          ))}
          <div>
            <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Notes (internal)</label>
            <textarea
              value={form.notes}
              onChange={setField("notes")}
              rows={2}
              className="mt-1 w-full border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black"
              data-testid="affiliate-input-notes"
            />
          </div>
        </div>
        <div className="sticky bottom-0 bg-white flex items-center justify-end gap-2 p-5 border-t border-black/5">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-full border border-neutral-200 hover:bg-neutral-50"
            data-testid="affiliate-modal-cancel"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="px-5 py-2 text-sm rounded-full bg-black text-white hover:bg-black/80 disabled:opacity-60"
            data-testid="affiliate-modal-save"
          >
            {saving ? "Saving…" : isEdit ? "Save changes" : "Add product"}
          </button>
        </div>
      </form>
    </div>
  );
}

function AffiliateRow({ item, onEdit, onDelete }) {
  return (
    <tr className="hover:bg-neutral-50" data-testid={`affiliate-row-${item.id}`}>
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          {item.product_image_url ? (
            <img
              src={item.product_image_url}
              alt=""
              className="w-10 h-10 rounded-lg object-cover border border-black/5"
              onError={(e) => { e.currentTarget.style.display = "none"; }}
            />
          ) : (
            <div className="w-10 h-10 rounded-lg bg-neutral-100 border border-black/5" />
          )}
          <div>
            <div className="font-medium text-sm text-neutral-900">{item.product_name}</div>
            {item.price_inr && <div className="text-xs text-neutral-500">{item.price_inr}</div>}
          </div>
        </div>
      </td>
      <td className="px-4 py-3 text-sm">
        <span className="inline-flex text-[10px] px-2 py-0.5 rounded-full bg-black text-white uppercase tracking-wider">
          {item.product_category}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-neutral-700">{item.platform}</td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap gap-1 max-w-xs">
          {(item.style_keywords || []).slice(0, 4).map((k) => (
            <span key={k} className="text-[10px] px-2 py-0.5 rounded bg-neutral-100 text-neutral-700">{k}</span>
          ))}
        </div>
      </td>
      <td className="px-4 py-3">
        <a
          href={item.affiliate_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-neutral-600 hover:text-black"
          data-testid={`affiliate-link-${item.id}`}
        >
          Open <ExternalLink className="w-3 h-3" strokeWidth={2} />
        </a>
      </td>
      <td className="px-4 py-3 text-right whitespace-nowrap">
        <button
          onClick={() => onEdit(item)}
          className="inline-flex items-center gap-1 text-xs text-neutral-600 hover:text-black px-2 py-1"
          data-testid={`affiliate-edit-${item.id}`}
        >
          <Edit2 className="w-3.5 h-3.5" strokeWidth={1.5} />
          Edit
        </button>
        <button
          onClick={() => onDelete(item)}
          className="inline-flex items-center gap-1 text-xs text-rose-700 hover:text-rose-900 px-2 py-1 ml-1"
          data-testid={`affiliate-delete-${item.id}`}
        >
          <Trash2 className="w-3.5 h-3.5" strokeWidth={1.5} />
          Delete
        </button>
      </td>
    </tr>
  );
}

export default function AdminAffiliates() {
  const { user } = useAuth();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/admin/affiliates");
      setItems(data);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && user.role === "admin") load();
  }, [user, load]);

  if (user === null) {
    return <div className="min-h-screen grid place-items-center text-overline">Loading…</div>;
  }
  if (!user || user.role !== "admin") {
    return <Navigate to="/dashboard" replace />;
  }

  const openAdd = () => { setEditing(null); setModalOpen(true); };
  const openEdit = (item) => { setEditing(item); setModalOpen(true); };
  const closeModal = () => { setModalOpen(false); setEditing(null); };
  const onSaved = (saved) => {
    setItems((cur) => {
      const idx = cur.findIndex((x) => x.id === saved.id);
      if (idx === -1) return [saved, ...cur];
      const next = cur.slice();
      next[idx] = saved;
      return next;
    });
    closeModal();
  };
  const onDelete = async (item) => {
    if (!window.confirm(`Delete "${item.product_name}"?`)) return;
    try {
      await api.delete(`/admin/affiliates/${item.id}`);
      setItems((cur) => cur.filter((x) => x.id !== item.id));
      toast.success("Deleted");
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="admin-affiliates-page">
      <Header />
      <main className="max-w-7xl mx-auto px-6 py-12">
        <div className="flex items-center justify-between mb-6 flex-wrap gap-4">
          <Link to="/dashboard" className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black" data-testid="back-to-dashboard">
            <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
            Back to dashboard
          </Link>
          <button
            onClick={openAdd}
            className="inline-flex items-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-5 py-2.5 text-sm font-medium transition-colors"
            data-testid="add-affiliate-btn"
          >
            <Plus className="w-4 h-4" strokeWidth={1.5} />
            Add affiliate product
          </button>
        </div>

        <div className="mb-8">
          <div className="text-overline mb-2">Admin</div>
          <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
            Affiliate Products
          </h1>
          <p className="text-neutral-500 mt-2 max-w-2xl">
            Curate the Indian-market affiliate database. Each entry is
            keyword-matched against products detected in reference images.
          </p>
        </div>

        <div className="bg-white border border-black/5 rounded-2xl shadow-soft overflow-hidden" data-testid="affiliate-list">
          <div className="p-4 border-b border-black/5 flex items-baseline justify-between">
            <span className="text-sm font-medium">{items.length} products</span>
            <span className="text-xs text-neutral-500">Admin only</span>
          </div>
          {loading ? (
            <div className="p-8 text-center text-sm text-neutral-500">Loading…</div>
          ) : items.length === 0 ? (
            <div className="p-8 text-center text-sm text-neutral-500" data-testid="affiliate-empty">
              No affiliate products yet. Click <span className="font-medium text-black">Add affiliate product</span> to start.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-[#F3F2EE]/60">
                  <tr className="text-left">
                    <th className="px-4 py-3 text-overline font-semibold">Product</th>
                    <th className="px-4 py-3 text-overline font-semibold">Category</th>
                    <th className="px-4 py-3 text-overline font-semibold">Platform</th>
                    <th className="px-4 py-3 text-overline font-semibold">Style keywords</th>
                    <th className="px-4 py-3 text-overline font-semibold">Link</th>
                    <th className="px-4 py-3 text-overline font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-black/5">
                  {items.map((it) => (
                    <AffiliateRow key={it.id} item={it} onEdit={openEdit} onDelete={onDelete} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>

      {modalOpen && (
        <AffiliateFormModal initial={editing} onClose={closeModal} onSave={onSaved} />
      )}
    </div>
  );
}
