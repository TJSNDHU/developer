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
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Eye, EyeOff, MessageCircle, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import Shell, { useChatSession } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";
import TabBar, { useActiveProject, setActiveProjectId } from "../components/TabBar";
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

  // Iter 212m-5 — Delete-project guard. Visible only when an active
  // project is set. Iter 212m-15 upgrade — replaced the cheap
  // `window.confirm` (one reflex OK-click = irreversible delete) with
  // a typed-name confirmation modal (the GitHub / Stripe pattern). The
  // user has to literally type the project name before the destructive
  // POST is fired — eliminates the "click red button by accident"
  // class of accidents the testing agent flagged on prod.
  const [deletingProject, setDeletingProject] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteConfirmInput, setDeleteConfirmInput] = useState("");
  const openDeleteModal = useCallback(() => {
    if (!project?.project_id) return;
    setDeleteConfirmInput("");
    setShowDeleteModal(true);
  }, [project]);
  const handleDeleteProject = useCallback(async () => {
    if (!project?.project_id || deletingProject) return;
    if (deleteConfirmInput.trim() !== project.name) return;
    setDeletingProject(true);
    try {
      await api.delete(`/cto/projects/${project.project_id}`);
      toast({ message: `Deleted "${project.name}".`, kind: "success" });
      setShowDeleteModal(false);
      setActiveProjectId(null);            // switches TabBar to Home and refreshes list
      navigate("/dashboard");
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Couldn't delete project.",
        kind: "error",
      });
    } finally {
      setDeletingProject(false);
    }
  }, [project, deletingProject, deleteConfirmInput, navigate]);

  // Track ORA panel open-state so the launch button can hide while the
  // panel is already on screen (the panel header already says Ask Advisor,
  // so the toolbar pill becomes redundant noise).
  const [oraOpen, setOraOpen] = useState(false);
  useEffect(() => {
    const onState = (e) => setOraOpen(!!e?.detail?.open);
    window.addEventListener("aurem:ora-panel-state", onState);
    return () => window.removeEventListener("aurem:ora-panel-state", onState);
  }, []);

  // Iter 163 — auto-hide topbar (tabs + Preview + Ask Advisor) when the
  // user starts typing, mirroring the sidebar auto-hide pattern in
  // Shell.jsx but INDEPENDENT of it: a thin top hot-zone strip
  // appears at the top edge; hovering it brings ONLY the topbar
  // back (not the sidebar). Sidebar peek lives in Shell.
  const [topHidden, setTopHidden] = useState(false);
  const topPeekFromHoverRef = useRef(false);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined"
      && window.matchMedia("(max-width: 900px)").matches
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 900px)");
    const onChange = (e) => setIsMobile(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    const onStart = () => setTopHidden(true);
    const onReset = () => {
      setTopHidden(false);
      topPeekFromHoverRef.current = false;
    };
    window.addEventListener("aurem:chat-session-started", onStart);
    window.addEventListener("aurem:chat-session-reset", onReset);
    return () => {
      window.removeEventListener("aurem:chat-session-started", onStart);
      window.removeEventListener("aurem:chat-session-reset", onReset);
    };
  }, []);
  const onTopHotZoneEnter = useCallback(() => {
    if (isMobile) return;
    topPeekFromHoverRef.current = true;
    setTopHidden(false);
  }, [isMobile]);
  const onTopBarMouseLeave = useCallback(() => {
    if (isMobile) return;
    if (topPeekFromHoverRef.current) {
      topPeekFromHoverRef.current = false;
      setTopHidden(true);
    }
  }, [isMobile]);

  return (
    <div
      data-testid="dashboard-root"
      style={{ display: "flex", flexDirection: "column", height: "100%" }}
    >
      {/* Iter 163 — top hot-zone strip. Visible only when topbar is
          hidden (auto-hide on typing). Hovering brings ONLY the
          topbar back; sidebar stays untouched. */}
      {!isMobile && topHidden && (
        <div
          data-testid="topbar-hotzone"
          onMouseEnter={onTopHotZoneEnter}
          onClick={onTopHotZoneEnter}
          title="Show top tabs"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            height: 8,
            zIndex: 90,
            cursor: "pointer",
            background: "transparent",
          }}
        />
      )}
      <div
        data-testid="dashboard-topbar"
        data-typing-hidden={topHidden && !isMobile ? "true" : "false"}
        onMouseLeave={onTopBarMouseLeave}
        style={{
          display: "flex", alignItems: "center",
          transform: (topHidden && !isMobile) ? "translateY(-105%)" : "translateY(0)",
          opacity: (topHidden && !isMobile) ? 0 : 1,
          pointerEvents: (topHidden && !isMobile) ? "none" : "auto",
          transition: "transform 260ms cubic-bezier(0.4, 0, 0.2, 1), opacity 200ms ease",
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <TabBar />
        </div>
        {project && (
          <button
            data-testid="delete-project-btn"
            onClick={openDeleteModal}
            disabled={deletingProject}
            title={`Delete "${project.name}" project permanently (does NOT touch GitHub)`}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              fontSize: 11, padding: "5px 10px",
              marginRight: 8,
              background: "rgba(239,68,68,0.08)",
              border: "1px solid rgba(239,68,68,0.32)",
              borderRadius: 6,
              color: "#ef4444",
              cursor: deletingProject ? "wait" : "pointer",
              flexShrink: 0,
              opacity: deletingProject ? 0.6 : 1,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            <Trash2 size={11} />
            {deletingProject ? "Deleting…" : "Delete project"}
          </button>
        )}
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
            title="Ask Advisor — second-opinion AI panel"
            className="ask-ora-launch-btn hidden-on-mobile"
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
            Ask Advisor
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

      {showDeleteModal && project && (
        <div
          data-testid="delete-project-modal-overlay"
          onClick={() => !deletingProject && setShowDeleteModal(false)}
          style={{
            position: "fixed", inset: 0, zIndex: 9600,
            background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 24,
          }}
        >
          <div
            data-testid="delete-project-modal"
            onClick={(e) => e.stopPropagation()}
            style={{
              maxWidth: 480, width: "100%",
              background: "var(--panel)",
              border: "1px solid var(--danger)",
              borderRadius: 8,
              padding: 28,
              color: "var(--text)",
              boxShadow: "0 24px 60px -12px rgba(0,0,0,0.7), 0 0 24px -8px var(--danger)",
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
              <Trash2 size={18} color="var(--danger)" />
              <h2 style={{ margin: 0, fontSize: 16, color: "var(--danger)", letterSpacing: "0.05em" }}>
                Delete project — confirm
              </h2>
            </div>
            <p style={{ fontSize: 13, color: "var(--text-dim)", lineHeight: 1.55, margin: "0 0 6px" }}>
              You are about to permanently delete <b style={{ color: "var(--text)" }}>{project.name}</b>.
              This removes the saved PAT, repo link, and all task history for this project.
            </p>
            <p style={{ fontSize: 12, color: "var(--text-faint)", lineHeight: 1.55, margin: "0 0 18px" }}>
              Your GitHub repository at <code>{project.github_owner}/{project.github_repo}</code> is
              <b style={{ color: "var(--accent-2)" }}> NOT </b>touched. This cannot be undone.
            </p>
            <label style={{ fontSize: 10, color: "var(--text-faint)", letterSpacing: "0.1em",
                            textTransform: "uppercase", display: "block", marginBottom: 6 }}>
              Type <code style={{ color: "var(--danger)", background: "rgba(239,68,68,0.08)",
                                  padding: "1px 5px", borderRadius: 3 }}>{project.name}</code> to confirm
            </label>
            <input
              data-testid="delete-project-confirm-input"
              autoFocus
              value={deleteConfirmInput}
              onChange={(e) => setDeleteConfirmInput(e.target.value)}
              disabled={deletingProject}
              placeholder={project.name}
              style={{
                width: "100%", padding: "8px 10px",
                background: "var(--bg)", color: "var(--text)",
                border: "1px solid var(--border)",
                borderRadius: 4, fontSize: 13,
                fontFamily: "'JetBrains Mono', monospace",
                marginBottom: 16,
              }}
            />
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button
                data-testid="delete-project-cancel"
                onClick={() => setShowDeleteModal(false)}
                disabled={deletingProject}
                className="btn-ghost"
                style={{ padding: "7px 14px", fontSize: 12 }}
              >
                Cancel
              </button>
              <button
                data-testid="delete-project-confirm"
                onClick={handleDeleteProject}
                disabled={deletingProject || deleteConfirmInput.trim() !== project.name}
                style={{
                  padding: "7px 14px", fontSize: 12, fontWeight: 600,
                  background: "var(--danger)", color: "#0a0a0a",
                  border: "1px solid var(--danger)", borderRadius: 4,
                  cursor: (deletingProject || deleteConfirmInput.trim() !== project.name)
                    ? "not-allowed" : "pointer",
                  opacity: (deleteConfirmInput.trim() !== project.name) ? 0.4 : 1,
                  fontFamily: "'JetBrains Mono', monospace",
                  letterSpacing: "0.05em",
                }}
              >
                {deletingProject ? "Deleting…" : "Delete forever"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
