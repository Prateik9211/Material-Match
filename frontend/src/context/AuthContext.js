import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from "react";
import api, { formatApiError } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking, false = unauth, obj = auth
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    api
      .get("/auth/me")
      .then((r) => { if (mounted) setUser(r.data); })
      .catch(() => { if (mounted) setUser(false); });
    return () => {
      mounted = false;
    };
  }, []);

  const login = useCallback(async (email, password) => {
    setError("");
    try {
      const { data } = await api.post("/auth/login", { email, password });
      setUser(data);
      return true;
    } catch (e) {
      setError(formatApiError(e));
      return false;
    }
  }, []);

  const register = useCallback(async (email, password, name) => {
    setError("");
    try {
      const { data } = await api.post("/auth/register", { email, password, name });
      setUser(data);
      return true;
    } catch (e) {
      setError(formatApiError(e));
      return false;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } catch (err) {
      // Server-side logout failure is non-fatal; client state is cleared regardless.
      // Logging here aids debugging without surfacing to the user.
      console.error("Logout request failed:", err);
    }
    setUser(false);
  }, []);

  const value = useMemo(
    () => ({ user, login, register, logout, error, setError }),
    [user, login, register, logout, error]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
