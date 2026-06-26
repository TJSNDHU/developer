/**
 * Signup.jsx — Developer sign-up.
 */
import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Rocket, Github } from "lucide-react";
import AuthShell from "../components/AuthShell";
import usePageMeta from "../lib/usePageMeta";
import { api, setToken, setUser } from "../lib/api";
import { trackSignup } from "../lib/analytics";
import RobotGuide, { RobotGuideKeyframes, escapeHtml } from "../components/RobotGuide";

export default function Signup() {
  usePageMeta({
    title: "Sign up · Claim 1,000 free tokens · AUREM Dev",
    description: "Create your AUREM Dev account in 30 seconds. 1,000 tokens free on signup — no credit card required. Bring your own Anthropic, DeepSeek or Gemini key.",
    canonical: (typeof window !== "undefined" ? window.location.origin : "") + "/signup",
  });
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const rawNext = searchParams.get("next") || "";
  const next = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/dashboard";
  const [form, setForm] = useState({ name: "", email: "", password: "", password_confirm: "" });
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const u = (k, v) => setForm((prev) => ({ ...prev, [k]: v }));

  async function submit(e) {
    e.preventDefault();
    if (!agreed) {
      setError("Please agree to the Terms of Service and Privacy Policy to continue.");
      return;
    }
    if (form.password !== form.password_confirm) {
      setError("Passwords do not match. Please re-enter the same password in both fields.");
      return;
    }
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
        // Iter 212m-30 PR-2 — preserve signup timestamp so the chat
        // panel can render the 3-day founder-welcome tint.
        created_at: r.data.created_at,
      });
      // Iter 101 — if a referrer is in localStorage (set by App.jsx
      // when ?ref=… landed), attribute the new account now that we
      // have a token.
      try {
        const ref = localStorage.getItem("aurem_ref");
        if (ref && ref !== r.data.user_id) {
          await api.post("/referrals/attribute", { ref_code: ref });
          localStorage.removeItem("aurem_ref");
        }
      } catch { /* non-blocking */ }
      // Iter 156 — Google Ads conversion. Fire AFTER the account row
      // exists and BEFORE navigation so the event leaves the page
      // before React unmounts.
      trackSignup();
      try { localStorage.setItem("aurem_just_logged_in", "1"); } catch {}
      navigate(next, { replace: true });
    } catch (e) {
      setError(e?.response?.data?.detail || "Sign up failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      secondaryCta={
        <Link to="/login" data-testid="auth-nav-login" className="btn-ghost" style={{ fontSize: 12 }}>
          Sign in
        </Link>
      }
    >
      <section style={{ maxWidth: 460, margin: "20px auto" }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <span className="eyebrow">sign up</span>
          <h1 className="serif" style={{ fontSize: 32, marginTop: 10 }}>Create your developer account</h1>
          <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
            1,000 tokens free. No card required.
          </p>
        </div>

        <div className="card" data-testid="signup-card" style={{
          background: "rgba(20, 20, 28, 0.55)",
          backdropFilter: "blur(10px)",
          border: "1px solid rgba(255,255,255,0.06)",
        }}>
          <RobotGuideKeyframes />
          <RobotGuide
            testid="signup-robot-guide"
            kind={error ? "error" : "info"}
            message={
              error
                ? `Hmm — <strong>${escapeHtml(error)}</strong>. Fix the highlighted field and try again.`
                : !form.email
                  ? `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — creates your account instantly, no password needed.`
                  : !form.password
                    ? `Pick a <strong>strong password</strong> (6+ characters) below. <span class="ora-arrow">👇</span>`
                    : form.password_confirm && form.password !== form.password_confirm
                      ? `Passwords don&rsquo;t match yet — re-enter them to continue.`
                      : !agreed
                        ? `One last thing — <strong>accept the Terms & Privacy</strong> below to unlock signup. <span class="ora-arrow">👇</span>`
                        : `Ready to ship! Hit <strong>Create account & start</strong> below. <span class="ora-arrow">🚀</span>`
            }
          />
          {/* Iter 61 — GitHub OAuth-first CTA (parity with Login) */}
          <button
            type="button"
            data-testid="signup-github-oauth"
            onClick={() => {
              // Live origin keeps callback aligned with whichever
              // host (preview / auremcto.com / custom) loaded the app.
              const base = window.location.origin;
              window.location.href = `${base}/api/aurem-dev/github/oauth/connect?signup=1`;
            }}
            style={{
              padding: "12px 14px", marginBottom: 16,
              borderRadius: 4, cursor: "pointer",
              background: "#0d1117", color: "#fff",
              border: "1px solid #30363d",
              fontWeight: 600, fontSize: 13,
              display: "flex", alignItems: "center", gap: 8,
              justifyContent: "center", width: "100%",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
            }}
          >
            <Github size={14} /> Continue with GitHub
          </button>
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            margin: "0 0 16px 0", color: "var(--text-faint)",
            fontSize: 10, letterSpacing: "0.1em",
          }}>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.08)" }} />
            <span>OR EMAIL</span>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.08)" }} />
          </div>
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

            <label>
              <span className="label-mini">Confirm password</span>
              <input
                data-testid="signup-password-confirm"
                className="input"
                type="password"
                required
                minLength={6}
                value={form.password_confirm}
                onChange={(e) => u("password_confirm", e.target.value)}
                placeholder="Re-enter password"
              />
              {form.password_confirm && form.password !== form.password_confirm && (
                <span
                  data-testid="signup-password-mismatch"
                  style={{
                    fontSize: 11,
                    color: "var(--danger, #ff6b6b)",
                    marginTop: 6,
                    display: "block",
                  }}
                >
                  Passwords do not match
                </span>
              )}
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

            <label
              data-testid="signup-terms-label"
              style={{
                display: "flex", alignItems: "flex-start", gap: 8,
                fontSize: 12, color: "var(--text-dim)",
                cursor: "pointer", lineHeight: 1.5,
              }}>
              <input
                data-testid="signup-terms-checkbox"
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                style={{ marginTop: 3 }}
              />
              <span>
                I agree to the{" "}
                <Link to="/terms" target="_blank" rel="noopener" data-testid="signup-terms-link"
                      style={{ color: "var(--accent)", textDecoration: "underline" }}>
                  Terms of Service
                </Link>{" "}and{" "}
                <Link to="/privacy" target="_blank" rel="noopener" data-testid="signup-privacy-link"
                      style={{ color: "var(--accent)", textDecoration: "underline" }}>
                  Privacy Policy
                </Link>.
              </span>
            </label>

            <button
              type="submit"
              data-testid="signup-submit"
              className="btn-primary"
              disabled={busy || !agreed}
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
    </AuthShell>
  );
}
