/**
 * RepoCleanupBanner.jsx — Iter 212m-136
 *
 * Dashboard banner that surfaces when one or more of the user's
 * projects point to a GitHub repo that has been deleted or renamed.
 *
 * Flow:
 *  1. On mount + on `aurem:repo-status-refresh` event, fetch
 *     /api/aurem-dev/cto/projects/cleanup-summary.
 *  2. If `count > 0`, render a slim amber pill banner:
 *     "N projects point to deleted repos — Clean up"
 *  3. Clicking opens a modal listing each broken project with a
 *     pre-checked checkbox + the GitHub URL it used to point at.
 *  4. Confirm → POST /cleanup-delete; on success fires the
 *     `aurem:repo-status-refresh` event so the sidebar drops the
 *     red rows immediately + shows a success toast.
 *
 * No new dependencies. Uses the same fetch utility (`api`) and
 * toast singleton (`Toast`) used everywhere else.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { toast } from "./Toast";

const REASON_LABEL = {
  repo_not_found: "Repo deleted or renamed on GitHub",
  github_rejected: "Token rejected (revoked or missing scope)",
  repo_not_set:    "No repo linked",
  no_token:        "No token to authenticate",
};

export default function RepoCleanupBanner() {
  const [broken, setBroken] = useState([]);
  const [open, setOpen]     = useState(false);
  const [busy, setBusy]     = useState(false);
  const [selected, setSelected] = useState(() => new Set());

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/cto/projects/cleanup-summary");
      const body = r?.data || {};
      const list = Array.isArray(body.broken) ? body.broken : [];
      setBroken(list);
      // Default-select every broken project so the user can just hit
      // "Delete" without ticking 5 boxes manually.
      setSelected(new Set(list.map((p) => p.project_id)));
    } catch {
      // Soft-fail: never block the dashboard because the banner failed.
      setBroken([]);
    }
  }, []);

  useEffect(() => {
    refresh();
    const onRefresh = () => refresh();
    window.addEventListener("aurem:repo-status-refresh", onRefresh);
    // 5-min auto refresh so users who leave the tab open get fresh state.
    const t = setInterval(refresh, 5 * 60 * 1000);
    return () => {
      window.removeEventListener("aurem:repo-status-refresh", onRefresh);
      clearInterval(t);
    };
  }, [refresh]);

  const toggle = (pid) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  };

  const confirmDelete = async () => {
    if (busy || selected.size === 0) return;
    setBusy(true);
    try {
      const ids = Array.from(selected);
      const r = await api.post("/cto/projects/cleanup-delete", {
        project_ids: ids,
      });
      const body = r?.data || {};
      if (body.deleted > 0) {
        toast.success(
          `Cleaned up ${body.deleted} project${body.deleted === 1 ? "" : "s"}.`,
        );
      } else if (body.skipped > 0) {
        toast.error("No projects deleted — they may have been re-linked already.");
      }
      // Tell the sidebar + projects list to refresh.
      window.dispatchEvent(new Event("aurem:projects-changed"));
      window.dispatchEvent(new Event("aurem:repo-status-refresh"));
      setOpen(false);
      await refresh();
    } catch (e) {
      toast.error(`Cleanup failed: ${e?.message || "unknown error"}`);
    } finally {
      setBusy(false);
    }
  };

  if (broken.length === 0) return null;

  const count = broken.length;

  return (
    <>
      <button
        type="button"
        data-testid="repo-cleanup-banner"
        onClick={() => setOpen(true)}
        style={{
          display: "flex", alignItems: "center", gap: 10,
          // Iter 212m-196 — Banner width now hugs the chat input's
          // inner column. Match the composer's `padding: 14px
          // clamp(16px, 17.25%, 240px)` and the LoopStepBar's
          // `margin: 8px clamp(16px, 17.25%, 240px)` so the amber
          // pill, LOOP strip and chat textarea all sit on the same
          // left/right rail. Previous `margin: 6px 12px 0` +
          // `width: calc(100% - 24px)` made the banner span nearly
          // full chat-pane width (~2x wider than the input).
          margin: "6px clamp(16px, 17.25%, 240px) 0",
          padding: "8px 14px",
          background: "rgba(245,158,11,0.10)",
          border: "1px solid rgba(245,158,11,0.45)",
          borderRadius: 10,
          color: "#fbbf24",
          fontSize: 12, fontFamily: "'JetBrains Mono', monospace",
          letterSpacing: "0.04em",
          cursor: "pointer",
          textAlign: "left",
        }}
        title={`${count} project${count === 1 ? "" : "s"} disconnected — open cleanup`}
      >
        <span style={{ fontSize: 14 }}>⚠</span>
        <span style={{ flex: 1 }}>
          <strong>{count}</strong> project{count === 1 ? "" : "s"} point
          {count === 1 ? "s" : ""} to deleted or unreachable repos — click to clean up
        </span>
        <span style={{ opacity: 0.7, fontSize: 11 }}>Open →</span>
      </button>

      {open && (
        <div
          data-testid="repo-cleanup-modal"
          onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}
          style={{
            position: "fixed", inset: 0, zIndex: 9000,
            background: "rgba(0,0,0,0.55)",
            backdropFilter: "blur(4px)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 24,
          }}
        >
          <div
            role="dialog"
            aria-labelledby="repo-cleanup-title"
            style={{
              width: "min(560px, 100%)", maxHeight: "80vh",
              background: "rgba(20,20,22,0.96)",
              border: "1px solid rgba(245,158,11,0.45)",
              borderRadius: 14,
              boxShadow: "0 20px 60px rgba(0,0,0,0.45)",
              padding: 18, display: "flex", flexDirection: "column", gap: 12,
              color: "#f5f5f7",
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            <div id="repo-cleanup-title"
                 style={{ fontSize: 14, color: "#fbbf24",
                          letterSpacing: "0.06em" }}>
              CLEAN UP {count} BROKEN PROJECT{count === 1 ? "" : "S"}
            </div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.7)",
                          lineHeight: 1.5 }}>
              These projects point to a GitHub repo that returns an error
              when we ping it. Untick anything you&apos;d rather keep (and re-link
              manually later via Settings).
            </div>
            <div data-testid="repo-cleanup-list"
                 style={{ overflowY: "auto", display: "flex",
                          flexDirection: "column", gap: 6,
                          paddingRight: 4 }}>
              {broken.map((p) => {
                const checked = selected.has(p.project_id);
                const slug = p.owner && p.repo ? `${p.owner}/${p.repo}` : "—";
                return (
                  <label
                    key={p.project_id}
                    data-testid={`repo-cleanup-row-${p.project_id}`}
                    style={{
                      display: "flex", alignItems: "flex-start", gap: 10,
                      padding: "8px 10px",
                      background: checked
                        ? "rgba(239,68,68,0.08)"
                        : "rgba(255,255,255,0.03)",
                      border: `1px solid ${checked
                        ? "rgba(239,68,68,0.35)"
                        : "rgba(255,255,255,0.08)"}`,
                      borderRadius: 8,
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(p.project_id)}
                      style={{ marginTop: 2 }}
                    />
                    <span style={{ display: "flex", flexDirection: "column",
                                   gap: 2, flex: 1 }}>
                      <span style={{ color: "#f5f5f7", fontWeight: 600 }}>
                        {p.name}
                      </span>
                      <span style={{ color: "rgba(255,255,255,0.55)",
                                     fontSize: 11 }}>
                        {slug}{p.branch ? ` · ${p.branch}` : ""}
                      </span>
                      <span style={{ color: "#fca5a5", fontSize: 10,
                                     letterSpacing: "0.04em" }}>
                        {REASON_LABEL[p.error] || p.error || "Disconnected"}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end",
                          gap: 8, marginTop: 4 }}>
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={busy}
                data-testid="repo-cleanup-cancel"
                style={{
                  padding: "8px 14px", fontSize: 12,
                  background: "transparent",
                  border: "1px solid rgba(255,255,255,0.15)",
                  borderRadius: 8, color: "#f5f5f7",
                  cursor: busy ? "not-allowed" : "pointer",
                  opacity: busy ? 0.5 : 1,
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={confirmDelete}
                disabled={busy || selected.size === 0}
                data-testid="repo-cleanup-confirm"
                style={{
                  padding: "8px 14px", fontSize: 12, fontWeight: 600,
                  background: selected.size === 0
                    ? "rgba(239,68,68,0.25)" : "#ef4444",
                  border: "1px solid #ef4444",
                  borderRadius: 8, color: "#fff",
                  cursor: (busy || selected.size === 0)
                    ? "not-allowed" : "pointer",
                  opacity: busy ? 0.6 : 1,
                  minWidth: 140,
                }}
              >
                {busy ? "Deleting…"
                  : `Delete ${selected.size} project${selected.size === 1 ? "" : "s"}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
