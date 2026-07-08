/**
 * Login.jsx — Developer sign-in.
 */
import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { LogIn, Github } from "lucide-react";
import GoogleIcon from "../components/GoogleIcon";
import AuthShell from "../components/AuthShell";
import usePageMeta from "../lib/usePageMeta";
import { api, setToken, setUser } from "../lib/api";
import RobotGuide, { RobotGuideKeyframes, escapeHtml } from "../components/RobotGuide";

export default function Login() {
  usePageMeta({
    title: "Sign in · AUREM Dev",
    description: "Sign in to your AUREM Dev account and continue shipping features to your GitHub repo with an autonomous AI engineer.",
    canonical: (typeof window !== "undefined" ? window.location.origin : "") + "/login",
  });
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // Honour ?next=… for safe in-app paths only (must start with /, not //)
  const rawNext = searchParams.get("next") || "";
  const next = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/dashboard";
  // Iter 113 — friendly banner when user cancelled the GitHub OAuth flow
  // and was sent back here.
  const cancelled = searchParams.get("github") === "cancelled";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Iter 212m-20 — Admin 2FA challenge state. `null` = normal email/pw
  // form. `{ mfa_token, email }` = the password was correct but the
  // account has TOTP enabled, so we now collect the 6-digit code.
  const [mfaState, setMfaState] = useState(null);
  const [mfaCode,  setMfaCode]  = useState("");
  const [useBackup, setUseBackup] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/auth/login", { email: email.trim(), password });
      // Iter 212m-20 — admin 2FA gate. Switch to the 2FA step instead
      // of issuing a session.
      if (r.data?.mfa_required && r.data?.mfa_token) {
        setMfaState({ mfa_token: r.data.mfa_token, email: r.data.email || email });
        setBusy(false);
        return;
      }
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id,
        email: r.data.email,
        name: r.data.name,
        tier: r.data.tier,
        tokens_remaining: r.data.tokens_remaining,
      });
      try { localStorage.setItem("aurem_just_logged_in", "1"); } catch { /* ignore */ }
      navigate(next, { replace: true });
    } catch (e) {
      setError(e?.response?.data?.detail || "Sign in failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  // Iter 212m-20 — submit the second-leg verify with the 6-digit code
  // (or a backup recovery code) in exchange for the real session JWT.
  async function submitMfa(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const payload = { mfa_token: mfaState.mfa_token };
    if (useBackup) payload.backup_code = mfaCode.trim();
    else           payload.code        = mfaCode.replace(/\D/g, "");
    try {
      const r = await api.post("/auth/login/2fa-verify", payload);
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id,
        email: r.data.email,
        name: r.data.name,
        tier: r.data.tier,
        tokens_remaining: r.data.tokens_remaining,
      });
      try { localStorage.setItem("aurem_just_logged_in", "1"); } catch { /* ignore */ }
      navigate(next, { replace: true });
    } catch (e) {
      setError(e?.response?.data?.detail || "Invalid 2FA code. Try again.");
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
          <RobotGuideKeyframes />
          <RobotGuide
            testid="login-robot-guide"
            kind={error ? "error" : "info"}
            message={
              error
                ? `Hmm — <strong>${escapeHtml(error)}</strong>. Try again, or use GitHub above.`
                : cancelled
                  ? `No worries — GitHub sign-in was cancelled. Try again, or sign in with email below. <span class="ora-arrow">👇</span>`
                  : email && password.length >= 6
                    ? `Looks good — hit <strong>Sign in</strong> when you&rsquo;re ready. <span class="ora-arrow">👇</span>`
                    : email
                      ? `Now enter your <strong>password</strong> and sign in. <span class="ora-arrow">👇</span>`
                      : `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — one tap, no password.`
            }
          />
          {/* Iter 113 — friendly banner when GitHub OAuth was cancelled */}
          {cancelled && (
            <div data-testid="login-github-cancelled" style={{
              padding: "10px 12px", marginBottom: 12,
              borderRadius: 4,
              background: "rgba(255,138,42,0.08)",
              border: "1px solid rgba(255,138,42,0.35)",
              color: "#ff8a2a", fontSize: 12, lineHeight: 1.5,
            }}>
              GitHub sign-in cancelled. You can try again or use email below.
            </div>
          )}
          {/* Iter 212m-183 — Google OAuth (Emergent-managed) one-click */}
          {/* REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR
              REDIRECT URLS, THIS BREAKS THE AUTH */}
          <button
            type="button"
            data-testid="login-google-oauth"
            onClick={() => {
              const redirectUrl = window.location.origin + "/oauth-finish";
              window.location.href =
                `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
            }}
            style={{
              padding: "12px 14px", marginBottom: 12,
              borderRadius: 4, cursor: "pointer",
              background: "#fff", color: "#1f1f1f",
              border: "1px solid #dadce0",
              fontWeight: 600, fontSize: 13,
              display: "flex", alignItems: "center", gap: 8,
              justifyContent: "center", width: "100%",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
            }}
          >
            <GoogleIcon size={16} /> Continue with Google
          </button>
          {/* Iter 50 — GitHub OAuth-first CTA (signup-killer removed) */}
          <button
            type="button"
            data-testid="login-github-oauth"
            onClick={() => {
              // Use the live origin so the OAuth callback returns to
              // whichever domain the user actually loaded the app from
              // (preview pod, auremcto.com, custom domain). Reading
              // REACT_APP_BACKEND_URL here would lock us to the build-
              // time value and break across environments.
              const base = window.location.origin;
              // Iter 113 — pass intent=login so backend redirects to
              // /login (not /signup) if the user clicks Cancel on
              // GitHub's consent screen.
              window.location.href = `${base}/api/aurem-dev/github/oauth/connect?signup=1&intent=login`;
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
          <form onSubmit={submit} style={{ display: mfaState ? "none" : "grid", gap: 16 }}>
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

          {/* Iter 212m-20 — second leg of admin 2FA login. Shown only
              after the password step returned `mfa_required:true`. */}
          {mfaState && (
            <form
              data-testid="login-2fa-form"
              onSubmit={submitMfa}
              style={{ display: "grid", gap: 16 }}
            >
              <div style={{
                fontSize: 12, color: "var(--text-dim)",
                padding: "10px 12px", borderRadius: 4,
                background: "rgba(255,197,96,0.06)",
                border: "1px solid rgba(255,197,96,0.25)",
              }}>
                🔐 Two-factor required for <strong>{mfaState.email}</strong>.
                {useBackup
                  ? " Enter a backup code from when you enrolled."
                  : " Enter the 6-digit code from your authenticator app."}
              </div>
              <label>
                <span className="label-mini">
                  {useBackup ? "Backup code" : "Authenticator code"}
                </span>
                <input
                  data-testid={useBackup ? "login-2fa-backup-input" : "login-2fa-code-input"}
                  className="input"
                  type="text"
                  required
                  autoFocus
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  placeholder={useBackup ? "XXXX-XXXX-XXXX" : "000000"}
                  inputMode={useBackup ? "text" : "numeric"}
                  pattern={useBackup ? undefined : "[0-9]{6}"}
                  maxLength={useBackup ? 14 : 6}
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    letterSpacing: useBackup ? "0.12em" : "0.4em",
                    fontSize: 18,
                    textAlign: "center",
                  }}
                />
              </label>
              {error && (
                <div data-testid="login-2fa-error" style={{
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
                data-testid="login-2fa-submit"
                className="btn-primary"
                disabled={busy || mfaCode.length < (useBackup ? 12 : 6)}
                style={{ justifyContent: "center" }}
              >
                <LogIn size={15} /> {busy ? "Verifying…" : "Verify & sign in"}
              </button>
              <div style={{
                display: "flex", justifyContent: "space-between",
                fontSize: 11, color: "var(--text-dim)",
              }}>
                <button
                  type="button"
                  data-testid="login-2fa-toggle-backup"
                  onClick={() => { setUseBackup((v) => !v); setMfaCode(""); setError(null); }}
                  style={{
                    background: "transparent", border: "none",
                    color: "var(--accent-2)", cursor: "pointer",
                    padding: 0, fontSize: 11,
                  }}
                >
                  {useBackup
                    ? "← Use authenticator code instead"
                    : "Use a backup code →"}
                </button>
                <button
                  type="button"
                  data-testid="login-2fa-cancel"
                  onClick={() => {
                    setMfaState(null); setMfaCode(""); setUseBackup(false);
                    setError(null);
                  }}
                  style={{
                    background: "transparent", border: "none",
                    color: "var(--text-faint)", cursor: "pointer",
                    padding: 0, fontSize: 11,
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}

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
