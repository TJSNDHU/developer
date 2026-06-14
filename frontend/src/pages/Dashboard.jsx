/**
 * Dashboard.jsx — Authenticated home: full-width chat with a top-right
 * "Preview / Hide preview" toggle that drives ChatPanel's right-side
 * live iframe pane.
 *
 * Iter 145 — collapsed prior split-pane layout. The legacy PreviewPane
 * showed only "No preview yet" and was redundant with ChatPanel's
 * existing live-URL iframe. Top-right button now dispatches a
 * `aurem:toggle-preview` window event that ChatPanel listens for.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Eye, EyeOff, MessageCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";
import Shell, { useChatSession } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";
import TabBar, { useActiveProject } from "../components/TabBar";
import NewUserWizard, { isWizardDismissed } from "../components/NewUserWizard";
import { toast } from "../components/Toast";
import { api } from "../lib/api";

const PREVIEW_PREF_KEY = "aurem_preview_open";
const SHARE_MILESTONES = [10, 25, 50, 100, 250];

export default function Dashboard() {
  return (
    <Shell requireAuth>
      <DashboardBody />
    </Shell>
  );
}

function DashboardBody() {
  const { sessionId, refreshSessions } = useChatSession();
  const project = useActiveProject();
  const navigate = useNavigate();
  const [showWizard, setShowWizard] = useState(false);
  const [showPreview, setShowPreview] = useState(() => {
    try { return localStorage.getItem(PREVIEW_PREF_KEY) === "1"; }
    catch { return false; }
  });

  useEffect(() => {
    let cancelled = false;
    if (isWizardDismissed()) return;
    api.get("/cto/projects/list")
      .then((r) => {
        if (cancelled) return;
        const count = (r.data?.projects || []).length;
        if (count === 0) setShowWizard(true);
      })
      .catch(() => { /* ignore */ });
    return () => { cancelled = true; };
  }, []);

  // Milestone share-prompt on ship events (preview auto-open moved
  // into ChatPanel; here we only handle the celebratory toast).
  useEffect(() => {
    const handler = (e) => {
      const id = e?.detail?.task_id;
      if (!id) return;
      api.get("/wrapped/me?period=all_time").then((r) => {
        const shipped = r.data?.stats?.tasks_shipped || 0;
        const milestone = SHARE_MILESTONES.find(
          (m) => shipped >= m && !localStorage.getItem(`aurem_toast_${m}`),
        );
        if (!milestone) return;
        try { localStorage.setItem(`aurem_toast_${milestone}`, "1"); }
        catch { /* ignore */ }
        toast({
          message: `🎉 You've shipped ${milestone} tasks with AUREM — tap to share your Wrapped`,
          kind: "info",
          duration: 8000,
          onClick: () => navigate("/wrapped"),
        });
      }).catch(() => { /* silent */ });
    };
    window.addEventListener("aurem:shipped", handler);
    return () => window.removeEventListener("aurem:shipped", handler);
  }, [navigate]);

  // Keep button label in sync with ChatPanel's internal preview state
  // (ChatPanel may auto-open the pane when a project with a preview_url
  // is selected, or close it after a code reply).
  useEffect(() => {
    const onStateChanged = (e) => {
      const open = !!e?.detail?.open;
      setShowPreview(open);
    };
    window.addEventListener("aurem:preview-state-changed", onStateChanged);
    return () => window.removeEventListener("aurem:preview-state-changed", onStateChanged);
  }, []);

  const togglePreview = useCallback(() => {
    setShowPreview((p) => {
      const next = !p;
      try { localStorage.setItem(PREVIEW_PREF_KEY, next ? "1" : "0"); }
      catch { /* ignore */ }
      window.dispatchEvent(new CustomEvent("aurem:toggle-preview", {
        detail: { open: next },
      }));
      return next;
    });
  }, []);

  // Track ORA panel open-state so the launch button can hide while the
  // panel is already on screen (the panel header already says ASK ORA,
  // so the toolbar pill becomes redundant noise).
  const [oraOpen, setOraOpen] = useState(false);
  useEffect(() => {
    const onState = (e) => setOraOpen(!!e?.detail?.open);
    window.addEventListener("aurem:ora-panel-state", onState);
    return () => window.removeEventListener("aurem:ora-panel-state", onState);
  }, []);

  return (
    <div
      data-testid="dashboard-root"
      style={{ display: "flex", flexDirection: "column", height: "100%" }}
    >
      <div style={{ display: "flex", alignItems: "center" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <TabBar />
        </div>
        <button
          data-testid="preview-toggle"
          onClick={togglePreview}
          title={showPreview ? "Hide live preview" : "Show live preview"}
          className="preview-toggle-btn"
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: 11, padding: "5px 10px",
            background: showPreview
              ? "rgba(255,138,42,0.10)"
              : "rgba(255,255,255,0.04)",
            border: "1px solid var(--border, rgba(255,200,120,0.16))",
            borderRadius: 6,
            color: showPreview ? "var(--accent-2, #ffb347)" : "var(--text-dim)",
            cursor: "pointer", flexShrink: 0,
          }}
        >
          {showPreview ? <EyeOff size={11} /> : <Eye size={11} />}
          {showPreview ? "Hide preview" : "Preview"}
        </button>
        {!oraOpen && (
          <button
            data-testid="ask-ora-launch-btn"
            onClick={() => {
              try { window.dispatchEvent(new CustomEvent("aurem:ora-open")); }
              catch { /* ignore */ }
            }}
            title="Ask ORA — second-opinion AI panel"
            className="ask-ora-launch-btn"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              fontSize: 10, fontWeight: 700,
              padding: "5px 12px",
              margin: "0 14px 0 10px",
              background: "var(--accent-soft, rgba(255,138,42,0.10))",
              border: "1px solid var(--accent, rgba(255,138,42,0.4))",
              borderRadius: 6,
              color: "var(--accent-2, #ffb347)",
              cursor: "pointer", flexShrink: 0,
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.12em",
              boxShadow: "0 0 10px -3px var(--accent, rgba(255,138,42,0.4))",
            }}
          >
            <MessageCircle size={11} />
            ASK ORA
          </button>
        )}
      </div>

      <div style={{ flex: 1, minHeight: 0, display: "flex", overflow: "hidden" }}>
        <div
          data-testid="chat-pane"
          style={{ width: "100%", minWidth: 0, overflow: "hidden" }}
        >
          <ChatPanel
            sessionId={sessionId}
            onTurnSaved={refreshSessions}
            activeProject={project}
          />
        </div>
      </div>

      {showWizard && (
        <NewUserWizard onComplete={() => setShowWizard(false)} />
      )}
    </div>
  );
}
