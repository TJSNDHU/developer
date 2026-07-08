/**
 * OAuthFinish.jsx — final hop after GitHub OAuth signup/sign-in.
 *
 * Backend redirects here with the JWT in the URL fragment (#token=…
 * &login=…) so the token never lands in server access logs or Referer
 * headers. We stash it in localStorage and route to /dashboard.
 *
 * Failure modes (no token, bad fragment) bounce the user back to /login
 * with a friendly error.
 */
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { setToken, setUser, api } from "../lib/api";
import { trackSignup } from "../lib/analytics";

export default function OAuthFinish() {
  const nav = useNavigate();
  const [status, setStatus] = useState("Signing you in…");

  useEffect(() => {
    async function run() {
      try {
        // Read fragment: "#token=eyJ...&login=octocat" (GitHub) OR
        // "#session_id=..." (Google via Emergent-managed OAuth).
        const raw = (window.location.hash || "").replace(/^#/, "");
        const parts = new URLSearchParams(raw);

        // ── Google path (Emergent-managed OAuth) ──────────────────
        // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR
        // REDIRECT URLS, THIS BREAKS THE AUTH
        const sessionId = parts.get("session_id");
        if (sessionId) {
          let d;
          try {
            const resp = await api.post("/auth/google/session", {
              session_id: sessionId,
            });
            d = resp.data || {};
          } catch (e) {
            setStatus("Google sign-in failed. Sending you back…");
            setTimeout(() => nav("/login?google=error", { replace: true }), 1000);
            return;
          }
          if (!d.token) {
            setStatus("No sign-in token returned. Redirecting…");
            setTimeout(() => nav("/login?google=missing_token", { replace: true }), 800);
            return;
          }
          setToken(d.token);
          setUser({
            user_id:          d.user_id,
            email:            d.email,
            name:             d.name || d.email,
            tier:             d.tier,
            tokens_remaining: d.tokens_remaining,
          });
          try {
            const ref = localStorage.getItem("aurem_ref");
            if (ref && ref !== d.user_id) {
              await api.post("/referrals/attribute", { ref_code: ref });
              localStorage.removeItem("aurem_ref");
            }
          } catch { /* non-blocking */ }
          try { localStorage.setItem("aurem_just_logged_in", "1"); } catch {}
          if (d.new) trackSignup();
          try { window.history.replaceState(null, "", "/oauth-finish"); } catch {}
          setStatus("Signed in. Redirecting to your dashboard…");
          nav("/dashboard", { replace: true });
          return;
        }

        // ── GitHub path (backend redirect with #token=…) ──────────
        const token = parts.get("token");
        const login = parts.get("login") || "";
        // Iter 156 — `new=1` is set by the backend only when this
        // OAuth callback minted a brand-new account row. We fire
        // the Google Ads signup conversion at most once per session.
        const isNewAccount = parts.get("new") === "1";
        if (!token) {
          setStatus("No sign-in token returned. Redirecting…");
          setTimeout(() => nav("/login?github=missing_token", { replace: true }), 800);
          return;
        }
        setToken(token);
        // Try to hydrate the user from /usage/me so the rest of the
        // app has tier/email/tokens_remaining without a refresh.
        try {
          const me = await api.get("/usage/me");
          if (me?.data?.user) {
            setUser({
              user_id:          me.data.user.user_id,
              email:            me.data.user.email,
              name:             me.data.user.name || login || me.data.user.email,
              tier:             me.data.user.tier,
              tokens_remaining: me.data.user.tokens_remaining,
            });
          }
        } catch {
          // Non-fatal — Dashboard will refetch.
          setUser({ name: login || "developer" });
        }
        // Flag for the PWA install popup so the Dashboard can prompt.
        try { localStorage.setItem("aurem_just_logged_in", "1"); } catch {}
        // Iter 156 — Google Ads signup conversion for GitHub OAuth
        // path. Only fires when backend flagged this hop as a new
        // account; return-logins are a no-op.
        if (isNewAccount) {
          trackSignup();
        }
        // Clear the fragment so a refresh doesn't replay it.
        try {
          window.history.replaceState(null, "", "/oauth-finish");
        } catch {}
        setStatus("Signed in. Redirecting to your dashboard…");
        nav("/dashboard", { replace: true });
      } catch (e) {
        setStatus("Sign-in failed. Sending you back to login…");
        setTimeout(() => nav("/login?github=error", { replace: true }), 1200);
      }
    }
    run();
  }, [nav]);

  return (
    <div
      data-testid="oauth-finish"
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0a0e1a",
        color: "#e9edf2",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 13,
        letterSpacing: "0.04em",
      }}
    >
      <div style={{ textAlign: "center" }}>
        <div
          style={{
            width: 28, height: 28, margin: "0 auto 18px",
            border: "2px solid rgba(255,255,255,0.15)",
            borderTopColor: "#ffce7a",
            borderRadius: "50%",
            animation: "auremspin 0.9s linear infinite",
          }}
        />
        <div data-testid="oauth-finish-status">{status}</div>
        <style>{`@keyframes auremspin { to { transform: rotate(360deg); } }`}</style>
      </div>
    </div>
  );
}
