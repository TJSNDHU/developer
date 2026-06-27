/**
 * components/WarmStatusBar.jsx — Iter 212m-59 rewrite
 *
 * Previously rendered "Loading your project… X%" with a thin amber
 * progress strip — the "stuck at 80%" UX that triggered Bolt-style
 * "is it broken?" anxiety.  Now renders a polished skeleton: three
 * grey chat-bubble placeholders that shimmer (opacity 0.4 → 0.78
 * → 0.4 over 1.5s) during the ~2-6s warm-start window.
 *
 * Returns null when status is "idle" or "ready" so users never see
 * the skeleton outside the brief warm-up.  Stays inline-friendly
 * (no fixed height; sits where the old strip lived) so the rest of
 * the chat layout is unchanged.
 */
import React from "react";

export default function WarmStatusBar({ status }) {
  if (status === "idle" || status === "ready") return null;

  return (
    <div
      data-testid="warm-status-bar"
      data-warm-status={status}
      role="status"
      aria-label="Loading your project"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "16px 18px 18px",
        borderBottom: "1px solid var(--border, rgba(255,255,255,0.06))",
        background: "transparent",
      }}
    >
      {/* Three "fake" chat bubbles — alternating user-right /
          assistant-left to match the real chat below. */}
      <SkeletonBubble side="left"  width="62%" lines={2} delay={0} />
      <SkeletonBubble side="right" width="42%" lines={1} delay={180} />
      <SkeletonBubble side="left"  width="78%" lines={3} delay={360} />
    </div>
  );
}

function SkeletonBubble({ side, width, lines, delay }) {
  const isLeft = side === "left";
  return (
    <div
      data-testid={`skeleton-bubble-${side}`}
      style={{
        alignSelf: isLeft ? "flex-start" : "flex-end",
        width,
        maxWidth: "min(620px, 80%)",
        padding: "11px 14px",
        borderRadius: 14,
        borderTopLeftRadius:  isLeft ? 4 : 14,
        borderTopRightRadius: isLeft ? 14 : 4,
        background: isLeft
          ? "rgba(255,255,255,0.04)"
          : "rgba(232,160,32,0.08)",
        border: `1px solid ${isLeft
          ? "rgba(255,255,255,0.06)"
          : "rgba(232,160,32,0.18)"}`,
        display: "flex", flexDirection: "column", gap: 7,
        animation: `ora-skeleton-shimmer 1.5s ease-in-out ${delay}ms infinite`,
      }}
    >
      {Array.from({ length: lines }).map((_, i) => (
        <span
          key={i}
          aria-hidden="true"
          style={{
            height: 9,
            width: i === lines - 1 ? "55%" : "100%",
            borderRadius: 4,
            background: isLeft
              ? "rgba(255,255,255,0.10)"
              : "rgba(232,160,32,0.22)",
          }}
        />
      ))}
    </div>
  );
}
