/**
 * Signup.jsx — Developer sign-up.
 */
import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Rocket } from "lucide-react";
import Shell from "../components/Shell";
import { api, setToken, setUser } from "../lib/api";

export default function Signup() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const u = (k, v) => setForm((prev) => ({ ...prev, [k]: v }));

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/auth/signup", {
        email: form.email.trim(),
        password: form.password,
        name: form.name.trim() || undefined,
      });
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
      setError(e?.response?.data?.detail || "Sign up failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <section style={{ maxWidth: 460, margin: "60px auto" }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <span className="eyebrow">sign up</span>
          <h1 className="serif" style={{ fontSize: 32, marginTop: 10 }}>Create your developer account</h1>
          <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
            1,000 tokens free. No card required.
          </p>
        </div>

        <div className="card" data-testid="signup-card">
          <form onSubmit={submit} style={{ display: "grid", gap: 16 }}>
            <label>
              <span className="label-mini">Full name (optional)</span>
              <input
                data-testid="signup-name"
                className="input"
                value={form.name}
                onChange={(e) => u("name", e.target.value)}
                placeholder="Ada Lovelace"
              />
            </label>

            <label>
              <span className="label-mini">Email</span>
              <input
                data-testid="signup-email"
                className="input"
                type="email"
                required
                value={form.email}
                onChange={(e) => u("email", e.target.value)}
                placeholder="you@company.com"
              />
            </label>

            <label>
              <span className="label-mini">Password (min 6 chars)</span>
              <input
                data-testid="signup-password"
                className="input"
                type="password"
                required
                minLength={6}
                value={form.password}
                onChange={(e) => u("password", e.target.value)}
              />
            </label>

            {error && (
              <div data-testid="signup-error" style={{
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
              data-testid="signup-submit"
              className="btn-primary"
              disabled={busy}
              style={{ justifyContent: "center" }}
            >
              <Rocket size={15} /> {busy ? "Creating account…" : "Create account & start"}
            </button>
          </form>

          <div style={{
            marginTop: 22, paddingTop: 18,
            borderTop: "1px solid var(--border)",
            textAlign: "center", fontSize: 13, color: "var(--text-dim)",
          }}>
            Already have an account?{" "}
            <Link to="/login" data-testid="signup-to-login">Sign in →</Link>
          </div>
        </div>
      </section>
    </Shell>
  );
}
