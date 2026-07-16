/**
 * app/page.jsx — home page. Shows either the login form or the
 * user's data once signed in. Deliberately tiny so an LLM-generated
 * app can extend it easily.
 */
"use client";
import { useEffect, useState } from "react";

export default function Home() {
  const [me, setMe]     = useState(null);
  const [mode, setMode] = useState("login");
  const [email, setE]   = useState("");
  const [pw, setP]      = useState("");
  const [err, setErr]   = useState("");

  useEffect(() => {
    fetch("/api/auth/me").then(r => r.ok ? r.json() : null).then(setMe);
  }, []);

  async function submit(e) {
    e.preventDefault();
    setErr("");
    const r = await fetch(`/api/auth/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password: pw }),
    });
    const j = await r.json();
    if (!r.ok) return setErr(j.error || "Something went wrong.");
    setMe(j);
  }

  if (me?.email) {
    return (
      <main style={{ maxWidth: 480, margin: "80px auto", fontFamily: "system-ui", padding: 24 }}>
        <h1>Welcome, {me.name || me.email}</h1>
        <p>Your app is running. Add your own pages and routes to <code>app/</code>.</p>
        <form action="/api/auth/logout" method="post"><button>Sign out</button></form>
      </main>
    );
  }

  return (
    <main style={{ maxWidth: 400, margin: "80px auto", fontFamily: "system-ui", padding: 24 }}>
      <h1>{mode === "login" ? "Sign in" : "Create account"}</h1>
      <form onSubmit={submit}>
        <input type="email" placeholder="Email" value={email} onChange={(e) => setE(e.target.value)} required
               style={{ display: "block", width: "100%", padding: 10, marginBottom: 10 }} />
        <input type="password" placeholder="Password (≥ 8 chars)" value={pw} onChange={(e) => setP(e.target.value)} required
               style={{ display: "block", width: "100%", padding: 10, marginBottom: 10 }} />
        {err && <p style={{ color: "crimson" }}>{err}</p>}
        <button type="submit" style={{ padding: 10, width: "100%" }}>
          {mode === "login" ? "Sign in" : "Sign up"}
        </button>
      </form>
      <p style={{ marginTop: 12, textAlign: "center" }}>
        <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === "login" ? "signup" : "login"); }}>
          {mode === "login" ? "Don't have an account? Sign up" : "Already have one? Sign in"}
        </a>
      </p>
    </main>
  );
}
