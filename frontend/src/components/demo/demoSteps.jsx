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

// ─── STEP 1 — Signup (OAuth-first + email form) ────────────────
// Mirrors the real /signup page:  Google + GitHub OAuth buttons on
// top (primary flow), then an "OR EMAIL" divider with the classic
// name / email / password form as a fallback.  Cursor drifts to
// "Continue with GitHub" — that's the fastest path a new developer
// picks.
const StepSignup = ({ tick }) => {
  return (
    <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", position: "relative" }}>
      <div
        style={{
          width: 440,
          padding: 28,
          background: C.panel,
          border: `1px solid ${C.border}`,
          borderRadius: 14,
          boxShadow: `0 0 0 1px ${C.amberSoft} inset, 0 20px 60px rgba(0,0,0,0.5)`,
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 16 }}>
          <div style={{ fontFamily: C.mono, fontSize: 10, color: C.amber, letterSpacing: "0.18em", marginBottom: 8 }}>SIGN UP</div>
          <div style={{ fontFamily: "Georgia, serif", fontSize: 22, fontWeight: 700, color: C.text, letterSpacing: "-0.5px" }}>
            Create your developer account
          </div>
          <div style={{ fontSize: 12, color: C.dim, marginTop: 6 }}>1,000 tokens free. No card required.</div>
        </div>

        {/* ORA GUIDE card */}
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 10,
          padding: "10px 12px",
          background: C.amberSoft, border: `1px solid ${C.amber}`, borderRadius: 8,
          marginBottom: 14,
        }}>
          <div style={{ width: 26, height: 26, borderRadius: 6, background: C.amber, color: "#000", fontWeight: 800, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14 }}>ᴥ</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: C.mono, fontSize: 9, color: C.amber, letterSpacing: "0.16em", marginBottom: 2 }}>ORA GUIDE</div>
            <div style={{ fontSize: 11, color: C.text, lineHeight: 1.45 }}>
              Fastest way: <b>One Click Continue</b> ↓ — creates your account instantly.
            </div>
          </div>
        </div>

        {/* Google button */}
        <button
          style={{
            width: "100%", padding: "10px", marginBottom: 8,
            background: "#fff", color: "#000",
            border: "1px solid #d0d7de", borderRadius: 8,
            fontSize: 13, fontWeight: 600,
            display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 10,
          }}
        >
          <svg width="16" height="16" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.31-8.16 2.31-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
          Continue with Google
        </button>

        {/* GitHub button */}
        <button
          data-testid="demo-signup-github-btn"
          style={{
            width: "100%", padding: "10px", marginBottom: 12,
            background: "#0d1117", color: "#fff",
            border: `1px solid ${C.border2}`, borderRadius: 8,
            fontSize: 13, fontWeight: 600,
            display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 10,
            transform: tick > 0.7 ? "scale(0.98)" : "scale(1)",
            transition: "transform 100ms, background 200ms",
            outline: tick > 0.5 && tick < 0.85 ? `2px solid ${C.amber}` : "none",
            outlineOffset: 2,
          }}
        >
          <svg width="15" height="15" viewBox="0 0 16 16"><path fill="#fff" d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
          Continue with GitHub
        </button>

        {/* Divider */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "8px 0 12px" }}>
          <div style={{ flex: 1, height: 1, background: C.border2 }} />
          <div style={{ fontSize: 10, color: C.faint, fontFamily: C.mono, letterSpacing: "0.16em" }}>OR EMAIL</div>
          <div style={{ flex: 1, height: 1, background: C.border2 }} />
        </div>

        {/* Compact email + password stubs */}
        <div style={{ padding: "9px 12px", background: "#0a0e18", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 12, color: C.faint, fontFamily: C.mono, marginBottom: 8 }}>
          you@company.com
        </div>
        <div style={{ padding: "9px 12px", background: "#0a0e18", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 12, color: C.faint, fontFamily: C.mono }}>
          password (min 6)
        </div>
      </div>
      <Cursor
        x={tick < 0.3 ? 300 : tick < 0.65 ? 600 : 720}
        y={tick < 0.3 ? 200 : tick < 0.65 ? 380 : 440}
        label={tick > 0.75 ? "click" : null}
      />
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

// ─── STEP 3 — Add Repo Wizard (matches production) ─────────────
// Mirrors the real `new-user-wizard` modal that opens when a user
// clicks "Add Repository" in the sidebar.  Real product ALREADY has
// the user's GitHub connected via OAuth (`Continue with GitHub` on
// signup), so this modal loads their repo list from GitHub AND asks
// for a PAT with write access (contents: read & write) so ORA can
// push commits back.  Structure captured verbatim from prod:
//   • Header: "ORA · by Aurem CTO"  · "Step 1 of 3"
//   • Progress: 3 dots, first amber
//   • Title: "Connect your GitHub repo"
//   • Green pill: "🔗 Connected as your-github"
//   • ORA GUIDE amber card explaining the picker
//   • Repo dropdown ("3 repos found — pick one")
//   • Repository URL input (fallback)
//   • Branch input (default: main)
//   • GitHub PAT input + "Generate PAT →" amber button
//   • Footer: "Skip for now" · "Continue →"
const StepConnect = ({ tick }) => {
  // Cursor drifts from repo dropdown → PAT input → Continue.
  const cursorAt =
    tick < 0.28  ? { x: 420, y: 340 }    // repo dropdown
    : tick < 0.6 ? { x: 520, y: 500 }    // PAT input
    : tick < 0.88 ? { x: 720, y: 500 }   // Generate PAT button
    :              { x: 780, y: 620 };   // Continue
  const patFilled = tick > 0.65;

  return (
    <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", position: "relative" }}>
      <div
        data-testid="demo-add-repo-wizard"
        style={{
          width: 500,
          background: C.panel,
          border: `1px solid ${C.amber}`,
          borderRadius: 14,
          padding: 22,
          boxShadow: `0 30px 80px rgba(0,0,0,0.6), 0 0 0 1px ${C.amberSoft} inset`,
        }}
      >
        {/* Header row */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <div style={{ width: 28, height: 28, borderRadius: 6, background: C.amber, color: "#000", fontWeight: 800, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13 }}>ᴥ</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: C.mono, fontSize: 12, fontWeight: 700, color: C.amber }}>
              ORA <span style={{ color: C.faint, fontWeight: 400, fontSize: 10 }}>by Aurem CTO</span>
            </div>
          </div>
          <div style={{ fontFamily: C.mono, fontSize: 10, color: C.dim, letterSpacing: "0.08em" }}>Step 1 of 3</div>
          <div style={{ fontSize: 14, color: C.faint, cursor: "pointer" }}>×</div>
        </div>

        {/* Progress dots */}
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 14 }}>
          <div style={{ width: 24, height: 4, borderRadius: 2, background: C.amber }} />
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.border2 }} />
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.border2 }} />
          <div style={{ marginLeft: 8, fontFamily: C.mono, fontSize: 10, color: C.faint, letterSpacing: "0.08em" }}>Connect repo</div>
        </div>

        {/* ORA GUIDE card */}
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 10,
          padding: "9px 11px",
          background: C.amberSoft, border: `1px solid ${C.amber}`, borderRadius: 8,
          marginBottom: 12,
        }}>
          <div style={{ width: 22, height: 22, borderRadius: 5, background: C.amber, color: "#000", fontWeight: 800, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12 }}>ᴥ</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: C.mono, fontSize: 9, color: C.amber, letterSpacing: "0.16em", marginBottom: 1 }}>ORA GUIDE</div>
            <div style={{ fontSize: 11, color: C.text, lineHeight: 1.45 }}>
              Your GitHub repos are loaded! <b>Pick a repo</b> from the dropdown — or paste a URL. 👇
            </div>
          </div>
        </div>

        {/* Title + connected pill */}
        <div style={{ fontFamily: "Georgia, serif", fontSize: 17, fontWeight: 700, color: C.text, marginBottom: 8 }}>
          Connect your GitHub repo
        </div>
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "5px 10px",
          background: C.greenSoft, border: `1px solid ${C.green}`, borderRadius: 8,
          fontSize: 11, fontFamily: C.mono, color: C.green,
          marginBottom: 10,
        }}>
          <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
          Connected as <b>your-github</b>
        </div>
        <div style={{ fontSize: 11, color: C.dim, lineHeight: 1.5, marginBottom: 12 }}>
          Pick a repo from your account or paste any URL — ORA will read it, write the diff, and push the commit back.
        </div>

        {/* YOUR REPOSITORIES dropdown */}
        <MonoLabel>YOUR REPOSITORIES</MonoLabel>
        <div
          data-testid="demo-repo-dropdown"
          style={{
            marginTop: 5, marginBottom: 10,
            padding: "8px 11px", background: "#0a0e18",
            border: `1px solid ${tick < 0.28 ? C.amber : C.border}`,
            borderRadius: 8, fontSize: 12, color: tick < 0.28 ? C.amber : C.text,
            fontFamily: C.mono, display: "flex", justifyContent: "space-between", alignItems: "center",
            transition: "border-color 200ms, color 200ms",
          }}
        >
          {tick < 0.28 ? "3 repos found — pick one" : "your-org/frontend"}
          <span style={{ color: C.faint }}>▾</span>
        </div>

        {/* BRANCH */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
          <div>
            <MonoLabel>BRANCH</MonoLabel>
            <div style={{ marginTop: 5, padding: "8px 11px", background: "#0a0e18", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 12, color: C.text, fontFamily: C.mono }}>
              main
            </div>
          </div>
          <div>
            <MonoLabel>REPO URL</MonoLabel>
            <div style={{ marginTop: 5, padding: "8px 11px", background: "#0a0e18", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 11, color: C.faint, fontFamily: C.mono }}>
              github.com/…
            </div>
          </div>
        </div>

        {/* PAT + Generate button */}
        <div style={{ fontFamily: C.mono, fontSize: 9, color: C.faint, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 4 }}>
          GITHUB PERSONAL ACCESS TOKEN <span style={{ color: C.amber }}>(required · contents: read &amp; write)</span>
        </div>
        <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
          <div
            style={{
              flex: 1,
              padding: "8px 11px",
              background: "#0a0e18",
              border: `1px solid ${tick >= 0.28 && tick < 0.65 ? C.amber : C.border}`,
              borderRadius: 8,
              fontSize: 12,
              color: patFilled ? C.text : C.faint,
              fontFamily: C.mono,
              letterSpacing: patFilled ? "0.1em" : 0,
              transition: "border-color 200ms",
            }}
          >
            {patFilled ? typed("ghp_••••••••••••••••••••••", tick, 0.65, 0.85) : "ghp_… or github_pat_…"}
          </div>
          <button
            data-testid="demo-generate-pat-btn"
            style={{
              padding: "8px 12px",
              background: tick >= 0.65 && tick < 0.88 ? "#e08e07" : C.amber,
              color: "#000",
              border: "none", borderRadius: 8,
              fontFamily: C.mono, fontSize: 11, fontWeight: 700, letterSpacing: "0.04em",
              cursor: "pointer",
              boxShadow: tick > 0.7 && tick < 0.9 ? `0 0 16px ${C.amberSoft}` : "none",
              transform: tick > 0.82 && tick < 0.88 ? "scale(0.97)" : "scale(1)",
              transition: "background 200ms, transform 100ms",
            }}
          >
            Generate PAT →
          </button>
        </div>
        <div style={{ fontSize: 10, color: C.faint, marginBottom: 14 }}>
          Encrypted at rest · only used to read &amp; push this repo
        </div>

        {/* Footer */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ fontSize: 11, color: C.faint, fontFamily: C.mono, cursor: "pointer" }}>Skip for now</div>
          <button
            data-testid="demo-wizard-continue-btn"
            style={{
              padding: "8px 20px",
              background: patFilled ? C.amber : "#3a2b0f",
              color: patFilled ? "#000" : C.faint,
              border: "none", borderRadius: 8,
              fontFamily: C.mono, fontSize: 12, fontWeight: 700, letterSpacing: "0.04em",
              display: "inline-flex", alignItems: "center", gap: 6,
              transform: tick > 0.93 ? "scale(0.98)" : "scale(1)",
              transition: "background 200ms, color 200ms, transform 100ms",
            }}
          >
            {tick > 0.93 ? "CONNECTING…" : "Continue →"}
          </button>
        </div>
      </div>
      <Cursor x={cursorAt.x} y={cursorAt.y} label={(tick > 0.85 && tick < 0.9) || tick > 0.94 ? "click" : null} />
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
// Iter 212m-231 — Each step now carries an `audioSrc` pointing to a
// generated MP3 in /public/demo-audio/.  Run `python backend/scripts/
// generate_demo_audio.py` (with EMERGENT_LLM_KEY set) to regenerate
// the files.  Missing files degrade gracefully — visuals play on
// without audio.

export const FULL_STEPS = [
  {
    id: "signup",
    caption: "Sign up in seconds — no credit card, 10 free tasks.",
    duration: 6000,
    urlPath: "/signup",
    audioSrc: "/demo-audio/step-1.mp3",
    render: StepSignup,
  },
  {
    id: "dashboard",
    caption: "Land on an empty dashboard — one clear next step.",
    duration: 5000,
    urlPath: "/dashboard",
    audioSrc: "/demo-audio/step-2.mp3",
    render: StepDashboard,
  },
  {
    id: "connect",
    caption: "Pick a repo · paste a PAT with contents:read&write · Continue. That's it.",
    duration: 9000,
    urlPath: "/dashboard",
    audioSrc: "/demo-audio/step-3.mp3",
    render: StepConnect,
  },
  {
    id: "connected",
    caption: "Green dot means ORA has secure repo context now.",
    duration: 5500,
    urlPath: "/dashboard",
    audioSrc: "/demo-audio/step-4.mp3",
    render: StepConnected,
  },
  {
    id: "chat",
    caption: "Chat naturally — or use `/` for scan, plan, fix commands.",
    duration: 6500,
    urlPath: "/dashboard",
    audioSrc: "/demo-audio/step-5.mp3",
    render: StepChat,
  },
  {
    id: "loop",
    caption: "LOOP mode drives PLAN → EXECUTE → VERIFY → SCAN → SHIP autonomously.",
    duration: 9500,
    urlPath: "/dashboard",
    audioSrc: "/demo-audio/step-6.mp3",
    render: StepLoop,
  },
  {
    id: "ship",
    caption: "PR shipped — Vanguard-reviewed, ready to merge. That's a full loop.",
    duration: 7000,
    urlPath: "/dashboard",
    audioSrc: "/demo-audio/step-7.mp3",
    render: StepShip,
  },
];

// Teaser cut — 4 highlight moments only, faster pacing (~24s total).
// Re-uses the same MP3 files where possible so we don't need extra
// TTS generation for the compact landing embed.
export const TEASER_STEPS = [
  { ...FULL_STEPS[0], duration: 4500,  caption: "Sign up · 10 free tasks." },
  { ...FULL_STEPS[2], duration: 6500,  caption: "Add repo — pick from your GitHub, drop a PAT, Continue." },
  { ...FULL_STEPS[5], duration: 8000,  caption: "LOOP mode ships production-ready code." },
  { ...FULL_STEPS[6], duration: 6000,  caption: "Merged in minutes. No hand-holding." },
];
