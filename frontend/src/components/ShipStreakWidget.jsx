/**
 * ShipStreakWidget.jsx — Iter 212m-89
 *
 * Mini dashboard chip that shows weekly ship count + auto-fires
 * celebratory toasts at milestone crossings (10 / 25 / 50 / 100) with
 * one-tap LinkedIn + Twitter share buttons.
 *
 * Data source: `GET /wrapped/me?period=this_week` (already exists since
 * iter 145 — returns `stats.tasks_shipped` for the rolling week).
 *
 * Refresh triggers:
 *   • initial mount
 *   • `aurem:shipped` custom event (fired by ChatPanel after a
 *     successful ship-via-CTO task — see iter 212m-10)
 *   • 60 s background poll (cheap, single doc)
 *
 * Milestone de-dupe: localStorage key `aurem_streak_toast_{N}`. So a
 * user crossing 10 ships gets one toast, then 25, then 50, etc., even
 * after page reloads.
 *
 * Mounted once at Dashboard top — visually a small pill next to the
 * v2 TopBar "New run" button.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Flame } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";

const MILESTONES = [10, 25, 50, 100, 250];
const POLL_MS = 60_000;

function shareText(n) {
  return `🚀 I shipped ${n} commits this week via @AUREMcto — AI CTO that ` +
         `writes, reviews & ships code with Vanguard scans on every push.\n\n` +
         `Build smarter. Ship faster. https://auremcto.com`;
}

function openLinkedIn(n) {
  const url = "https://auremcto.com";
  const text = encodeURIComponent(shareText(n));
  window.open(
    `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}&summary=${text}`,
    "_blank", "noopener,noreferrer,width=600,height=600",
  );
}
function openTwitter(n) {
  window.open(
    `https://twitter.com/intent/tweet?text=${encodeURIComponent(shareText(n))}`,
    "_blank", "noopener,noreferrer,width=600,height=600",
  );
}

export default function ShipStreakWidget() {
  const [count, setCount] = useState(null);

  const fire = useCallback(async () => {
    try {
      const r = await api.get("/wrapped/me?period=this_week");
      const shipped = r?.data?.stats?.tasks_shipped ?? 0;
      setCount(shipped);
      // Milestone hit? Toast once.
      // Iter 212m-202 — pick the HIGHEST milestone the user has crossed
      // but not yet acknowledged, not the lowest. Prior behaviour
      // showed "10 ships this week" to a user who had already shipped
      // 81 (confusing — that number contradicts the widget next to it,
      // and the celebration under-sells their real progress).
      const hit = [...MILESTONES]
        .filter((m) => shipped >= m && !localStorage.getItem(`aurem_streak_toast_${m}`))
        .pop();
      if (!hit) return;
      // Auto-mark every lower milestone as seen too, so we don't
      // backfill a queue of small toasts on the next call.
      try {
        for (const m of MILESTONES) {
          if (m <= hit) localStorage.setItem(`aurem_streak_toast_${m}`, "1");
        }
      } catch { /* private mode */ }
      toast({
        kind: "success",
        duration: 11_000,
        message: `🔥 You just crossed ${hit} ships this week — tap to share`,
        onClick: () => openTwitter(hit),
        position: "bottom-right",   // iter 212m-221 — avoid Graph tab codebase-panel header overlap
      });
      // Also fire a custom event so other UI pieces (analytics, audit
      // log) can pick this up without coupling to the widget.
      try {
        window.dispatchEvent(new CustomEvent("aurem:streak-milestone", {
          detail: { milestone: hit, shipped },
        }));
      } catch { /* noop */ }
    } catch {
      // Silent — widget is non-essential.
    }
  }, []);

  useEffect(() => {
    fire();
    const onShipped = () => fire();
    window.addEventListener("aurem:shipped", onShipped);
    const iv = setInterval(fire, POLL_MS);
    return () => {
      window.removeEventListener("aurem:shipped", onShipped);
      clearInterval(iv);
    };
  }, [fire]);

  // Hide until we have a count to avoid layout shift on slow loads.
  if (count === null || count === 0) return null;

  return (
    <div
      data-testid="ship-streak-widget"
      className="group relative inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-3 py-[5px] text-[11px] font-semibold text-primary cursor-pointer transition-colors hover:bg-primary/20"
      title="Weekly ship count · click for share options"
      onClick={() => openTwitter(count)}
    >
      <Flame className="size-3 shrink-0" strokeWidth={2.5} />
      <span data-testid="ship-streak-count">{count}</span>
      <span className="font-normal opacity-80">ships this week</span>

      {/* Hover share row */}
      <div
        className="absolute right-0 top-full z-30 mt-1 hidden gap-1 rounded-md border border-border bg-popover p-1 shadow-xl group-hover:flex"
        style={{ minWidth: 140 }}
      >
        <button
          data-testid="ship-streak-share-twitter"
          onClick={(e) => { e.stopPropagation(); openTwitter(count); }}
          className="flex-1 rounded px-2 py-1 text-[11px] font-medium text-foreground hover:bg-secondary"
        >Tweet</button>
        <button
          data-testid="ship-streak-share-linkedin"
          onClick={(e) => { e.stopPropagation(); openLinkedIn(count); }}
          className="flex-1 rounded px-2 py-1 text-[11px] font-medium text-foreground hover:bg-secondary"
        >LinkedIn</button>
      </div>
    </div>
  );
}
