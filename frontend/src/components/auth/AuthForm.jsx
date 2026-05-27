import React from "react";
import { Mail, Lock, User as UserIcon, ArrowRight } from "lucide-react";

/**
 * Shared field renderer for the auth form. Keeps the input/icon shell
 * consistent across login + register without per-field repetition.
 */
function FieldShell({ label, icon: Icon, children }) {
  return (
    <div className="space-y-1.5">
      <label className="text-overline">{label}</label>
      <div className="relative">
        <Icon className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-neutral-400" strokeWidth={1.5} />
        {children}
      </div>
    </div>
  );
}

/**
 * Stateless presentational form for both Login and Register. The page-level
 * `Auth` component owns submit + state — this component only renders.
 */
export default function AuthForm({
  mode,                 // "login" | "register"
  name, onNameChange,
  email, onEmailChange,
  password, onPasswordChange,
  busy,
  error,
  submitLabel,
  onSubmit,
}) {
  return (
    <form onSubmit={onSubmit} className="space-y-4" data-testid="auth-form">
      {mode === "register" && (
        <FieldShell label="Name" icon={UserIcon}>
          <input
            type="text"
            value={name}
            onChange={(e) => onNameChange(e.target.value)}
            placeholder="Your full name"
            className="w-full pl-11 pr-4 py-3.5 bg-white border border-black/10 rounded-xl focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
            data-testid="auth-name-input"
          />
        </FieldShell>
      )}

      <FieldShell label="Email" icon={Mail}>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => onEmailChange(e.target.value)}
          placeholder="you@studio.com"
          className="w-full pl-11 pr-4 py-3.5 bg-white border border-black/10 rounded-xl focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
          data-testid="auth-email-input"
        />
      </FieldShell>

      <FieldShell label="Password" icon={Lock}>
        <input
          type="password"
          required
          minLength={6}
          value={password}
          onChange={(e) => onPasswordChange(e.target.value)}
          placeholder="Min. 6 characters"
          className="w-full pl-11 pr-4 py-3.5 bg-white border border-black/10 rounded-xl focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
          data-testid="auth-password-input"
        />
      </FieldShell>

      {error && (
        <div
          className="text-sm bg-red-50 text-red-700 border border-red-100 rounded-xl px-4 py-3"
          data-testid="auth-error"
        >
          {error}
        </div>
      )}

      <button
        type="submit"
        disabled={busy}
        className="w-full bg-black text-white hover:bg-black/80 rounded-full py-3.5 font-medium transition-colors inline-flex items-center justify-center gap-2 disabled:opacity-60"
        data-testid="auth-submit-btn"
      >
        {submitLabel}
        {!busy && <ArrowRight className="w-4 h-4" strokeWidth={1.5} />}
      </button>

      <button
        type="button"
        disabled
        title="Coming soon"
        className="w-full border border-black/10 bg-white text-neutral-600 rounded-full py-3.5 font-medium inline-flex items-center justify-center gap-2 disabled:opacity-60 cursor-not-allowed"
        data-testid="auth-google-btn"
      >
        Continue with Google
        <span className="text-xs text-neutral-400">(soon)</span>
      </button>
    </form>
  );
}
