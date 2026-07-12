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
import { X, Plus, FolderGit2 } from "lucide-react";
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
      const list = r.data?.projects || [];
      setProjects(list);
      // Keep the cache fresh for hooks that hydrate synchronously
      // on next mount (useActiveProject reads this on first render).
      try { localStorage.setItem("aurem_projects_cache", JSON.stringify(list)); }
      catch { /* quota — ignore */ }

      // Iter 212m-190 — Active-project auto-restore / auto-heal.
      // Priority order:
      //   1. If saved active id still exists in the list → keep it.
      //   2. If saved id was deleted (or never set) AND at least one
      //      *wired* (has github_owner + github_repo) project exists →
      //      auto-activate the first wired one. This covers:
      //         a) fresh browsers with no localStorage seed
      //         b) users whose active project was deleted while they
      //            were logged out
      //         c) the pre-existing "exactly one wired project" case
      //   3. If nothing wired but there are projects → activate first
      //      so the chat isn't stuck on null forever.
      const savedId = getActiveProjectId();
      const savedStillExists = savedId && list.some((p) => p.project_id === savedId);
      const wired = list.filter((p) => p.github_owner && p.github_repo);

      if (savedStillExists) {
        // Nothing to do — user's last project is intact.
        return;
      }
      // Saved id is either missing or points to a deleted project.
      if (wired.length > 0) {
        setActiveProjectId(wired[0].project_id);
      } else if (list.length > 0) {
        setActiveProjectId(list[0].project_id);
      } else if (savedId) {
        // No projects at all but a stale id was pinned — clear it so
        // downstream consumers correctly render the "no repo" state
        // instead of pointing at a ghost project.
        setActiveProjectId(null);
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    refresh();
    const onChange = () => {
      setActive(getActiveProjectId());
      // Iter 212m-5 — also refresh the project list when active project
      // changes. This catches deletion (sets active to null) so the
      // deleted tab disappears immediately instead of waiting for
      // window focus.
      refresh();
    };
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
        background: "transparent",
        borderBottom: "1px solid var(--border)",
        overflowX: "auto", minHeight: 42,
      }}
    >
      {/* Iter 212m-20 — Home tab removed per founder request.
          The chat panel always operates inside a project scope; the
          "no project" state is reachable via /projects sidebar.
          Removing the Home pill cleans up the tab strip so customers
          don't accidentally drop out of their active project. */}
      {projects.map((p) => (
        <Tab
          key={p.project_id}
          testid={`tab-${p.project_id}`}
          // Iter 212m-15 — also expose a name-slug testid so Playwright
          // scripts can drive `getByTestId('project-tab-dogfood')`
          // instead of needing the opaque project_id.
          nameTestid={`project-tab-${(p.name || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-')}`}
          label={p.name}
          Icon={FolderGit2}
          active={active === p.project_id}
          onClick={() => switchTo(p.project_id)}
          onClose={(e) => closeTab(e, p.project_id)}
        />
      ))}
      <button
        data-testid="tab-add"
        onClick={() => navigate("/projects?add=1")}
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

function Tab({ testid, nameTestid, label, Icon, active, onClick, onClose }) {
  return (
    <div
      data-testid={testid}
      data-name-testid={nameTestid}
      onClick={onClick}
      role="button"
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "7px 12px 7px 10px",
        borderTopLeftRadius: 6, borderTopRightRadius: 6,
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
  // Iter 212m-105 — Synchronously hydrate from localStorage cache so
  // downstream consumers (AskAdvisor projectId, ChatPanel activeProject,
  // TopBar breadcrumb) get a non-null value on the very first render
  // after login. Without this, the Ask Advisor's tool calls hit the
  // backend with project_id=null and the model replies "No repo
  // connected" even when the user has projects.
  const [project, setProject] = useState(() => {
    if (!pid) return null;
    try {
      const raw = localStorage.getItem("aurem_projects_cache");
      if (!raw) return null;
      const cached = JSON.parse(raw);
      return (Array.isArray(cached) && cached.find((x) => x.project_id === pid)) || null;
    } catch { return null; }
  });

  useEffect(() => {
    function onChange() { setPid(getActiveProjectId()); }
    window.addEventListener("aurem:project-changed", onChange);
    return () => window.removeEventListener("aurem:project-changed", onChange);
  }, []);

  useEffect(() => {
    if (!pid) {
      // Iter 212m-190 — even with no saved active id, still fetch the
      // list so we can AUTO-SEED the first wired project. This is the
      // reliable auto-restore path for:
      //   • fresh browsers with empty localStorage
      //   • users whose last active project was deleted while logged out
      //   • incognito sessions
      let cancelled = false;
      api.get("/cto/projects/list")
        .then((r) => {
          if (cancelled) return;
          const list = r.data?.projects || [];
          try { localStorage.setItem("aurem_projects_cache", JSON.stringify(list)); }
          catch { /* quota — ignore */ }
          if (list.length === 0) return;
          const wired = list.filter((p) => p.github_owner && p.github_repo);
          const target = wired[0] || list[0];
          if (target) {
            // setActiveProjectId dispatches `aurem:project-changed`
            // which re-runs this hook with the new pid, so we do not
            // manually call setProject here.
            setActiveProjectId(target.project_id);
          }
        })
        .catch(() => { /* offline / auth failure — silent */ });
      return () => { cancelled = true; };
    }
    let cancelled = false;
    api.get("/cto/projects/list")
      .then((r) => {
        if (cancelled) return;
        const list = r.data?.projects || [];
        // Keep the cache fresh for the next mount.
        try { localStorage.setItem("aurem_projects_cache", JSON.stringify(list)); }
        catch { /* quota — ignore */ }
        const p = list.find((x) => x.project_id === pid);
        if (p) {
          setProject(p);
          return;
        }
        // Iter 212m-190 — saved active id points to a project that no
        // longer exists (deleted while logged out, revoked, etc.).
        // Auto-heal by falling back to the first wired project instead
        // of leaving the UI stuck on a ghost.
        if (list.length > 0) {
          const wired = list.filter((x) => x.github_owner && x.github_repo);
          const target = wired[0] || list[0];
          if (target) {
            setActiveProjectId(target.project_id);   // triggers hook re-run
            return;
          }
        }
        // No projects at all — clear the stale pin so consumers render
        // the "no repo" state correctly.
        setProject(null);
        setActiveProjectId(null);
      })
      .catch(() => { /* keep cached project intact on transient failure */ });
    return () => { cancelled = true; };
  }, [pid]);

  return project;
}
