/**
 * pages/Demo.jsx — Iter 212m-200
 *
 * Public /demo route.  Renders the animated walkthrough player with
 * either the full E2E arc (default) or the ~25 second teaser cut
 * intended for ads (`?mode=teaser`).  No auth required.
 *
 * All content in the player is fabricated mock UI — no real PATs
 * or production repo details render at any point.
 */
import React, { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import WalkthroughPlayer from "../components/demo/WalkthroughPlayer";
import { FULL_STEPS, TEASER_STEPS } from "../components/demo/demoSteps";

export default function Demo() {
  const [sp] = useSearchParams();
  const mode = sp.get("mode") === "teaser" ? "teaser" : "full";
  const steps = mode === "teaser" ? TEASER_STEPS : FULL_STEPS;

  const totalSec = useMemo(
    () => Math.round(steps.reduce((s, x) => s + (x.duration || 0), 0) / 1000),
    [steps]
  );

  return (
    <div
      data-testid={`demo-page-${mode}`}
      style={{
        minHeight: "100vh",
        background:
          "radial-gradient(900px 540px at 18% -8%, rgba(245,158,11,0.20), transparent 70%)," +
          "radial-gradient(820px 480px at 86% 6%, rgba(99,102,241,0.14), transparent 65%)," +
          "linear-gradient(180deg, rgba(10,14,26,0.78) 0%, rgba(5,8,17,0.92) 100%)," +
          "#050811",
        color: "#e5e7eb",
        fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif',
        padding: "48px 24px 96px",
      }}
    >
      {/* Simple top nav */}
      <div style={{ maxWidth: 1200, margin: "0 auto 32px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Link
          to="/"
          data-testid="demo-back-home"
          style={{
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: 12,
            color: "#94a3b8",
            textDecoration: "none",
            letterSpacing: "0.06em",
          }}
        >
          ← Home
        </Link>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <Link
            to={mode === "teaser" ? "/demo" : "/demo?mode=teaser"}
            data-testid="demo-mode-switch"
            style={{
              padding: "6px 12px",
              fontFamily: '"JetBrains Mono", monospace',
              fontSize: 11,
              letterSpacing: "0.06em",
              color: "#f59e0b",
              border: "1px solid rgba(245,158,11,0.4)",
              borderRadius: 999,
              textDecoration: "none",
            }}
          >
            {mode === "teaser" ? "▶ FULL WALKTHROUGH" : "⚡ 25s TEASER"}
          </Link>
          <Link
            to="/signup"
            data-testid="demo-cta-signup"
            style={{
              padding: "8px 16px",
              fontFamily: '"JetBrains Mono", monospace',
              fontSize: 12,
              background: "#f59e0b",
              color: "#000",
              borderRadius: 8,
              textDecoration: "none",
              fontWeight: 700,
              letterSpacing: "0.04em",
            }}
          >
            START FREE →
          </Link>
        </div>
      </div>

      {/* Heading */}
      <div style={{ maxWidth: 1080, margin: "0 auto 24px", textAlign: "center" }}>
        <div
          style={{
            display: "inline-block",
            padding: "6px 12px",
            background: "rgba(245,158,11,0.08)",
            border: "1px solid rgba(245,158,11,0.3)",
            borderRadius: 999,
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: 10,
            letterSpacing: "0.18em",
            color: "#f59e0b",
            marginBottom: 16,
          }}
        >
          {mode === "teaser" ? "TEASER · ~25 SECONDS" : `FULL WALKTHROUGH · ~${totalSec}s`}
        </div>
        <h1
          style={{
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: "clamp(24px, 3.4vw, 40px)",
            fontWeight: 700,
            letterSpacing: "-1px",
            margin: "0 0 12px",
            color: "#f8fafc",
          }}
        >
          {mode === "teaser"
            ? "See ORA ship code in 25 seconds."
            : "From signup to shipped PR — see the whole loop."}
        </h1>
        <p style={{ margin: 0, color: "#94a3b8", fontSize: "clamp(13px, 1.15vw, 16px)", maxWidth: 640, marginLeft: "auto", marginRight: "auto", lineHeight: 1.6 }}>
          {mode === "teaser"
            ? "Sign up · connect repo · LOOP mode · merged PR. No hand-holding."
            : "Watch how a new founder goes from empty dashboard to a merged pull request. Every screen below is a faithful mock of the real product."}
        </p>
      </div>

      {/* Player */}
      <WalkthroughPlayer steps={steps} mode={mode} loop />

      {/* CTA row */}
      <div style={{ maxWidth: 1080, margin: "48px auto 0", textAlign: "center" }}>
        <div style={{ fontSize: 13, color: "#94a3b8", marginBottom: 18 }}>
          Ready to run your own scan?
        </div>
        <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          <Link
            to="/signup"
            data-testid="demo-cta-signup-bottom"
            style={{
              padding: "14px 28px",
              fontFamily: '"JetBrains Mono", monospace',
              fontSize: 13,
              background: "#f59e0b",
              color: "#000",
              borderRadius: 10,
              textDecoration: "none",
              fontWeight: 700,
              letterSpacing: "0.04em",
            }}
          >
            START FREE — 10 TASKS
          </Link>
          <Link
            to="/pricing"
            data-testid="demo-cta-pricing"
            style={{
              padding: "14px 28px",
              fontFamily: '"JetBrains Mono", monospace',
              fontSize: 13,
              background: "transparent",
              color: "#94a3b8",
              border: "1px solid #334155",
              borderRadius: 10,
              textDecoration: "none",
              fontWeight: 600,
              letterSpacing: "0.04em",
            }}
          >
            SEE PRICING
          </Link>
        </div>
      </div>
    </div>
  );
}
