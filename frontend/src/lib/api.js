import axios from "axios";
import { useEffect, useState } from "react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

const api = axios.create({
  baseURL: API,
  withCredentials: true,
});

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
