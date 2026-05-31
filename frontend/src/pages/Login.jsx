/**
 * Login.jsx — Developer sign-in.
 */
import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { LogIn } from "lucide-react";
import AuthShell from "../components/AuthShell";
import usePageMeta from "../lib/usePageMeta";
import { api, setToken, setUser } from "../lib/api";

export default function Login() {
  usePageMeta({
    title: "Sign in · AUREM Dev",
    description: "Sign in to your AUREM Dev account and continue shipping features to your GitHub repo with an autonomous AI engineer.",
    canonical: "https://auremcto.com/login",
  });
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/auth/login", { email: email.trim(), password });
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id,
        email: r.data.email,
        name: r.data.name,
        tier: r.data.tier,
        tokens_remaining: r.data.tokens_remaining,
      });
      navigate("/dashboard");
    } catch (e) {
      setError(e?.response?.data?.detail || "Sign in failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      secondaryCta={
        <Link to="/signup" data-testid="auth-nav-signup" className="btn-primary" style={{ fontSize: 12 }}>
          Get started
        </Link>
      }
    >
      <section style={{ maxWidth: 440, margin: "20px auto" }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <span className="eyebrow">sign in</span>
          <h1 className="serif" style={{ fontSize: 32, marginTop: 10 }}>Welcome back, builder.</h1>
          <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
            Sign in to your AUREM Dev account.
          </p>
        </div>

        <div className="card" data-testid="login-card" style={{
          background: "rgba(20, 20, 28, 0.55)",
          backdropFilter: "blur(10px)",
          border: "1px solid rgba(255,255,255,0.06)",
        }}>
          <form onSubmit={submit} style={{ display: "grid", gap: 16 }}>
            <label>
              <span className="label-mini">Email</span>
              <input
                data-testid="login-email"
                className="input"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </label>

            <label>
              <span className="label-mini">Password</span>
              <input
                data-testid="login-password"
                className="input"
                type="password"
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>

            {error && (
              <div data-testid="login-error" style={{
                fontSize: 12, color: "var(--danger)",
                border: "1px solid rgba(255,107,107,0.25)",
                background: "rgba(255,107,107,0.06)",
                padding: "10px 12px", borderRadius: 4,
              }}>
                {error}
              </div>
            )}

            <button
              type="submit"
              data-testid="login-submit"
              className="btn-primary"
              disabled={busy}
              style={{ justifyContent: "center" }}
            >
              <LogIn size={15} /> {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div style={{
            marginTop: 22, paddingTop: 18,
            borderTop: "1px solid var(--border)",
            textAlign: "center", fontSize: 13, color: "var(--text-dim)",
          }}>
            No account yet?{" "}
            <Link to="/signup" data-testid="login-to-signup">Claim 1000 tokens →</Link>
          </div>
        </div>
      </section>
    </AuthShell>
  );
}
