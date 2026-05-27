import axios from "axios";
import { useEffect, useState } from "react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

// ---- Bearer token fallback ---------------------------------------------------
// Cookies are still the primary auth mechanism (httponly + secure + samesite=none).
// We ALSO keep a copy of the access token in localStorage and send it as
// `Authorization: Bearer …`. This makes auth resilient to any cross-site cookie
// quirk in production (third-party-cookie blocking, proxy stripping, intermittent
// SameSite enforcement, etc.). The backend already accepts either.
const TOKEN_KEY = "mm_access_token";

export function setAccessToken(token) {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getAccessToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

const api = axios.create({
  baseURL: API,
  withCredentials: true,
});

// Attach the bearer token (when present) to every outgoing request.
api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// If the server returns 401 on any request, clear the stale token so the next
// page load sends the user back through the auth flow cleanly.
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      // Only clear if we currently have a token — avoids clobbering on the
      // expected /auth/me 401 during the initial "am I logged in?" check.
      if (getAccessToken()) setAccessToken(null);
    }
    return Promise.reject(err);
  }
);

export default api;

export function formatApiError(err) {
  const detail = err?.response?.data?.detail;
  if (detail == null) return err?.message || "Something went wrong.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

// ---- Client config (cached single fetch) ----
let _configPromise = null;
export function fetchConfig() {
  if (!_configPromise) {
    _configPromise = api
      .get("/config")
      .then((r) => r.data)
      .catch(() => ({ enable_real_analysis: false }));
  }
  return _configPromise;
}

export function useConfig() {
  const [config, setConfig] = useState(null);
  useEffect(() => {
    let mounted = true;
    fetchConfig().then((c) => mounted && setConfig(c));
    return () => { mounted = false; };
  }, []);
  return config;
}
