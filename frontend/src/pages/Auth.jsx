import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { ArrowRight, Mail, Lock, User as UserIcon } from "lucide-react";
import { toast } from "sonner";

export default function Auth() {
  const [searchParams] = useSearchParams();
  const initialMode = searchParams.get("mode") === "register" ? "register" : "login";
  const [mode, setMode] = useState(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const { user, login, register, error, setError } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (user) navigate("/dashboard", { replace: true });
  }, [user, navigate]);

  useEffect(() => {
    setError("");
  }, [mode, setError]);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    const ok = mode === "login"
      ? await login(email, password)
      : await register(email, password, name || email.split("@")[0]);
    setBusy(false);
    if (ok) {
      toast.success(mode === "login" ? "Welcome back" : "Account created");
      navigate("/dashboard");
    }
  };

  return (
    <div className="min-h-screen bg-[#F9F9F8] flex flex-col" data-testid="auth-page">
      <div className="px-6 py-6">
        <Link to="/" className="inline-flex items-center gap-2" data-testid="auth-back-home">
          <div className="w-6 h-6 rounded-md bg-black grid place-items-center">
            <div className="w-2.5 h-2.5 rounded-sm bg-white"></div>
          </div>
          <span className="font-display font-bold text-sm">MaterialMatch.AI</span>
        </Link>
      </div>

      <div className="flex-1 grid lg:grid-cols-2">
        {/* Left visual */}
        <div className="hidden lg:block relative overflow-hidden">
          <img
            src="https://images.unsplash.com/photo-1772423945486-8e941a5c01a9?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Nzd8MHwxfHNlYXJjaHwyfHxhcmNoaXRlY3R1cmFsJTIwbWF0ZXJpYWwlMjB0ZXh0dXJlJTIwd29vZCUyMHN0b25lfGVufDB8fHx8MTc3OTgxNDY0OHww&ixlib=rb-4.1.0&q=85"
            alt="Materials"
            className="absolute inset-0 w-full h-full object-cover"
          />
          <div className="absolute inset-0 bg-gradient-to-tr from-black/30 to-transparent"></div>
          <div className="absolute bottom-12 left-12 right-12 text-white">
            <h2 className="font-display text-4xl font-bold leading-tight mb-3">
              The fastest way<br />from inspiration to spec.
            </h2>
            <p className="text-white/80 max-w-sm">Trusted by interior designers and architecture practices.</p>
          </div>
        </div>

        {/* Right form */}
        <div className="flex items-center justify-center p-6 sm:p-12">
          <div className="w-full max-w-md space-y-8">
            <div>
              <div className="text-overline mb-3">{mode === "login" ? "Welcome back" : "Create account"}</div>
              <h1 className="font-display text-4xl font-bold tracking-tight">
                {mode === "login" ? "Sign in to continue." : "Start matching materials."}
              </h1>
            </div>

            <form onSubmit={submit} className="space-y-4" data-testid="auth-form">
              {mode === "register" && (
                <div className="space-y-1.5">
                  <label className="text-overline">Name</label>
                  <div className="relative">
                    <UserIcon className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-neutral-400" strokeWidth={1.5} />
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="Your full name"
                      className="w-full pl-11 pr-4 py-3.5 bg-white border border-black/10 rounded-xl focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
                      data-testid="auth-name-input"
                    />
                  </div>
                </div>
              )}
              <div className="space-y-1.5">
                <label className="text-overline">Email</label>
                <div className="relative">
                  <Mail className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-neutral-400" strokeWidth={1.5} />
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@studio.com"
                    className="w-full pl-11 pr-4 py-3.5 bg-white border border-black/10 rounded-xl focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
                    data-testid="auth-email-input"
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-overline">Password</label>
                <div className="relative">
                  <Lock className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-neutral-400" strokeWidth={1.5} />
                  <input
                    type="password"
                    required
                    minLength={6}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Min. 6 characters"
                    className="w-full pl-11 pr-4 py-3.5 bg-white border border-black/10 rounded-xl focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm"
                    data-testid="auth-password-input"
                  />
                </div>
              </div>

              {error && (
                <div className="text-sm bg-red-50 text-red-700 border border-red-100 rounded-xl px-4 py-3" data-testid="auth-error">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={busy}
                className="w-full bg-black text-white hover:bg-black/80 rounded-full py-3.5 font-medium transition-colors inline-flex items-center justify-center gap-2 disabled:opacity-60"
                data-testid="auth-submit-btn"
              >
                {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
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

            <div className="text-sm text-neutral-600 text-center">
              {mode === "login" ? (
                <>
                  New to MaterialMatch?{" "}
                  <button onClick={() => setMode("register")} className="font-medium text-black underline" data-testid="auth-switch-register">
                    Create account
                  </button>
                </>
              ) : (
                <>
                  Already have an account?{" "}
                  <button onClick={() => setMode("login")} className="font-medium text-black underline" data-testid="auth-switch-login">
                    Sign in
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
