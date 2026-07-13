/**
 * components/demo/demoSteps.jsx — Iter 212m-200
 *
 * Mock-UI scene renderers for the CSS-animated walkthrough.  Every
 * scene is a self-contained function receiving `{ tick, idx, playing }`
 * where `tick` is a 0..1 progress value within the current step.  This
 * lets each scene animate typing, dot pulses, LOOP phase progression,
 * etc. driven by the parent player rather than local timers.
 *
 * Rules
 *   • No real PATs.  All PAT inputs render `ghp_••••••••••••••`.
 *   • Repo owner/repo are the disposable `your-org/frontend`.
 *   • No external fetch, no auth.  Pure JSX.
 */
import React from "react";

// ─── shared token palette ──────────────────────────────────────
const C = {
  bg: "#05070d",
  panel: "#0f131e",
  panel2: "#131826",
  border: "#1f2937",
  border2: "#2d3748",
  text: "#e5e7eb",
  dim: "#94a3b8",
  faint: "#64748b",
  amber: "#f59e0b",
  amberSoft: "rgba(245,158,11,0.15)",
  green: "#22c55e",
  greenSoft: "rgba(34,197,94,0.15)",
  red: "#ef4444",
  mono: '"JetBrains Mono", ui-monospace, monospace',
};

// ─── shared bits ───────────────────────────────────────────────
const MonoLabel = ({ children, color }) => (
  <span
    style={{
      fontFamily: C.mono,
      fontSize: 10,
      letterSpacing: "0.18em",
      textTransform: "uppercase",
      color: color || C.faint,
    }}
  >
    {children}
  </span>
);

const Cursor = ({ x, y, label }) => (
  <div
    aria-hidden="true"
    style={{
      position: "absolute",
      left: x,
      top: y,
      pointerEvents: "none",
      zIndex: 20,
      transition: "left 320ms cubic-bezier(0.4, 0, 0.2, 1), top 320ms cubic-bezier(0.4, 0, 0.2, 1)",
    }}
  >
    <svg width="20" height="22" viewBox="0 0 20 22">
      <path
        d="M2 2 L2 18 L6 14 L9 20 L11.5 18.5 L8.5 12 L14 12 Z"
        fill="#fff"
        stroke="#000"
        strokeWidth="1.2"
      />
    </svg>
    {label && (
      <div
        style={{
          position: "absolute",
          top: 22,
          left: 14,
          padding: "3px 8px",
          background: "#000",
          border: `1px solid ${C.amber}`,
          borderRadius: 6,
          fontSize: 10,
          fontFamily: C.mono,
          color: C.amber,
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </div>
    )}
  </div>
);

// Utility: type-in text by tick progress.
const typed = (full, tick, startAt = 0, endAt = 1) => {
  if (tick <= startAt) return "";
  if (tick >= endAt) return full;
  const p = (tick - startAt) / (endAt - startAt);
  const n = Math.floor(full.length * p);
  return full.slice(0, n);
};

// ─── STEP 1 — Signup ───────────────────────────────────────────
const StepSignup = ({ tick }) => {
  const email = typed("founder@your-startup.com", tick, 0.15, 0.55);
  const pwd   = typed("••••••••••••", tick, 0.55, 0.85);
  const cursorAt = tick < 0.15
    ? { x: 480, y: 200 }
    : tick < 0.55
    ? { x: 480, y: 240 }
    : tick < 0.85
    ? { x: 480, y: 300 }
    : { x: 480, y: 380 };
  return (
    <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", position: "relative" }}>
      <div
        style={{
          width: 420,
          padding: 32,
          background: C.panel,
          border: `1px solid ${C.border}`,
          borderRadius: 14,
          boxShadow: `0 0 0 1px ${C.amberSoft} inset, 0 20px 60px rgba(0,0,0,0.5)`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18 }}>
          <div style={{ width: 34, height: 34, borderRadius: 8, background: C.amber, color: "#000", fontWeight: 800, display: "flex", alignItems: "center", justifyContent: "center" }}>A</div>
          <div>
            <div style={{ fontFamily: C.mono, fontSize: 15, fontWeight: 700, color: C.amber }}>ORA <span style={{ color: C.faint, fontWeight: 400, fontSize: 11 }}>by Aurem CTO</span></div>
            <div style={{ fontSize: 11, color: C.dim }}>Create your account · 10 free tasks</div>
          </div>
        </div>
        <MonoLabel>EMAIL</MonoLabel>
        <div style={{ marginTop: 6, marginBottom: 12, padding: "10px 12px", background: "#0a0e18", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, color: C.text, fontFamily: C.mono, minHeight: 20 }}>
          {email}<span style={{ opacity: tick > 0.15 && tick < 0.55 && Math.floor(tick * 20) % 2 ? 1 : 0 }}>│</span>
        </div>
        <MonoLabel>PASSWORD</MonoLabel>
        <div style={{ marginTop: 6, marginBottom: 18, padding: "10px 12px", background: "#0a0e18", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, color: C.text, fontFamily: C.mono, minHeight: 20 }}>
          {pwd}<span style={{ opacity: tick > 0.55 && tick < 0.85 && Math.floor(tick * 20) % 2 ? 1 : 0 }}>│</span>
        </div>
        <button
          disabled
          style={{
            width: "100%",
            padding: "12px",
            background: tick > 0.85 ? C.amber : "#3a2b0f",
            color: tick > 0.85 ? "#000" : C.faint,
            border: "none",
            borderRadius: 8,
            fontFamily: C.mono,
            fontWeight: 700,
            fontSize: 13,
            letterSpacing: "0.05em",
            transition: "background 200ms, color 200ms",
            transform: tick > 0.9 ? "scale(0.98)" : "scale(1)",
          }}
        >
          {tick > 0.9 ? "CREATING ACCOUNT…" : "CREATE ACCOUNT"}
        </button>
        <div style={{ marginTop: 14, textAlign: "center", fontSize: 11, color: C.faint }}>
          Already have an account? <span style={{ color: C.amber }}>Sign in</span>
        </div>
      </div>
      <Cursor x={cursorAt.x} y={cursorAt.y} label={tick > 0.88 ? "click" : null} />
    </div>
  );
};

// ─── STEP 2 — Empty Dashboard ─────────────────────────────────
const StepDashboard = ({ tick }) => (
  <div style={{ height: "100%", display: "grid", gridTemplateColumns: "180px 1fr", gap: 12, position: "relative" }}>
    {/* Sidebar */}
    <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
      <div style={{ fontFamily: C.mono, fontSize: 11, color: C.amber, fontWeight: 700, marginBottom: 4 }}>ORA</div>
      <div style={{ fontSize: 10, color: C.faint, marginBottom: 18 }}>by Aurem CTO</div>
      <MonoLabel>REPOSITORIES (0)</MonoLabel>
      <div style={{
        marginTop: 12,
        padding: "20px 12px",
        border: `1px dashed ${C.border2}`,
        borderRadius: 8,
        textAlign: "center",
        fontSize: 11,
        color: C.faint,
        fontFamily: C.mono,
      }}>
        No repos yet
      </div>
      <button
        style={{
          marginTop: 10,
          width: "100%",
          padding: "10px",
          background: tick > 0.5 ? C.amber : C.panel2,
          color: tick > 0.5 ? "#000" : C.text,
          border: `1px solid ${tick > 0.5 ? C.amber : C.border2}`,
          borderRadius: 8,
          fontFamily: C.mono,
          fontSize: 11,
          fontWeight: 700,
          cursor: "pointer",
          letterSpacing: "0.06em",
          transition: "all 200ms",
          boxShadow: tick > 0.5 ? `0 0 24px ${C.amberSoft}` : "none",
        }}
      >
        + ADD REPOSITORY
      </button>
    </div>
    {/* Main */}
    <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 24, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", gap: 14 }}>
      <div style={{ width: 62, height: 62, borderRadius: "50%", background: C.amberSoft, border: `1px solid ${C.amber}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 28 }}>👋</div>
      <div style={{ fontFamily: C.mono, fontSize: 20, fontWeight: 700, color: C.text }}>Welcome to ORA</div>
      <div style={{ fontSize: 13, color: C.dim, textAlign: "center", maxWidth: 360, lineHeight: 1.55 }}>
        Connect a GitHub repository to unlock chat with code context,
        automated scans, and one-click PR shipping.
      </div>
      <div style={{ marginTop: 10, padding: "8px 16px", background: C.amberSoft, border: `1px solid ${C.amber}`, borderRadius: 999, fontSize: 11, fontFamily: C.mono, color: C.amber, letterSpacing: "0.08em" }}>
        STEP 1 · CONNECT YOUR REPO
      </div>
    </div>
    <Cursor x={tick < 0.5 ? 400 : 90} y={tick < 0.5 ? 200 : 300} label={tick > 0.85 ? "click" : null} />
  </div>
);

// ─── STEP 3 — Connect GitHub via OAuth ────────────────────────
// Real product uses a `Connect GitHub` button that redirects to
// `/github/oauth/connect` → GitHub authorize screen → callback with
// scoped access. There is NO PAT-paste form anywhere in the modern
// onboarding, so the demo scene mirrors the same three beats:
//   Beat A (0.00 – 0.32): dashboard with a big "Connect GitHub" CTA,
//                          cursor drifts to it and clicks.
//   Beat B (0.32 – 0.90): GitHub authorize screen (GitHub Octocat
//                          header, scope list, "Authorize" button),
//                          cursor lands on Authorize, clicks.
//   Beat C (0.90 – 1.00): "Redirecting to AUREM…" spinner.  Step 4
//                          then picks up with the green dot.
const StepConnect = ({ tick }) => {
  const beatA = tick < 0.32;
  const beatB = tick >= 0.32 && tick < 0.9;
  const beatC = tick >= 0.9;

  // ── Beat A · CTA screen ────────────────────────────────────
  if (beatA) {
    // Cursor drifts diagonally to the button, "clicks" near the end.
    const p = tick / 0.32;
    return (
      <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", position: "relative" }}>
        <div
          style={{
            width: 480,
            padding: 32,
            background: C.panel,
            border: `1px solid ${C.amber}`,
            borderRadius: 14,
            boxShadow: `0 30px 80px rgba(0,0,0,0.6), 0 0 0 1px ${C.amberSoft} inset`,
            textAlign: "center",
          }}
        >
          {/* GitHub icon */}
          <div style={{ width: 56, height: 56, margin: "0 auto 14px", borderRadius: "50%", background: "#0d1117", border: `1px solid ${C.border2}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="30" height="30" viewBox="0 0 16 16" aria-hidden="true">
              <path fill="#fff" d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </div>
          <div style={{ fontFamily: C.mono, fontSize: 18, fontWeight: 700, color: C.text, marginBottom: 6 }}>
            Connect your GitHub
          </div>
          <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.6, marginBottom: 22 }}>
            One click. ORA opens a GitHub authorize screen — no tokens<br />
            to paste, no scripts to run. Scoped, revocable, private.
          </div>
          <button
            data-testid="demo-connect-github-btn"
            style={{
              width: "100%",
              padding: "12px",
              background: p > 0.85 ? "#e6e6e6" : "#fff",
              color: "#0d1117",
              border: "none",
              borderRadius: 8,
              fontFamily: C.mono,
              fontWeight: 700,
              fontSize: 13,
              letterSpacing: "0.05em",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              transform: p > 0.9 ? "scale(0.98)" : "scale(1)",
              transition: "background 200ms, transform 100ms",
            }}
          >
            <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
              <path fill="#0d1117" d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
            CONNECT GITHUB
          </button>
          <div style={{ marginTop: 12, fontSize: 10, color: C.faint, fontFamily: C.mono, letterSpacing: "0.06em" }}>
            🔒 We never see your GitHub password. Ever.
          </div>
        </div>
        <Cursor
          x={340 + p * 180}
          y={260 + p * 220}
          label={p > 0.9 ? "click" : null}
        />
      </div>
    );
  }

  // ── Beat B · GitHub authorize screen ───────────────────────
  if (beatB) {
    const bp = (tick - 0.32) / 0.58;
    return (
      <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", position: "relative", background: "#f6f8fa" }}>
        {/* Full-bleed light GitHub bg */}
        <div style={{ position: "absolute", inset: 0, background: "#f6f8fa" }} />
        {/* GitHub top nav strip */}
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 42, background: "#0d1117", display: "flex", alignItems: "center", padding: "0 22px", gap: 12 }}>
          <svg width="22" height="22" viewBox="0 0 16 16" aria-hidden="true">
            <path fill="#fff" d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
          </svg>
          <div style={{ color: "#c9d1d9", fontSize: 11, fontFamily: C.mono, letterSpacing: "0.06em" }}>
            github.com / authorize
          </div>
        </div>

        {/* Authorize card */}
        <div
          data-testid="demo-github-authorize"
          style={{
            position: "relative",
            zIndex: 2,
            width: 460,
            background: "#fff",
            border: "1px solid #d0d7de",
            borderRadius: 8,
            padding: 24,
            boxShadow: "0 8px 24px rgba(140,149,159,0.2)",
            color: "#1f2328",
            fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif',
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12, paddingBottom: 12, borderBottom: "1px solid #d0d7de", marginBottom: 12 }}>
            <div style={{ width: 40, height: 40, borderRadius: 8, background: C.amber, color: "#000", fontWeight: 800, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18 }}>A</div>
            <div>
              <div style={{ fontSize: 15, fontWeight: 600 }}>Authorize <span style={{ color: C.amber }}>AUREM CTO</span></div>
              <div style={{ fontSize: 11, color: "#57606a" }}>by aurem-cto · verified publisher</div>
            </div>
          </div>
          <div style={{ fontSize: 12.5, color: "#1f2328", lineHeight: 1.5, marginBottom: 12 }}>
            AUREM CTO by @yourname wants to access your GitHub account.
          </div>

          {/* Permissions */}
          <div style={{ background: "#f6f8fa", border: "1px solid #d0d7de", borderRadius: 6, padding: "10px 12px", marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, color: "#57606a", letterSpacing: "0.04em", textTransform: "uppercase" }}>
              Repositories
            </div>
            {[
              "Read repo contents & metadata",
              "Create & update pull requests",
              "Read commit history",
            ].map((t, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0", fontSize: 12.5 }}>
                <span style={{ width: 14, height: 14, borderRadius: 3, background: "#dafbe1", border: "1px solid #55d178", display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: "#1a7f37", fontWeight: 700 }}>✓</span>
                {t}
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: "#57606a", marginBottom: 14, padding: "6px 10px", background: "#fff8c5", border: "1px solid #d4a72c", borderRadius: 6 }}>
            🔒 GitHub never sends AUREM your password. You can revoke access any time from GitHub Settings.
          </div>

          <div style={{ display: "flex", gap: 8, flexDirection: "row-reverse" }}>
            <button
              data-testid="demo-authorize-btn"
              style={{
                padding: "8px 20px",
                background: bp > 0.85 ? "#1c7c34" : "#2da44e",
                color: "#fff",
                border: "1px solid rgba(31,35,40,0.15)",
                borderRadius: 6,
                fontWeight: 600,
                fontSize: 13,
                boxShadow: "0 1px 0 rgba(31,35,40,0.1)",
                transform: bp > 0.9 ? "scale(0.98)" : "scale(1)",
                transition: "background 150ms, transform 100ms",
              }}
            >
              Authorize AUREM CTO
            </button>
            <button
              style={{
                padding: "8px 16px",
                background: "#f6f8fa",
                color: "#1f2328",
                border: "1px solid rgba(31,35,40,0.15)",
                borderRadius: 6,
                fontSize: 13,
              }}
            >
              Cancel
            </button>
          </div>
        </div>
        <Cursor
          x={230 + bp * 350}
          y={200 + bp * 260}
          label={bp > 0.9 ? "click" : null}
        />
      </div>
    );
  }

  // ── Beat C · redirect flash ────────────────────────────────
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 14, position: "relative" }}>
      <div style={{ width: 46, height: 46, borderRadius: "50%", border: `3px solid ${C.border2}`, borderTopColor: C.amber, animation: "wpSpin 0.8s linear infinite" }} />
      <div style={{ fontFamily: C.mono, fontSize: 13, color: C.text, letterSpacing: "0.06em" }}>
        Redirecting to AUREM…
      </div>
      <div style={{ fontFamily: C.mono, fontSize: 11, color: C.faint }}>
        github.com → auremcto.com/github/oauth/callback
      </div>
      <style>{`@keyframes wpSpin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
};

// ─── STEP 4 — Repo Connected (green dot) ──────────────────────
const StepConnected = ({ tick }) => (
  <div style={{ height: "100%", display: "grid", gridTemplateColumns: "180px 1fr", gap: 12, position: "relative" }}>
    <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
      <div style={{ fontFamily: C.mono, fontSize: 11, color: C.amber, fontWeight: 700, marginBottom: 4 }}>ORA</div>
      <div style={{ fontSize: 10, color: C.faint, marginBottom: 18 }}>by Aurem CTO</div>
      <MonoLabel>REPOSITORIES (1)</MonoLabel>
      <div
        style={{
          marginTop: 12,
          padding: 12,
          background: C.panel2,
          border: `1px solid ${tick > 0.4 ? C.green : C.border2}`,
          borderRadius: 8,
          position: "relative",
          overflow: "hidden",
          transition: "border-color 300ms",
          boxShadow: tick > 0.4 ? `0 0 24px ${C.greenSoft}` : "none",
        }}
      >
        <div style={{ fontSize: 12, color: C.text, fontFamily: C.mono, marginBottom: 2 }}>your-org/frontend</div>
        <div style={{ fontSize: 10, color: tick > 0.4 ? C.green : C.faint, fontFamily: C.mono, display: "flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: tick > 0.4 ? C.green : C.faint,
              boxShadow: tick > 0.4 ? `0 0 8px ${C.green}` : "none",
              animation: tick > 0.4 ? "wpBlip 1.2s ease-in-out infinite" : "none",
            }}
          />
          {tick < 0.2 ? "Connecting…" : tick < 0.4 ? "Verifying…" : "Connected · main"}
        </div>
        {tick > 0.4 && (
          <div
            style={{
              position: "absolute",
              left: 0, right: 0, bottom: 0,
              height: 3,
              background: C.green,
            }}
          />
        )}
      </div>
    </div>
    <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 24, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16, position: "relative" }}>
      {tick > 0.5 && (
        <>
          <div style={{ position: "absolute", inset: 0, borderRadius: 10, background: `radial-gradient(400px 220px at 50% 50%, ${C.greenSoft}, transparent 70%)` }} />
          <div style={{ zIndex: 2, width: 72, height: 72, borderRadius: "50%", background: C.greenSoft, border: `2px solid ${C.green}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 32, color: C.green }}>✓</div>
          <div style={{ zIndex: 2, fontFamily: C.mono, fontSize: 18, fontWeight: 700, color: C.text }}>Repository connected</div>
          <div style={{ zIndex: 2, fontSize: 13, color: C.dim, textAlign: "center" }}>
            <b style={{ color: C.text }}>your-org/frontend</b> is now wired to ORA.<br />
            Ask a question or type <code style={{ color: C.amber, background: C.amberSoft, padding: "1px 6px", borderRadius: 4, fontSize: 11 }}>/scan</code> to run a health scan.
          </div>
        </>
      )}
      {tick <= 0.5 && (
        <>
          <div style={{ width: 44, height: 44, borderRadius: "50%", border: `3px solid ${C.border2}`, borderTopColor: C.amber, animation: "wpSpin 1s linear infinite" }} />
          <div style={{ fontFamily: C.mono, fontSize: 13, color: C.dim, letterSpacing: "0.06em" }}>Probing GitHub · scoped access…</div>
        </>
      )}
    </div>
    <style>{`
      @keyframes wpSpin { to { transform: rotate(360deg); } }
      @keyframes wpBlip { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
    `}</style>
  </div>
);

// ─── STEP 5 — Slash command / chat ────────────────────────────
const StepChat = ({ tick }) => {
  const cmd = typed("/scan bug hunt", tick, 0.1, 0.55);
  const showSuggest = tick > 0.15 && tick < 0.55;
  return (
    <div style={{ height: "100%", display: "grid", gridTemplateColumns: "180px 1fr 220px", gap: 10, position: "relative" }}>
      {/* sidebar */}
      <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 }}>
        <div style={{ fontFamily: C.mono, fontSize: 10, color: C.amber, marginBottom: 4 }}>ORA</div>
        <div style={{ padding: 10, background: C.panel2, border: `1px solid ${C.green}`, borderRadius: 6, fontFamily: C.mono, fontSize: 11, color: C.text }}>
          your-org/frontend
          <div style={{ marginTop: 4, fontSize: 9, color: C.green, display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: C.green }} />
            Connected · main
          </div>
        </div>
      </div>

      {/* chat panel */}
      <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, display: "flex", flexDirection: "column", position: "relative" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, paddingBottom: 8, borderBottom: `1px solid ${C.border}` }}>
          <span style={{ fontFamily: C.mono, fontSize: 12, color: C.amber, fontWeight: 700 }}>Chat</span>
          <span style={{ fontFamily: C.mono, fontSize: 11, color: C.dim }}>Preview</span>
          <span style={{ fontFamily: C.mono, fontSize: 11, color: C.dim }}>Graph</span>
        </div>
        <div style={{ flex: 1, padding: 12, fontSize: 12, color: C.dim, fontFamily: C.mono }}>
          <div style={{ padding: 10, background: C.panel2, borderLeft: `3px solid ${C.amber}`, borderRadius: 6, marginBottom: 10, fontSize: 12, lineHeight: 1.55, color: C.text }}>
            Hi &mdash; I&apos;m ORA, your engineering co-pilot. Ask me to plan, fix, or scan. Type <b style={{ color: C.amber }}>/</b> for commands.
          </div>
        </div>
        {/* Slash suggestion popover */}
        {showSuggest && (
          <div style={{
            position: "absolute", left: 22, bottom: 62,
            width: 320, background: "#0a0e18", border: `1px solid ${C.amber}`, borderRadius: 10,
            boxShadow: "0 20px 40px rgba(0,0,0,0.6)",
            padding: 6, zIndex: 4,
          }}>
            <MonoLabel color={C.amber}>SLASH COMMANDS</MonoLabel>
            {["/scan bug hunt", "/scan security", "/plan feature", "/fix"].map((s, i) => (
              <div key={s} style={{
                padding: "8px 10px", borderRadius: 6, fontSize: 12, fontFamily: C.mono,
                color: i === 0 ? C.amber : C.dim,
                background: i === 0 ? C.amberSoft : "transparent",
                marginTop: i === 0 ? 6 : 2,
              }}>{s}</div>
            ))}
          </div>
        )}
        {/* composer */}
        <div style={{
          padding: "10px 12px",
          background: "#0a0e18",
          border: `1px solid ${tick > 0.55 ? C.amber : C.border}`,
          borderRadius: 10,
          fontFamily: C.mono, fontSize: 13, color: C.text,
          minHeight: 40,
        }}>
          {cmd || <span style={{ color: C.faint }}>Ask ORA to build, fix, or scan…</span>}
          <span style={{ opacity: tick > 0.1 && tick < 0.55 && Math.floor(tick * 24) % 2 ? 1 : 0 }}>│</span>
        </div>
      </div>

      {/* Ask Advisor mini */}
      <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <MonoLabel color={C.amber}>ASK ADVISOR</MonoLabel>
        <div style={{ fontSize: 11, color: C.dim }}>ORA copilot · <span style={{ color: C.green }}>online</span></div>
        <div style={{ marginTop: 4, padding: 8, background: C.panel2, borderRadius: 6, fontSize: 11, color: C.text, lineHeight: 1.5 }}>
          Morning brief · Council live · few-shot retrieval active
        </div>
        <div style={{ marginTop: 2, padding: 6, background: "rgba(239,68,68,0.05)", border: `1px solid rgba(239,68,68,0.3)`, borderRadius: 6, fontSize: 10, color: C.dim }}>
          ⚠ Diagnose failed run
        </div>
      </div>

      <Cursor
        x={tick < 0.1 ? 500 : tick < 0.55 ? 320 : 620}
        y={tick < 0.1 ? 380 : tick < 0.55 ? 480 : 480}
        label={tick > 0.6 ? "enter" : null}
      />
    </div>
  );
};

// ─── STEP 6 — LOOP mode running ───────────────────────────────
const StepLoop = ({ tick }) => {
  const phases = ["PLAN", "EXECUTE", "VERIFY", "SCAN", "SHIP"];
  // Which phase is active at this tick
  const activePhase = Math.min(phases.length - 1, Math.floor(tick * phases.length));
  const messages = [
    { role: "user", text: "/scan bug hunt", show: 0 },
    { role: "ora",  text: "Running bug hunt on your-org/frontend@main…", show: 0.05 },
    { role: "phase", phase: "PLAN",    text: "Mapping repo · 42 files scanned", show: 0.10 },
    { role: "phase", phase: "EXECUTE", text: "Running static + heuristic checks", show: 0.28 },
    { role: "phase", phase: "VERIFY",  text: "Cross-checking 3 findings", show: 0.48 },
    { role: "phase", phase: "SCAN",    text: "Found 2 real bugs, 1 false positive filtered", show: 0.68 },
    { role: "phase", phase: "SHIP",    text: "Preparing PR with fixes…", show: 0.85 },
  ];
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 10 }}>
      {/* LOOP bar */}
      <div style={{
        background: "#161616",
        border: `1px solid ${C.border2}`,
        borderRadius: 10,
        padding: "10px 16px",
        display: "flex",
        alignItems: "center",
        gap: 16,
        fontFamily: C.mono,
      }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.amber, letterSpacing: "0.14em" }}>LOOP</div>
        {phases.map((p, i) => (
          <React.Fragment key={p}>
            {i > 0 && <span style={{ color: i <= activePhase ? C.amber : C.border2, fontSize: 10 }}>—</span>}
            <div style={{
              display: "flex", alignItems: "center", gap: 6, fontSize: 11,
              color: i < activePhase ? C.green : i === activePhase ? C.amber : C.faint,
              letterSpacing: "0.08em",
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: "50%",
                background: i < activePhase ? C.green : i === activePhase ? C.amber : "transparent",
                border: `1px solid ${i < activePhase ? C.green : i === activePhase ? C.amber : C.border2}`,
                boxShadow: i === activePhase ? `0 0 8px ${C.amber}` : "none",
                animation: i === activePhase ? "wpBlip 1s ease-in-out infinite" : "none",
              }} />
              {p}
            </div>
          </React.Fragment>
        ))}
      </div>

      {/* Message stream */}
      <div style={{ flex: 1, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, overflow: "hidden", display: "flex", flexDirection: "column", gap: 8 }}>
        {messages.filter((m) => tick >= m.show).map((m, i) => (
          <div
            key={i}
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "72%",
              padding: "8px 12px",
              borderRadius: 10,
              fontSize: 12,
              fontFamily: m.role === "user" ? undefined : C.mono,
              color: m.role === "user" ? "#000" : C.text,
              background: m.role === "user" ? C.amber
                : m.role === "phase" ? "rgba(245,158,11,0.06)"
                : C.panel2,
              border: m.role === "phase" ? `1px solid ${C.amber}` : `1px solid ${C.border}`,
              opacity: 0,
              animation: "wpFadeIn 320ms ease-out forwards",
              display: "flex",
              gap: 8,
              alignItems: "center",
            }}
          >
            {m.role === "phase" && (
              <span style={{ fontSize: 9, letterSpacing: "0.16em", color: C.amber, fontWeight: 700 }}>
                {m.phase}
              </span>
            )}
            <span>{m.text}</span>
          </div>
        ))}
      </div>
      <style>{`
        @keyframes wpFadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes wpBlip { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
      `}</style>
    </div>
  );
};

// ─── STEP 7 — Ship: PR opened / celebration ───────────────────
const StepShip = ({ tick }) => (
  <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 12, position: "relative" }}>
    {/* Confetti burst */}
    {tick > 0.05 && (
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden" }}>
        {Array.from({ length: 32 }).map((_, i) => {
          const angle = (i / 32) * Math.PI * 2;
          const dist = 80 + (i % 5) * 24 + tick * 100;
          const cx = 50, cy = 40;
          const dx = Math.cos(angle) * dist;
          const dy = Math.sin(angle) * dist;
          const colors = [C.amber, C.green, "#ec4899", "#8b5cf6", "#60a5fa"];
          return (
            <span key={i} style={{
              position: "absolute",
              left: `calc(${cx}% + ${dx}px)`,
              top: `calc(${cy}% + ${dy}px)`,
              width: 6, height: 10,
              background: colors[i % colors.length],
              transform: `rotate(${i * 33}deg)`,
              opacity: Math.max(0, 1 - tick * 1.4),
              borderRadius: 2,
              transition: "opacity 200ms",
            }} />
          );
        })}
      </div>
    )}

    {/* Ship card */}
    <div style={{
      background: C.panel,
      border: `1px solid ${C.green}`,
      borderRadius: 12,
      padding: 22,
      display: "flex",
      alignItems: "center",
      gap: 16,
      boxShadow: `0 0 40px ${C.greenSoft}`,
    }}>
      <div style={{ width: 54, height: 54, borderRadius: "50%", background: C.greenSoft, border: `1.5px solid ${C.green}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 26, color: C.green }}>✓</div>
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: C.mono, fontSize: 16, fontWeight: 700, color: C.text, marginBottom: 3 }}>PR shipped · 2 bugs fixed</div>
        <div style={{ fontSize: 12, color: C.dim, fontFamily: C.mono }}>
          #42 · your-org/frontend · <span style={{ color: C.green }}>MERGED</span>
        </div>
      </div>
      <div style={{ padding: "6px 14px", background: C.greenSoft, border: `1px solid ${C.green}`, borderRadius: 999, fontSize: 11, color: C.green, fontFamily: C.mono, letterSpacing: "0.06em" }}>
        SHIPPED VIA CTO
      </div>
    </div>

    {/* Diff preview mock */}
    <div style={{ flex: 1, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, fontFamily: C.mono, fontSize: 11, lineHeight: 1.8, color: C.text, overflow: "hidden" }}>
      <div style={{ color: C.faint, marginBottom: 6 }}>src/api/user.ts</div>
      <div style={{ color: C.red, background: "rgba(239,68,68,0.06)", padding: "1px 8px" }}>- if (user.email == input.email) &#123;</div>
      <div style={{ color: C.green, background: "rgba(34,197,94,0.06)", padding: "1px 8px" }}>+ if (user.email === input.email.toLowerCase().trim()) &#123;</div>
      <div style={{ color: C.faint, marginTop: 12, marginBottom: 6 }}>src/utils/date.ts</div>
      <div style={{ color: C.red, background: "rgba(239,68,68,0.06)", padding: "1px 8px" }}>- return new Date(ts).toLocaleDateString();</div>
      <div style={{ color: C.green, background: "rgba(34,197,94,0.06)", padding: "1px 8px" }}>+ return new Date(ts).toLocaleDateString(&apos;en-US&apos;, &#123; timeZone: &apos;UTC&apos; &#125;);</div>
      <div style={{ marginTop: 14, padding: "8px 12px", background: C.amberSoft, border: `1px solid ${C.amber}`, borderRadius: 8, color: C.text, fontFamily: C.mono, fontSize: 12 }}>
        Vanguard reviewed · <b style={{ color: C.green }}>3/3 checks passed</b> · ready to merge
      </div>
    </div>
  </div>
);

// ─── Exported step arrays ─────────────────────────────────────

export const FULL_STEPS = [
  {
    id: "signup",
    caption: "Sign up in seconds — no credit card, 10 free tasks.",
    duration: 6000,
    urlPath: "/signup",
    render: StepSignup,
  },
  {
    id: "dashboard",
    caption: "Land on an empty dashboard — one clear next step.",
    duration: 5000,
    urlPath: "/dashboard",
    render: StepDashboard,
  },
  {
    id: "connect",
    caption: "One click → GitHub OAuth authorize screen → back in.  No PATs, no tokens to paste.",
    duration: 9000,
    urlPath: "/dashboard",
    render: StepConnect,
  },
  {
    id: "connected",
    caption: "Green dot means ORA has secure repo context now.",
    duration: 5500,
    urlPath: "/dashboard",
    render: StepConnected,
  },
  {
    id: "chat",
    caption: "Chat naturally — or use `/` for scan, plan, fix commands.",
    duration: 6500,
    urlPath: "/dashboard",
    render: StepChat,
  },
  {
    id: "loop",
    caption: "LOOP mode drives PLAN → EXECUTE → VERIFY → SCAN → SHIP autonomously.",
    duration: 9500,
    urlPath: "/dashboard",
    render: StepLoop,
  },
  {
    id: "ship",
    caption: "PR shipped — Vanguard-reviewed, ready to merge. That's a full loop.",
    duration: 7000,
    urlPath: "/dashboard",
    render: StepShip,
  },
];

// Teaser cut — 4 highlight moments only, faster pacing (~24s total).
export const TEASER_STEPS = [
  { ...FULL_STEPS[0], duration: 4500,  caption: "Sign up · 10 free tasks." },
  { ...FULL_STEPS[2], duration: 6500,  caption: "Connect GitHub — one OAuth click." },
  { ...FULL_STEPS[5], duration: 8000,  caption: "LOOP mode ships production-ready code." },
  { ...FULL_STEPS[6], duration: 6000,  caption: "Merged in minutes. No hand-holding." },
];
