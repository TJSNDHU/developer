/**
 * api.js — Single source for backend URL + auth helpers.
 */
import axios from "axios";

// Backend base URL: REACT_APP_BACKEND_URL (preview) or VITE_API_URL (local)
const BACKEND =
  (typeof process !== "undefined" && process.env && process.env.REACT_APP_BACKEND_URL) ||
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_URL) ||
  "";

export const API_BASE = `${BACKEND}/api/aurem-dev`;

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
});

// Attach JWT from localStorage on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("aurem_token");
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Health check (no /aurem-dev prefix — it's on /api/health)
export const healthApi = axios.create({
  baseURL: `${BACKEND}/api`,
  timeout: 10000,
});

export function setToken(t) {
  if (t) localStorage.setItem("aurem_token", t);
  else localStorage.removeItem("aurem_token");
}

export function getToken() {
  return localStorage.getItem("aurem_token");
}

export function setUser(u) {
  if (u) localStorage.setItem("aurem_user", JSON.stringify(u));
  else localStorage.removeItem("aurem_user");
}

export function getUser() {
  try {
    const raw = localStorage.getItem("aurem_user");
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function logout() {
  setToken(null);
  setUser(null);
  window.location.href = "/login";
}
