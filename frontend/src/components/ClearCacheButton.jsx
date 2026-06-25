/**
 * components/ClearCacheButton.jsx — Iter 212m-25
 *
 * Small "🧹 Clear cache" button that lives under the customer-app
 * logo in the sidebar. Clicking it wipes UI cache (NOT auth) and
 * reloads the current page so the user gets a clean state.
 *
 * Hidden when the sidebar is collapsed so we don't crowd the rail.
 */
import React, { useState } from "react";
import { Sparkles, Loader2 } from "lucide-react";
import { clearUICacheAndReload } from "../lib/cacheCleaner";
import { toast } from "./Toast";

export default function ClearCacheButton({ collapsed = false, testid }) {
  const [busy, setBusy] = useState(false);

  async function onClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    try {
      const { cleared } = await clearUICacheAndReload();
      const totalKeys = (cleared.localStorage || 0)
                       + (cleared.sessionStorage || 0)
                       + (cleared.indexedDB || 0)
                       + (cleared.caches || 0);
      toast({
        message: `🧹 Cache cleared (${totalKeys} item${totalKeys === 1 ? "" : "s"}) — refreshing…`,
        kind: "success",
      });
    } catch (err) {
      toast({
        message: `Cache clear failed: ${err.message || err}`,
        kind: "error",
      });
      setBusy(false);
    }
  }

  if (collapsed) return null;

  return (
    <button
      type="button"
      data-testid={testid || "clear-cache-btn"}
      onClick={onClick}
      title="Clear UI cache and refresh this page (you stay signed in)"
      disabled={busy}
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "6px 10px",
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        color: "var(--text-faint)",
        fontSize: 10, letterSpacing: "0.04em", textTransform: "uppercase",
        cursor: busy ? "wait" : "pointer",
        marginTop: 6, marginLeft: 6,
        width: "calc(100% - 12px)", textAlign: "left",
        transition: "color 120ms, border-color 120ms, background 120ms",
        opacity: busy ? 0.6 : 1,
        fontFamily: "'JetBrains Mono', monospace",
      }}
      onMouseEnter={(e) => {
        if (busy) return;
        e.currentTarget.style.color = "var(--accent)";
        e.currentTarget.style.borderColor = "var(--border-strong)";
      }}
      onMouseLeave={(e) => {
        if (busy) return;
        e.currentTarget.style.color = "var(--text-faint)";
        e.currentTarget.style.borderColor = "var(--border)";
      }}
    >
      {busy
        ? <Loader2 size={11} className="spin" />
        : <Sparkles size={11} />}
      <span>{busy ? "clearing…" : "clear cache"}</span>
    </button>
  );
}
