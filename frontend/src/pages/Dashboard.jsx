/**
 * Dashboard.jsx — Authenticated home, chat on the left + live preview
 * on the right (Bolt-style activation pattern).
 *
 * Layout:
 *   ┌──────────────┬──────────────┐
 *   │  ChatPanel   │ PreviewPane  │
 *   │  (left)      │ (right)      │
 *   └──────────────┴──────────────┘
 *
 * Resize handle in the middle. "◈ Preview" toggle in the top bar
 * hides/shows the right side so existing single-pane users don't
 * get whiplash.  Auto-shows the first time a task ships.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import Shell, { useChatSession } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";
import PreviewPane from "../components/PreviewPane";
import TabBar, { useActiveProject } from "../components/TabBar";
import NewUserWizard, { isWizardDismissed } from "../components/NewUserWizard";
import { api } from "../lib/api";

const PREVIEW_PREF_KEY = "aurem_preview_open";

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
  const [showWizard, setShowWizard] = useState(false);
  const [latestTaskId, setLatestTaskId] = useState(null);
  const [showPreview, setShowPreview] = useState(() => {
    try { return localStorage.getItem(PREVIEW_PREF_KEY) === "1"; }
    catch { return false; }
  });
  const [splitRatio, setSplitRatio] = useState(60);
  const dragRef = useRef(false);

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

  // Listen for ship events from ChatPanel and auto-pop the preview pane
  // the very first time something ships in this session.
  useEffect(() => {
    const handler = (e) => {
      const id = e?.detail?.task_id;
      if (!id) return;
      setLatestTaskId(id);
      // Only auto-show; respect explicit user hide afterwards.
      if (localStorage.getItem(PREVIEW_PREF_KEY) === null) {
        setShowPreview(true);
        try { localStorage.setItem(PREVIEW_PREF_KEY, "1"); } catch { /* ignore */ }
      }
    };
    window.addEventListener("aurem:shipped", handler);
    return () => window.removeEventListener("aurem:shipped", handler);
  }, []);

  const startDrag = useCallback(() => {
    dragRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const onMove = (ev) => {
      if (!dragRef.current) return;
      const pct = Math.min(75, Math.max(30, (ev.clientX / window.innerWidth) * 100));
      setSplitRatio(pct);
    };
    const onUp = () => {
      dragRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  function togglePreview() {
    setShowPreview((p) => {
      const next = !p;
      try { localStorage.setItem(PREVIEW_PREF_KEY, next ? "1" : "0"); }
      catch { /* ignore */ }
      return next;
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <TabBar />
        </div>
        <button
          data-testid="preview-toggle"
          onClick={togglePreview}
          title={showPreview ? "Hide preview pane" : "Show live preview pane"}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: 11, padding: "5px 10px",
            margin: "0 14px",
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
      </div>

      <div style={{ flex: 1, minHeight: 0, display: "flex", overflow: "hidden" }}>
        <div
          data-testid="chat-pane"
          style={{
            width: showPreview ? `${splitRatio}%` : "100%",
            minWidth: 0, overflow: "hidden",
            transition: dragRef.current ? "none" : "width .15s ease",
          }}
        >
          <ChatPanel
            sessionId={sessionId}
            onTurnSaved={refreshSessions}
            activeProject={project}
          />
        </div>

        {showPreview && (
          <>
            <div
              data-testid="split-handle"
              onMouseDown={startDrag}
              style={{
                width: 4, flexShrink: 0, cursor: "col-resize",
                background: "var(--border, rgba(255,200,120,0.10))",
                transition: "background .15s",
              }}
              onMouseEnter={(e) =>
                (e.currentTarget.style.background = "rgba(255,138,42,0.32)")}
              onMouseLeave={(e) =>
                (e.currentTarget.style.background = "var(--border, rgba(255,200,120,0.10))")}
            />
            <div style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
              <PreviewPane taskId={latestTaskId} />
            </div>
          </>
        )}
      </div>

      {showWizard && (
        <NewUserWizard onComplete={() => setShowWizard(false)} />
      )}
    </div>
  );
}
