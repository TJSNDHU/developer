/**
 * App.jsx — Personal Track starter UI (cookie-based auth).
 *
 * Iter 212m-238 security hardening: NO localStorage token access.
 * The backend sets httpOnly cookies on sign-in — the browser sends
 * them on every request automatically. We just add
 * `credentials: 'include'` and check the 401 → refresh → retry loop.
 */
import React, { useEffect, useState, useCallback } from "react";

const API = import.meta.env.VITE_API_URL || "";

/** Wrapper around fetch that:
 *   1. Always sends cookies (credentials: 'include')
 *   2. On 401, silently attempts POST /auth/refresh and retries once
 *   3. Never touches localStorage — cookies handle everything
 */
async function apiFetch(path, opts = {}) {
  const url = `${API}${path.startsWith("/") ? path : `/${path}`}`;
  const doCall = () => fetch(url, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  let r = await doCall();
  if (r.status === 401 && !opts._retried) {
    const refresh = await fetch(`${API}/api/auth/refresh`, {
      method: "POST", credentials: "include",
    });
    if (refresh.ok) r = await doCall();
  }
  return r;
}

export default function App() {
  const [me, setMe]         = useState(null);
  const [ready, setReady]   = useState(false);
  const [mode, setMode]     = useState("login");
  const [email, setEmail]   = useState("");
  const [pw, setPw]         = useState("");
  const [err, setErr]       = useState("");
  const [busy, setBusy]     = useState(false);

  const checkSession = useCallback(async () => {
    try {
      const r = await apiFetch("/api/auth/me");
      if (r.ok) setMe(await r.json());
    } catch { /* offline is fine */ }
    setReady(true);
  }, []);
  useEffect(() => { checkSession(); }, [checkSession]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await fetch(`${API}/api/auth/${mode}`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password: pw }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || "Something went wrong.");
      setMe(j);
    } catch (ex) {
      setErr(ex.message);
    } finally { setBusy(false); }
  }

  async function signout() {
    await fetch(`${API}/api/auth/logout`, {
      method: "POST", credentials: "include",
    });
    setMe(null);
  }

  if (!ready) return null;

  if (me?.email) {
    return (
      <main style={styles.wrap}>
        <h1 style={styles.h1}>Welcome, {me.email}</h1>
        <p style={styles.p}>Your app is running. Extend <code>ui/src/</code>.</p>
        <button style={styles.btn} onClick={signout}>Sign out</button>
      </main>
    );
  }

  return (
    <main style={styles.wrap}>
      <h1 style={styles.h1}>{mode === "login" ? "Sign in" : "Create account"}</h1>
      <form onSubmit={submit}>
        <input style={styles.input} type="email" placeholder="Email"
               value={email} onChange={(e) => setEmail(e.target.value)}
               required autoComplete="email" />
        <input style={styles.input} type="password" placeholder="Password (≥ 8 chars)"
               value={pw} onChange={(e) => setPw(e.target.value)}
               required autoComplete={mode === "login" ? "current-password" : "new-password"} />
        {err && <p style={styles.err}>{err}</p>}
        <button style={styles.btn} type="submit" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Sign up"}
        </button>
      </form>
      <p style={styles.p}>
        {mode === "login" ? (
          <>
            <a href="#" onClick={(e) => { e.preventDefault(); setMode("signup"); }}>
              Don&apos;t have an account? Sign up
            </a>
            <br />
            <a href="/reset-password" style={{ fontSize: 13, marginTop: 8, display: "inline-block" }}>
              Forgot your password?
            </a>
          </>
        ) : (
          <a href="#" onClick={(e) => { e.preventDefault(); setMode("login"); }}>
            Already have an account? Sign in
          </a>
        )}
      </p>
    </main>
  );
}

const styles = {
  wrap:  { maxWidth: 400, margin: "80px auto", fontFamily: "system-ui", padding: 24 },
  h1:    { fontSize: 24, marginBottom: 16 },
  input: { display: "block", width: "100%", padding: "10px 12px", marginBottom: 10, borderRadius: 6, border: "1px solid #ddd", font: "inherit" },
  btn:   { padding: "10px 16px", width: "100%", background: "#111", color: "#fff", border: "none", borderRadius: 6, fontWeight: 500, cursor: "pointer" },
  p:     { marginTop: 12, textAlign: "center" },
  err:   { color: "crimson", fontSize: 14, margin: "8px 0" },
};
