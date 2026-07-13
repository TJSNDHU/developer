/**
 * components/tour/ConnectRepoTour.jsx — Iter 212m-200
 *
 * Interactive guided tour that runs on the REAL dashboard for signed-in
 * users who have not yet connected a repo.  It is a lightweight, custom
 * spotlight implementation (no library dependencies): we render an SVG
 * mask that dims everything except the current target element, and a
 * tooltip card next to it.
 *
 * Triggers
 *   • FinishSetupBanner button click.
 *   • URL param `?tour=connect-repo` on /dashboard (email deep link).
 *
 * Steps
 *   1. Highlight the sidebar "Add Repository" (or Repositories list).
 *   2. Highlight the repo-help modal / GitHub token field once opened.
 *   3. Highlight the chat composer (once repo is connected).
 *
 * Props
 *   onClose    () => void   Fired when user finishes/skips the tour.
 *
 * The tour is purely UX guidance — it never types or clicks for the
 * user.  It only spotlights + explains.
 */
import React, { useEffect, useState, useMemo, useCallback } from "react";

const STEPS = [
  {
    id: "sidebar-add",
    selectors: [
      '[data-testid="ds2-add-repo"]',
      '[data-testid="ds2-sidebar-repos"]',
      '[data-testid="add-project-button"]',
    ],
    fallback: { x: 32, y: 240, width: 168, height: 40 },
    title: "Step 1 · Add a repository",
    body: "Click here to open the repo wizard. You'll paste a GitHub PAT + owner/repo.  We only ever store an encrypted, scoped token.",
    cta: "Got it — next",
  },
  {
    id: "chat-toolbar-gh",
    selectors: [
      '[data-testid="chat-github-status"]',
      '[data-testid="chat-form"]',
    ],
    fallback: { x: 400, y: 400, width: 300, height: 120 },
    title: "Step 2 · Chat with repo context",
    body: "Once the green dot lights up here, ORA has your repo context.  Type a request in plain English or use `/` for slash commands like `/scan bug hunt`.",
    cta: "Next",
  },
  {
    id: "loop-toggle",
    selectors: [
      '[data-testid="loop-step-bar"]',
      '[data-testid="loop-mode-toggle"]',
    ],
    fallback: { x: 400, y: 300, width: 400, height: 44 },
    title: "Step 3 · Turn on LOOP mode",
    body: "LOOP autonomously runs PLAN → EXECUTE → VERIFY → SCAN → SHIP for you.  Perfect for `/scan bug hunt` and full-feature builds.",
    cta: "Finish tour",
  },
];

function findTarget(step) {
  if (!step) return null;
  for (const sel of step.selectors) {
    const el = document.querySelector(sel);
    if (el) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) return rect;
    }
  }
  return step.fallback;
}

export default function ConnectRepoTour({ onClose }) {
  const [i, setI] = useState(0);
  const [rect, setRect] = useState(null);
  const step = STEPS[i];

  const recompute = useCallback(() => {
    setRect(findTarget(step));
  }, [step]);

  useEffect(() => {
    recompute();
    const onResize = () => recompute();
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    // Re-probe every 400ms in case the DOM shifts (chat streams).
    const id = setInterval(recompute, 400);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
      clearInterval(id);
    };
  }, [recompute]);

  const spotlight = useMemo(() => {
    if (!rect) return { x: 0, y: 0, w: 0, h: 0, r: 12 };
    const pad = 10;
    return {
      x: Math.max(0, rect.left - pad),
      y: Math.max(0, rect.top - pad),
      w: rect.width + pad * 2,
      h: rect.height + pad * 2,
      r: 12,
    };
  }, [rect]);

  // Position tooltip
  const tip = useMemo(() => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const tipW = 340;
    const tipH = 180;
    let x = spotlight.x + spotlight.w + 20;
    let y = spotlight.y + spotlight.h / 2 - tipH / 2;
    if (x + tipW > vw - 20) x = Math.max(20, spotlight.x - tipW - 20);
    if (y < 20) y = 20;
    if (y + tipH > vh - 20) y = Math.max(20, vh - tipH - 20);
    return { x, y, w: tipW };
  }, [spotlight]);

  if (!rect) return null;

  const next = () => {
    if (i + 1 >= STEPS.length) onClose?.();
    else setI(i + 1);
  };
  const skip = () => onClose?.();

  return (
    <div
      data-testid="connect-repo-tour"
      style={{ position: "fixed", inset: 0, zIndex: 10000, pointerEvents: "none" }}
    >
      {/* Dim overlay with SVG mask cutout */}
      <svg
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "auto" }}
        onClick={skip}
      >
        <defs>
          <mask id="tour-mask">
            <rect width="100%" height="100%" fill="white" />
            <rect
              x={spotlight.x}
              y={spotlight.y}
              width={spotlight.w}
              height={spotlight.h}
              rx={spotlight.r}
              ry={spotlight.r}
              fill="black"
              style={{ transition: "all 240ms ease" }}
            />
          </mask>
        </defs>
        <rect
          width="100%"
          height="100%"
          fill="rgba(4,7,16,0.72)"
          mask="url(#tour-mask)"
        />
        {/* Spotlight ring */}
        <rect
          x={spotlight.x}
          y={spotlight.y}
          width={spotlight.w}
          height={spotlight.h}
          rx={spotlight.r}
          ry={spotlight.r}
          fill="none"
          stroke="#f59e0b"
          strokeWidth="2"
          style={{ transition: "all 240ms ease", filter: "drop-shadow(0 0 12px rgba(245,158,11,0.6))" }}
        />
      </svg>

      {/* Tooltip */}
      <div
        data-testid={`tour-tooltip-${step.id}`}
        style={{
          position: "absolute",
          left: tip.x,
          top: tip.y,
          width: tip.w,
          pointerEvents: "auto",
          background: "linear-gradient(180deg, rgba(20,24,34,0.98), rgba(13,16,24,0.98))",
          border: "1px solid rgba(245,158,11,0.4)",
          borderRadius: 12,
          padding: "16px 18px",
          color: "#e5e7eb",
          boxShadow: "0 20px 60px rgba(0,0,0,0.6)",
          fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif',
        }}
      >
        <div style={{
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: 10, letterSpacing: "0.18em", color: "#f59e0b",
          marginBottom: 6,
        }}>
          STEP {i + 1} OF {STEPS.length}
        </div>
        <div style={{ fontSize: 15, fontWeight: 700, color: "#f8fafc", marginBottom: 8 }}>
          {step.title}
        </div>
        <div style={{ fontSize: 12, lineHeight: 1.55, color: "#94a3b8", marginBottom: 14 }}>
          {step.body}
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            type="button"
            data-testid="tour-skip"
            onClick={skip}
            style={{
              background: "transparent",
              border: "1px solid #334155",
              color: "#94a3b8",
              padding: "7px 12px",
              borderRadius: 7,
              fontSize: 11,
              fontFamily: '"JetBrains Mono", monospace',
              letterSpacing: "0.05em",
              cursor: "pointer",
            }}
          >
            SKIP
          </button>
          <button
            type="button"
            data-testid="tour-next"
            onClick={next}
            style={{
              background: "#f59e0b",
              border: "none",
              color: "#000",
              padding: "7px 14px",
              borderRadius: 7,
              fontSize: 11,
              fontFamily: '"JetBrains Mono", monospace',
              fontWeight: 700,
              letterSpacing: "0.05em",
              cursor: "pointer",
            }}
          >
            {step.cta}
          </button>
        </div>
      </div>
    </div>
  );
}
