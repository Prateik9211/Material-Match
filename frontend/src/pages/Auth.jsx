import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import AuthForm from "@/components/auth/AuthForm";

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

  let submitLabel = mode === "login" ? "Sign in" : "Create account";
  if (busy) submitLabel = "Working…";

  const heading = mode === "login" ? "Sign in to continue." : "Start matching materials.";
  const overline = mode === "login" ? "Welcome back" : "Create account";

  return (
    <div className="min-h-screen bg-[#FAF8F5] flex flex-col" data-testid="auth-page">
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
              <div className="text-overline mb-3">{overline}</div>
              <h1 className="font-display text-4xl font-bold tracking-tight">{heading}</h1>
            </div>

            <AuthForm
              mode={mode}
              name={name} onNameChange={setName}
              email={email} onEmailChange={setEmail}
              password={password} onPasswordChange={setPassword}
              busy={busy}
              error={error}
              submitLabel={submitLabel}
              onSubmit={submit}
            />

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
