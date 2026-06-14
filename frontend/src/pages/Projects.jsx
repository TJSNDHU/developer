/**
 * Projects.jsx — Multi-project CTO dashboard.
 *
 * Workflow:
 *   1. Connect a client's existing GitHub repo (PAT or OAuth-derived token)
 *   2. Pick a project → submit a natural-language task
 *   3. Background worker clones → AI edits → commits → pushes
 *   4. Live step log + commit SHA + task history
 */
import React, { useEffect, useState, useCallback } from "react";
import {
  Plus, FolderGit2, Github, Send, Trash2, Loader2,
  CheckCircle2, AlertCircle, RefreshCw, ExternalLink,
  Pencil, Info, Undo2, Copy as CopyIcon,
} from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api } from "../lib/api";
import { toast } from "../components/Toast";

export default function Projects() {
  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="multi-project"
        title="Client Projects"
        sub="Connect any client's existing GitHub repo. Submit tasks in plain English — AUREM CTO pulls, edits, commits, pushes."
      />
      <Body />
    </Shell>
  );
}

function Body() {
  const [projects, setProjects] = useState([]);
  const [showAdd, setShowAdd] = useState(false);
  const [active, setActive] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/cto/projects/list");
      const list = r.data?.projects || [];
      setProjects(list);
      // Keep `active` in sync with the latest server copy of the selected project
      setActive((cur) =>
        cur ? list.find((p) => p.project_id === cur.project_id) || null : cur
      );
    } catch (e) {
      toast({ message: "Couldn't load projects", kind: "error" });
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "minmax(0, 320px) minmax(0, 1fr)",
      gap: 24, minHeight: 540,
      width: "100%", maxWidth: "100%", minWidth: 0,
    }}>
      <aside data-testid="proj-list" className="card" style={{ padding: 14, alignSelf: "start", minWidth: 0, overflow: "hidden" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <span className="eyebrow">projects</span>
          <button data-testid="proj-add-btn" className="btn-ghost" onClick={() => setShowAdd(true)}
                  style={{ padding: "4px 10px", fontSize: 11 }}>
            <Plus size={12} /> Add
          </button>
        </div>
        {projects.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--text-faint)" }}>
            No projects yet. Click <strong>+ Add</strong> to connect a client repo.
          </p>
        )}
        {projects.map((p) => {
          const sel = active?.project_id === p.project_id;
          return (
            <div
              key={p.project_id}
              data-testid={`proj-row-${p.project_id}`}
              onClick={() => setActive(p)}
              style={{
                padding: "10px 12px", borderRadius: 4, cursor: "pointer",
                marginBottom: 6,
                background: sel ? "var(--accent-soft)" : "transparent",
                borderLeft: sel ? "2px solid var(--accent)" : "2px solid transparent",
              }}
            >
              <div style={{ fontSize: 13, color: sel ? "var(--accent-2)" : "var(--text)" }}>
                {p.name}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace" }}>
                {p.github_owner}/{p.github_repo}
                {p.tasks_done ? ` · ${p.tasks_done} tasks` : ""}
              </div>
            </div>
          );
        })}
      </aside>

      <section style={{ minWidth: 0 }}>
        {active ? (
          <ProjectDetail
            key={active.project_id}
            project={active}
            onRemoved={() => { setActive(null); refresh(); }}
            onChanged={refresh}
          />
        ) : (
          <div className="card" data-testid="proj-empty" style={{ textAlign: "center", color: "var(--text-faint)", padding: 60 }}>
            <FolderGit2 size={28} style={{ opacity: 0.4, marginBottom: 10 }} />
            <p>Select or add a project to start submitting tasks.</p>
          </div>
        )}
      </section>

      {showAdd && <AddDialog onClose={() => setShowAdd(false)} onAdded={() => { setShowAdd(false); refresh(); }} />}
    </div>
  );
}

function PatHelpTooltip() {
  const [open, setOpen] = useState(false);
  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        data-testid="pat-help-btn"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        title="How to get a PAT"
        style={{
          background: "none", border: "none", padding: 0,
          cursor: "pointer", color: "var(--accent-2)",
          display: "inline-flex", verticalAlign: "middle",
        }}
      >
        <Info size={12} />
      </button>
      {open && (
        <div
          onMouseLeave={() => setOpen(false)}
          data-testid="pat-help-tooltip"
          style={{
            position: "absolute", top: 18, left: 0, zIndex: 50,
            width: 360, padding: 14,
            background: "var(--bg-elev)",
            border: "1px solid var(--accent-2)",
            borderRadius: 4, fontSize: 11,
            lineHeight: 1.6, color: "var(--text)",
            boxShadow: "0 12px 32px -8px rgba(0,0,0,0.6)",
          }}
        >
          <div style={{ color: "var(--accent-2)", fontWeight: 600, marginBottom: 6 }}>
            How to get a GitHub PAT
          </div>
          <ol style={{ paddingLeft: 16, margin: "0 0 8px", color: "var(--text-dim)" }}>
            <li>Open{" "}
              <a href="https://github.com/settings/tokens?type=beta" target="_blank" rel="noreferrer"
                 style={{ color: "var(--accent-2)" }}>
                github.com/settings/tokens
              </a> → <strong>Fine-grained tokens</strong>
            </li>
            <li>Click <strong>Generate new token</strong></li>
            <li><strong>Repository access:</strong> select the repo(s) you want AUREM CTO to edit</li>
            <li>
              <strong>Permissions needed:</strong>
              <ul style={{ paddingLeft: 16, marginTop: 4 }}>
                <li><code>Contents</code>: <strong>Read and write</strong> ← push code</li>
                <li><code>Metadata</code>: <strong>Read-only</strong> (auto-added)</li>
                <li><code>Pull requests</code>: <strong>Read and write</strong> (optional, future)</li>
              </ul>
            </li>
            <li>Generate → copy the <code>github_pat_xxx</code> token (only shown once)</li>
            <li>Paste it here. We store it encrypted.</li>
          </ol>
          <div style={{ color: "var(--text-faint)", fontSize: 10 }}>
            Classic PATs (<code>ghp_…</code>) also work — give them <code>repo</code> scope.
          </div>
        </div>
      )}
    </span>
  );
}

function AddDialog({ onClose, onAdded }) {
  const [f, setF] = useState({ name: "", github_url: "", github_token: "", branch: "main", tech_stack: "", preview_url: "" });
  const [busy, setBusy] = useState(false);
  // Iter 49 — OAuth-first picker
  const [ghStatus, setGhStatus] = useState({ loading: true, connected: false, login: null });
  const [repos, setRepos] = useState([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [showManualPAT, setShowManualPAT] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await api.get("/github/oauth/status");
        if (!alive) return;
        setGhStatus({ loading: false, connected: !!r.data?.connected, login: r.data?.login || null });
        if (r.data?.connected) {
          setReposLoading(true);
          try {
            const rr = await api.get("/github/oauth/repos");
            if (alive) setRepos(rr.data?.repos || []);
          } catch { /* silent */ }
          finally { if (alive) setReposLoading(false); }
        }
      } catch {
        if (alive) setGhStatus({ loading: false, connected: false, login: null });
      }
    })();
    return () => { alive = false; };
  }, []);

  const up = (k, v) => setF((p) => ({ ...p, [k]: v }));

  function pickRepo(repo) {
    setF((p) => ({
      ...p,
      name: p.name || repo.name,
      github_url: repo.url,
      branch: repo.default_branch || "main",
    }));
  }

  // OAuth path: PAT not required. Manual path: PAT required.
  const canSubmit = f.name.trim() && f.github_url.trim() &&
    (ghStatus.connected || f.github_token.trim());

  async function submit(e) {
    e.preventDefault();
    if (!canSubmit) {
      toast({
        message: ghStatus.connected
          ? "Pick a repo first."
          : "Connect GitHub or paste a PAT to continue.",
        kind: "error",
      });
      return;
    }
    setBusy(true);
    try {
      // Don't send github_token at all on OAuth path — backend falls back.
      const payload = ghStatus.connected && !f.github_token.trim()
        ? { name: f.name, github_url: f.github_url, branch: f.branch,
            tech_stack: f.tech_stack, preview_url: f.preview_url }
        : f;
      await api.post("/cto/projects/add", payload);
      toast({ message: `Connected ${f.name}`, kind: "success" });
      onAdded();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Connect failed", kind: "error" });
    } finally { setBusy(false); }
  }

  function startOAuth() {
    // Read JWT from localStorage and pass via ?auth= so the redirect
    // works even when the browser strips Authorization headers.
    const token = localStorage.getItem("aurem_token") || localStorage.getItem("token") || "";
    // Use window.location.origin so the OAuth callback comes back to
    // whichever domain the user is actually on (preview, auremcto.com,
    // custom domain) rather than the build-time backend URL.
    const base = window.location.origin;
    window.location.href = `${base}/api/aurem-dev/github/oauth/connect?auth=${encodeURIComponent(token)}`;
  }

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 9000, background: "rgba(0,0,0,0.65)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
    }}>
      <form onSubmit={submit} onClick={(e) => e.stopPropagation()} data-testid="proj-add-dialog"
            style={{ maxWidth: 540, width: "100%", padding: 24,
                     background: "var(--panel)", border: "1px solid var(--border-strong)",
                     borderRadius: 6, display: "grid", gap: 12 }}>
        <h3 className="serif" style={{ margin: 0, fontSize: 18 }}>Connect a repo</h3>

        {/* Iter 49 — OAuth-first row */}
        {ghStatus.loading ? null : ghStatus.connected ? (
          <div data-testid="oauth-connected-banner" style={{
            padding: 10, borderRadius: 4, fontSize: 12,
            background: "rgba(34, 197, 94, 0.08)",
            border: "1px solid rgba(34, 197, 94, 0.3)",
            color: "var(--text)",
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <Github size={14} />
            <span>Connected as <strong>@{ghStatus.login}</strong> — pick a repo below.</span>
          </div>
        ) : (
          <button type="button" data-testid="oauth-connect-cta"
            onClick={startOAuth} style={{
              padding: "12px 14px", borderRadius: 4,
              background: "var(--accent-2)", color: "var(--bg)",
              border: "1px solid var(--accent-2)", fontWeight: 600, fontSize: 13,
              cursor: "pointer", display: "flex", alignItems: "center",
              gap: 8, justifyContent: "center",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
            }}>
            <Github size={14} /> Continue with GitHub
          </button>
        )}

        <label><span className="label-mini">Project name</span>
          <input data-testid="proj-name" className="input" required value={f.name}
                 onChange={(e) => up("name", e.target.value)} placeholder="My App" /></label>

        {ghStatus.connected && repos.length > 0 ? (
          <label><span className="label-mini">Pick repo</span>
            <select
              data-testid="proj-repo-picker"
              className="input"
              value={f.github_url}
              onChange={(e) => {
                const r = repos.find((x) => x.url === e.target.value);
                if (r) pickRepo(r);
              }}
            >
              <option value="">— select —</option>
              {repos.map((r) => (
                <option key={r.full_name} value={r.url}>
                  {r.full_name} {r.private ? "🔒" : ""}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label><span className="label-mini">GitHub URL</span>
            <input data-testid="proj-url" className="input" required value={f.github_url}
                   onChange={(e) => up("github_url", e.target.value)}
                   placeholder="https://github.com/owner/repo" /></label>
        )}

        {ghStatus.connected && reposLoading && (
          <div style={{ fontSize: 11, color: "var(--text-faint)" }}>Loading your repos…</div>
        )}

        {/* Manual PAT only shown when not connected, or explicitly toggled */}
        {(!ghStatus.connected || showManualPAT) && (
          <label>
            <span className="label-mini" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              Personal Access Token (PAT)
              {!ghStatus.connected && <span style={{ color: "var(--danger)" }}>*required</span>}
              <PatHelpTooltip />
            </span>
            <input data-testid="proj-pat" className="input" value={f.github_token}
                   onChange={(e) => up("github_token", e.target.value)}
                   required={!ghStatus.connected}
                   placeholder="github_pat_xxx or ghp_xxx" type="password" />
          </label>
        )}
        {ghStatus.connected && !showManualPAT && (
          <button type="button" onClick={() => setShowManualPAT(true)} style={{
            background: "transparent", border: "none",
            color: "var(--text-faint)", textAlign: "left",
            cursor: "pointer", fontSize: 11,
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            Use a Personal Access Token instead →
          </button>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12 }}>
          <label><span className="label-mini">Branch</span>
            <input data-testid="proj-branch" className="input" value={f.branch}
                   onChange={(e) => up("branch", e.target.value)} /></label>
          <label><span className="label-mini">Tech (optional)</span>
            <input data-testid="proj-tech" className="input" value={f.tech_stack}
                   onChange={(e) => up("tech_stack", e.target.value)}
                   placeholder="WordPress, Next.js, FastAPI…" /></label>
        </div>
        <label>
          <span className="label-mini">Live preview URL (optional)</span>
          <input data-testid="proj-preview-url" className="input" value={f.preview_url}
                 onChange={(e) => up("preview_url", e.target.value)}
                 placeholder="https://yoursite.com or http://localhost:3000" />
        </label>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" data-testid="proj-add-submit" className="btn-primary" disabled={busy || !canSubmit}>
            <Github size={13} /> {busy ? "Connecting…" : "Connect"}
          </button>
        </div>
      </form>
    </div>
  );
}

function EditDialog({ project, onClose, onSaved }) {
  const [f, setF] = useState({ github_token: "", branch: project.branch || "main", tech_stack: project.tech_stack || "", preview_url: project.preview_url || "" });
  const [busy, setBusy] = useState(false);
  const up = (k, v) => setF((p) => ({ ...p, [k]: v }));
  async function submit(e) {
    e.preventDefault();
    if (!f.github_token.trim() && !f.branch.trim() && !f.tech_stack.trim() && !f.preview_url.trim()) {
      toast({ message: "Nothing to update — fill at least one field.", kind: "warn" });
      return;
    }
    setBusy(true);
    try {
      await api.patch(`/cto/projects/${project.project_id}`, f);
      toast({ message: "Project updated", kind: "success" });
      onSaved();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Update failed", kind: "error" });
    } finally { setBusy(false); }
  }
  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 9000, background: "rgba(0,0,0,0.65)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
    }}>
      <form onSubmit={submit} onClick={(e) => e.stopPropagation()} data-testid="proj-edit-dialog"
            style={{ maxWidth: 500, width: "100%", padding: 24,
                     background: "var(--panel)", border: "1px solid var(--border-strong)",
                     borderRadius: 6, display: "grid", gap: 12 }}>
        <h3 className="serif" style={{ margin: 0, fontSize: 18 }}>
          Edit · {project.name}
        </h3>
        <p style={{ fontSize: 12, color: "var(--text-dim)", margin: 0 }}>
          {project.github_owner}/{project.github_repo}
        </p>
        <label>
          <span className="label-mini" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            Update PAT <PatHelpTooltip />
          </span>
          <input data-testid="proj-edit-pat" className="input" type="password"
                 value={f.github_token} onChange={(e) => up("github_token", e.target.value)}
                 placeholder="Leave blank to keep current PAT" />
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12 }}>
          <label><span className="label-mini">Branch</span>
            <input data-testid="proj-edit-branch" className="input" value={f.branch}
                   onChange={(e) => up("branch", e.target.value)} /></label>
          <label><span className="label-mini">Tech</span>
            <input data-testid="proj-edit-tech" className="input" value={f.tech_stack}
                   onChange={(e) => up("tech_stack", e.target.value)} /></label>
        </div>
        <label>
          <span className="label-mini">Live preview URL</span>
          <input data-testid="proj-edit-preview-url" className="input" value={f.preview_url}
                 onChange={(e) => up("preview_url", e.target.value)}
                 placeholder="https://yoursite.com or http://localhost:3000" />
        </label>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" data-testid="proj-edit-save" className="btn-primary" disabled={busy}>
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ProjectDetail({ project, onRemoved, onChanged }) {
  const [task, setTask] = useState("");
  const [files, setFiles] = useState("");
  const [context, setContext] = useState("");
  const [tasks, setTasks] = useState([]);
  const [activeTaskId, setActiveTaskId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showEdit, setShowEdit] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await api.get(`/cto/tasks/project/${project.project_id}`);
      setTasks(r.data?.tasks || []);
    } catch {}
  }, [project.project_id]);

  useEffect(() => { refresh(); }, [refresh]);

  // Poll the active task every 2s until done/failed
  useEffect(() => {
    if (!activeTaskId) return;
    const id = setInterval(async () => {
      try {
        const r = await api.get(`/cto/tasks/${activeTaskId}`);
        const t = r.data?.task;
        setTasks((cur) => {
          const exists = cur.find((x) => x.task_id === t.task_id);
          return exists ? cur.map((x) => (x.task_id === t.task_id ? t : x)) : [t, ...cur];
        });
        if (t && ["done", "failed"].includes(t.status)) {
          // If a rollback is in flight, keep polling until it settles
          const rb = t.rollback_status;
          if (rb !== "queued" && rb !== "running") {
            setActiveTaskId(null);
          }
        }
      } catch {}
    }, 2000);
    return () => clearInterval(id);
  }, [activeTaskId]);

  async function submit(e) {
    e.preventDefault();
    if (!task.trim()) return;
    setBusy(true);
    try {
      const fileList = files.split(",").map((s) => s.trim()).filter(Boolean);
      const r = await api.post("/cto/tasks/submit", {
        project_id: project.project_id, task: task.trim(),
        files: fileList, context: context.trim(),
      });
      setTask(""); setFiles(""); setContext("");
      setActiveTaskId(r.data.task_id);
      toast({ message: "Task queued — pulling repo…", kind: "info" });
      await refresh();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Submit failed", kind: "error" });
    } finally { setBusy(false); }
  }

  async function remove() {
    if (!window.confirm(`Remove project "${project.name}"?`)) return;
    try {
      await api.delete(`/cto/projects/${project.project_id}`);
      toast({ message: "Project removed", kind: "info" });
      onRemoved();
    } catch (e) {
      toast({ message: "Remove failed", kind: "error" });
    }
  }

  async function rollbackTask(t) {
    try {
      const r = await api.post(`/cto/tasks/${t.task_id}/rollback`, { confirm: "ROLLBACK" });
      toast({ message: "Rollback queued — reverting commit…", kind: "info" });
      setActiveTaskId(r.data.task_id);
      await refresh();
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Rollback failed to start",
        kind: "error",
      });
    }
  }

  return (
    <div data-testid="proj-detail" style={{ display: "grid", gap: 18 }}>
      <div className="card">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <div>
            <h3 className="serif" style={{ margin: 0, fontSize: 18 }}>{project.name}</h3>
            <a href={project.github_url} target="_blank" rel="noreferrer"
               style={{ fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
              {project.github_owner}/{project.github_repo}@{project.branch} <ExternalLink size={9} />
            </a>
          </div>
          <button data-testid="proj-edit" onClick={() => setShowEdit(true)} className="btn-ghost"
                  style={{ padding: "6px 10px", fontSize: 11, marginRight: 6 }}>
            <Pencil size={11} /> Edit
          </button>
          <button data-testid="proj-remove" onClick={remove} className="btn-ghost"
                  style={{ borderColor: "rgba(255,107,107,0.3)", color: "var(--danger)", padding: "6px 10px", fontSize: 11 }}>
            <Trash2 size={11} /> Remove
          </button>
        </div>

        <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
          <label><span className="label-mini">Task (plain English)</span>
            <textarea data-testid="task-input" className="input" rows={2}
                      value={task} onChange={(e) => setTask(e.target.value)}
                      placeholder="Fix the JWT bug in auth.py and add a /health endpoint"
                      style={{ resize: "none", fontFamily: "'Jost', sans-serif" }} /></label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <label><span className="label-mini">Files (comma-sep, optional)</span>
              <input data-testid="task-files" className="input" value={files} onChange={(e) => setFiles(e.target.value)}
                     placeholder="src/auth.py, src/routes.py" /></label>
            <label><span className="label-mini">Extra context (optional)</span>
              <input data-testid="task-context" className="input" value={context} onChange={(e) => setContext(e.target.value)}
                     placeholder="Error: 401 on /login" /></label>
          </div>
          <button type="submit" data-testid="task-submit" className="btn-primary" disabled={busy || !task.trim()}>
            <Send size={13} /> {busy ? "Queuing…" : "Run task"}
          </button>
        </form>
      </div>

      {/* Iter 147 — Hosted deploy widget moved to the /deploy page so all
          deploy-related controls live in one window. To manage hosted
          deploys for this project, go to Sidebar → Deploy. */}

      <div className="card" data-testid="task-history">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <span className="eyebrow">recent tasks</span>
          <button onClick={refresh} className="btn-ghost" style={{ padding: "4px 10px", fontSize: 11 }}>
            <RefreshCw size={11} /> Refresh
          </button>
        </div>
        {tasks.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--text-faint)" }}>No tasks yet.</p>
        ) : (
          tasks.map((t) => <TaskRow key={t.task_id} t={t} onRollback={rollbackTask} />)
        )}
      </div>

      {showEdit && (
        <EditDialog
          project={project}
          onClose={() => setShowEdit(false)}
          onSaved={() => { setShowEdit(false); onChanged?.(); }}
        />
      )}
    </div>
  );
}

function TaskRow({ t, onRollback }) {
  const [open, setOpen] = useState(["pulling", "reading", "fixing", "pushing"].includes(t.status));
  const STATUS_COLOR = {
    queued: "var(--text-faint)", pulling: "#60a5fa", reading: "#60a5fa",
    fixing: "var(--accent-2)", pushing: "var(--accent-2)",
    done: "var(--ok)", failed: "var(--danger)",
  };
  const color = STATUS_COLOR[t.status] || "var(--text-faint)";
  const running = !["done", "failed"].includes(t.status);
  const rbStatus = t.rollback_status;
  const rbRunning = rbStatus === "queued" || rbStatus === "running";
  const canRollback = (
    t.status === "done"
    && t.commit_sha
    && !t.rollback_sha
    && !rbRunning
    && rbStatus !== "failed"
  );

  async function handleRollback(e) {
    e.stopPropagation();
    // Two-step confirmation — guard against accidental clicks
    const ok1 = window.confirm(
      `Rollback commit ${t.commit_sha}?\n\n` +
      `This will create a new "Revert" commit on the project repo. ` +
      `Original history is preserved (no force-push).`
    );
    if (!ok1) return;
    const ok2 = window.confirm(
      `Are you sure?\n\n` +
      `AUREM CTO will push a revert of ${t.commit_sha} to the remote branch right now. ` +
      `Click OK to proceed.`
    );
    if (!ok2) return;
    onRollback?.(t);
  }

  // Iter 68 — Per-row action helpers (view commit, copy task id).
  // Visible always (not hover-only) since the user reported "no options
  // showing" — they couldn't discover the hover affordance.
  function copyTaskId() {
    navigator.clipboard?.writeText(t.task_id);
  }
  const commitUrl = t.commit_sha && t.project_id
    ? `https://github.com/${t.github_owner || ""}/${t.github_repo || ""}/commit/${t.commit_sha}`
    : null;

  return (
    <div data-testid={`task-row-${t.task_id}`} style={{
      borderTop: "1px solid var(--border)", padding: "10px 0",
      minWidth: 0,
    }}>
      <div onClick={() => setOpen((v) => !v)} style={{
        display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer",
        minWidth: 0,
      }}>
        <div style={{ flexShrink: 0, paddingTop: 2 }}>
          {running ? <Loader2 size={13} style={{ color, animation: "spin 1s linear infinite" }} />
                   : t.status === "done" ? <CheckCircle2 size={13} style={{ color }} />
                   : <AlertCircle size={13} style={{ color }} />}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Iter 68 — Wrap to 2 lines with ellipsis instead of single-line
              nowrap. Multi-line clamp keeps the row height predictable
              without truncating mid-word like the old "nowrap + ellipsis". */}
          <div style={{
            fontSize: 13, color: "var(--text)",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            overflowWrap: "anywhere",
            wordBreak: "break-word",
            lineHeight: 1.4,
          }}>
            {t.task}
          </div>
          <div style={{
            fontSize: 10, color, fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.1em", marginTop: 3,
            overflowWrap: "anywhere",
          }}>
            {t.status}{t.commit_sha ? ` · ${t.commit_sha}` : ""}
            {t.rollback_sha && (
              <span style={{ color: "var(--text-faint)", marginLeft: 8 }}>
                · reverted → {t.rollback_sha}
              </span>
            )}
            {rbRunning && (
              <span style={{ color: "var(--accent-2)", marginLeft: 8 }}>
                · rolling back…
              </span>
            )}
            {rbStatus === "failed" && (
              <span style={{ color: "var(--danger)", marginLeft: 8 }}>
                · rollback failed
              </span>
            )}
          </div>
        </div>
        {/* Action strip — flex-shrink:0 so it never gets pushed off-screen */}
        <div style={{
          display: "flex", alignItems: "center", gap: 4,
          flexShrink: 0,
        }} onClick={(e) => e.stopPropagation()}>
          {commitUrl && (
            <a
              href={commitUrl}
              target="_blank"
              rel="noreferrer"
              data-testid={`task-view-commit-${t.task_id}`}
              title="View commit on GitHub"
              className="btn-ghost"
              style={{
                padding: "4px 8px", fontSize: 11,
                textDecoration: "none",
              }}
            >
              <ExternalLink size={11} />
            </a>
          )}
          <button
            data-testid={`task-copy-id-${t.task_id}`}
            onClick={copyTaskId}
            title="Copy task ID"
            className="btn-ghost"
            style={{ padding: "4px 8px", fontSize: 11 }}
          >
            <CopyIcon size={11} />
          </button>
          {canRollback && (
            <button
              data-testid={`task-rollback-${t.task_id}`}
              onClick={handleRollback}
              title="Revert this commit on the remote repo"
              className="btn-ghost"
              style={{
                padding: "4px 10px", fontSize: 11,
                borderColor: "rgba(255,107,107,0.3)",
                color: "var(--danger)",
              }}
            >
              <Undo2 size={11} /> Rollback
            </button>
          )}
          {rbRunning && (
            <span data-testid={`task-rollback-status-${t.task_id}`}
                  style={{ fontSize: 11, color: "var(--accent-2)",
                           display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} />
              reverting…
            </span>
          )}
        </div>
      </div>
      {open && (
        <div style={{
          marginTop: 8, padding: 10,
          background: "var(--bg-elev)", borderRadius: 4,
          fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
          color: "var(--text-dim)", maxHeight: 220, overflowY: "auto",
        }}>
          {(t.steps || []).map((s, i) => (
            <div key={i} style={{ padding: "2px 0", color: s.status === "error" ? "var(--danger)" : s.status === "success" ? "var(--ok)" : "var(--text-dim)" }}>
              {s.step}
            </div>
          ))}
          {t.result && <div style={{ marginTop: 8, color: "var(--ok)" }}>→ {t.result}</div>}
          {t.error && <div style={{ marginTop: 8, color: "var(--danger)" }}>✗ {t.error}</div>}
          {(t.rollback_steps && t.rollback_steps.length > 0) && (
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
              <div style={{ color: "var(--accent-2)", marginBottom: 4 }}>
                ── rollback ──
              </div>
              {t.rollback_steps.map((s, i) => (
                <div key={i} style={{ padding: "2px 0", color: s.status === "error" ? "var(--danger)" : s.status === "success" ? "var(--ok)" : "var(--text-dim)" }}>
                  {s.step}
                </div>
              ))}
            </div>
          )}
          {t.rollback_error && <div style={{ marginTop: 8, color: "var(--danger)" }}>✗ rollback: {t.rollback_error}</div>}
        </div>
      )}
    </div>
  );
}
