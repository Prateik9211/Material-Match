import React, { useCallback, useEffect, useState } from "react";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Users, Trash2 } from "lucide-react";

/**
 * Admin-only real-user list. Uses `GET /admin/users` which already
 * filters out obvious test-account patterns (`*@test.com`, `*@t.com`,
 * `*@materialmatch.ai`). Sorted most-recent-first server-side.
 */
export default function AdminUsers() {
  const [users, setUsers] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [purging, setPurging] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [ur, sr] = await Promise.all([
        api.get("/admin/users"),
        api.get("/admin/stats"),
      ]);
      setUsers(ur.data.users || []);
      setStats(sr.data);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Two-step purge: first click = dry-run + native confirm, second click
  // (after confirm) = real ?confirm=true. The founder-whitelist inside
  // the backend endpoint is the ultimate safety.
  const purgeTestUsers = useCallback(async () => {
    if (purging) return;
    setPurging(true);
    try {
      const dry = await api.post("/admin/purge-test-users");
      const target = dry.data?.target_users ?? 0;
      const assoc = dry.data?.associated_records || {};
      if (target === 0) {
        toast.success("No test accounts to purge.");
        return;
      }
      const ok = window.confirm(
        `Purge ${target} test users?\n\n` +
        `Also removes:\n` +
        `  ${assoc.projects || 0} projects\n` +
        `  ${assoc.reports || 0} reports\n` +
        `  ${assoc.reviews || 0} reviews\n` +
        `  ${assoc.rooms || 0} rooms\n` +
        `  ${assoc.usage_counters || 0} usage counters\n` +
        `  ${assoc.ke_uploads || 0} catalogue uploads\n\n` +
        `Protected: ${(dry.data?.protected_whitelist || []).join(", ")}`
      );
      if (!ok) { toast("Cancelled."); return; }
      const real = await api.post("/admin/purge-test-users?confirm=true");
      toast.success(real.data?.message || "Purge complete.");
      await fetchAll();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setPurging(false);
    }
  }, [purging, fetchAll]);

  const fmt = (iso) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  return (
    <div className="min-h-screen bg-paper" data-testid="page-admin-users">
      <Header />
      <main className="max-w-5xl mx-auto px-6 py-12">
        <div className="text-overline mb-2">Admin</div>
        <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight mb-2">
          Registered users
        </h1>
        <p className="text-warm-grey mb-8">
          Real signups only — test accounts (<code>*@test.com</code>,
          {" "}<code>*@t.com</code>, <code>*@materialmatch.ai</code>) are
          filtered out.
        </p>

        <div className="mb-8">
          <button
            type="button"
            onClick={purgeTestUsers}
            disabled={purging}
            className="inline-flex items-center gap-2 text-xs px-4 py-2 rounded-full border border-stone-border-soft text-warm-grey hover:text-charcoal hover:border-charcoal disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            data-testid="purge-test-users-btn"
            title="Delete test/artefact accounts and their orphan projects & reviews. Founder whitelist is always protected."
          >
            <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
            {purging ? "Working…" : "Purge test accounts"}
          </button>
        </div>

        {stats && (
          <div className="grid sm:grid-cols-2 gap-4 mb-6" data-testid="admin-users-stats">
            <div className="bg-white border border-stone-border-soft rounded-2xl p-5 shadow-soft">
              <div className="text-overline mb-1">Real users</div>
              <div className="font-display text-3xl font-bold text-charcoal" data-testid="stat-real-users">
                {stats.real_users}
              </div>
            </div>
            <div className="bg-white border border-stone-border-soft rounded-2xl p-5 shadow-soft">
              <div className="text-overline mb-1">All rows (incl. test)</div>
              <div className="font-display text-3xl font-bold text-neutral-400" data-testid="stat-total-users">
                {stats.total_users}
              </div>
            </div>
          </div>
        )}

        {loading ? (
          <div className="text-warm-grey" data-testid="users-loading">Loading…</div>
        ) : users.length === 0 ? (
          <div className="bg-white border border-stone-border-soft rounded-2xl p-10 text-center text-warm-grey" data-testid="users-empty">
            No real users yet.
          </div>
        ) : (
          <div className="bg-white border border-stone-border-soft rounded-2xl shadow-soft overflow-hidden">
            <table className="w-full text-sm" data-testid="users-table">
              <thead className="bg-stone-panel/50 border-b border-stone-border-soft">
                <tr className="text-left">
                  <th className="px-4 py-3 text-xs uppercase tracking-widest text-warm-grey font-semibold">Email</th>
                  <th className="px-4 py-3 text-xs uppercase tracking-widest text-warm-grey font-semibold">Name</th>
                  <th className="px-4 py-3 text-xs uppercase tracking-widest text-warm-grey font-semibold">Region</th>
                  <th className="px-4 py-3 text-xs uppercase tracking-widest text-warm-grey font-semibold">Signed up</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u, i) => (
                  <tr
                    key={u.id}
                    className="border-t border-stone-border-soft/70"
                    data-testid={`user-row-${i}`}
                  >
                    <td className="px-4 py-3 font-medium text-charcoal" data-testid={`user-email-${i}`}>
                      {u.email}
                      {u.role === "admin" && (
                        <span className="ml-2 text-[10px] uppercase tracking-widest bg-ochre-soft text-ochre px-1.5 py-0.5 rounded">
                          Admin
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-warm-grey">{u.name || "—"}</td>
                    <td className="px-4 py-3 text-warm-grey">{u.region}</td>
                    <td className="px-4 py-3 text-warm-grey text-xs" data-testid={`user-date-${i}`}>{fmt(u.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
