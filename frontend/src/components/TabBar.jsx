/**
 * TabBar.jsx — Emergent-style header tabs.
 *
 *   [Home] [project_a ✕] [project_b ✕] [+]
 *
 * - "Home" is the default chat scope (no project context)
 * - Each project tab pins the chat to that project (project_id stored
 *   in localStorage as `aurem_active_project`)
 * - "+" button opens the Projects page to add/connect a new repo
 */
import React, { useEffect, useState, useCallback } from "react";
import { Home, X, Plus, FolderGit2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

const ACTIVE_KEY = "aurem_active_project";

export function getActiveProjectId() {
  return localStorage.getItem(ACTIVE_KEY) || null;
}

export function setActiveProjectId(id) {
  if (id) localStorage.setItem(ACTIVE_KEY, id);
  else localStorage.removeItem(ACTIVE_KEY);
  window.dispatchEvent(new Event("aurem:project-changed"));
}

export default function TabBar() {
  const [projects, setProjects] = useState([]);
  const [active, setActive] = useState(() => getActiveProjectId());
  const navigate = useNavigate();

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/cto/projects/list");
      setProjects(r.data?.projects || []);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    refresh();
    const onChange = () => setActive(getActiveProjectId());
    window.addEventListener("aurem:project-changed", onChange);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("aurem:project-changed", onChange);
      window.removeEventListener("focus", refresh);
    };
  }, [refresh]);

  function switchTo(id) {
    setActiveProjectId(id);
    setActive(id);
  }

  function closeTab(e, id) {
    e.stopPropagation();
    if (active === id) {
      setActiveProjectId(null);
      setActive(null);
    }
    // Note: this only "closes" the tab from the active set — project remains in DB.
    // The user can re-open by going to /projects and clicking the row.
  }

  return (
    <div
      data-testid="tabbar"
      style={{
        display: "flex", alignItems: "center", gap: 2,
        padding: "8px 12px 0",
        background: "var(--bg)",
        borderBottom: "1px solid var(--border)",
        overflowX: "auto", minHeight: 42,
      }}
    >
      <Tab
        testid="tab-home"
        label="Home"
        Icon={Home}
        active={!active}
        onClick={() => switchTo(null)}
      />
      {projects.map((p) => (
        <Tab
          key={p.project_id}
          testid={`tab-${p.project_id}`}
          label={p.name}
          Icon={FolderGit2}
          active={active === p.project_id}
          onClick={() => switchTo(p.project_id)}
          onClose={(e) => closeTab(e, p.project_id)}
        />
      ))}
      <button
        data-testid="tab-add"
        onClick={() => navigate("/projects")}
        title="Add a new project"
        style={{
          marginLeft: 4,
          width: 28, height: 28, borderRadius: 4,
          background: "transparent",
          border: "1px solid var(--border)",
          color: "var(--text-faint)",
          cursor: "pointer",
          display: "flex", alignItems: "center", justifyContent: "center",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = "var(--accent-2)";
          e.currentTarget.style.borderColor = "var(--border-strong)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = "var(--text-faint)";
          e.currentTarget.style.borderColor = "var(--border)";
        }}
      >
        <Plus size={13} />
      </button>
    </div>
  );
}

function Tab({ testid, label, Icon, active, onClick, onClose }) {
  return (
    <div
      data-testid={testid}
      onClick={onClick}
      role="button"
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "7px 12px 7px 10px",
        borderTopLeftRadius: 6, borderTopRightRadius: 6,
        borderBottom: "none",
        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
        borderBottom: active ? "1px solid var(--panel)" : "1px solid var(--border)",
        background: active ? "var(--panel)" : "transparent",
        color: active ? "var(--accent-2)" : "var(--text-dim)",
        cursor: "pointer",
        fontSize: 12,
        maxWidth: 200,
        marginBottom: -1,
        position: "relative",
        zIndex: active ? 2 : 1,
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.color = "var(--text)";
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.color = "var(--text-dim)";
      }}
    >
      <Icon size={12} style={{ flexShrink: 0 }} />
      <span
        style={{
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          fontWeight: active ? 600 : 400,
        }}
      >
        {label}
      </span>
      {onClose && (
        <button
          onClick={onClose}
          aria-label="Close tab"
          style={{
            background: "none", border: "none", padding: 0,
            color: "var(--text-faint)", cursor: "pointer",
            display: "inline-flex", marginLeft: 2,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--danger)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-faint)")}
        >
          <X size={11} />
        </button>
      )}
    </div>
  );
}

/**
 * Hook: subscribe to active-project changes from anywhere in the tree.
 */
export function useActiveProject() {
  const [pid, setPid] = useState(() => getActiveProjectId());
  const [project, setProject] = useState(null);

  useEffect(() => {
    function onChange() { setPid(getActiveProjectId()); }
    window.addEventListener("aurem:project-changed", onChange);
    return () => window.removeEventListener("aurem:project-changed", onChange);
  }, []);

  useEffect(() => {
    if (!pid) { setProject(null); return; }
    let cancelled = false;
    api.get("/cto/projects/list")
      .then((r) => {
        if (cancelled) return;
        const p = (r.data?.projects || []).find((x) => x.project_id === pid);
        setProject(p || null);
      })
      .catch(() => !cancelled && setProject(null));
    return () => { cancelled = true; };
  }, [pid]);

  return project;
}
