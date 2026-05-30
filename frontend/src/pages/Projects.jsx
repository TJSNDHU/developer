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
      setProjects(r.data?.projects || []);
    } catch (e) {
      toast({ message: "Couldn't load projects", kind: "error" });
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 24, minHeight: 540 }}>
      <aside data-testid="proj-list" className="card" style={{ padding: 14, alignSelf: "start" }}>
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
          <ProjectDetail key={active.project_id} project={active} onRemoved={() => { setActive(null); refresh(); }} />
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

function AddDialog({ onClose, onAdded }) {
  const [f, setF] = useState({ name: "", github_url: "", github_token: "", branch: "main", tech_stack: "" });
  const [busy, setBusy] = useState(false);
  const up = (k, v) => setF((p) => ({ ...p, [k]: v }));
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/cto/projects/add", f);
      toast({ message: `Connected ${f.name}`, kind: "success" });
      onAdded();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Connect failed", kind: "error" });
    } finally { setBusy(false); }
  }
  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 9000, background: "rgba(0,0,0,0.65)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
    }}>
      <form onSubmit={submit} onClick={(e) => e.stopPropagation()} data-testid="proj-add-dialog"
            style={{ maxWidth: 500, width: "100%", padding: 24,
                     background: "var(--panel)", border: "1px solid var(--border-strong)",
                     borderRadius: 6, display: "grid", gap: 12 }}>
        <h3 className="serif" style={{ margin: 0, fontSize: 18 }}>Connect client repo</h3>
        <label><span className="label-mini">Project name</span>
          <input data-testid="proj-name" className="input" required value={f.name} onChange={(e) => up("name", e.target.value)} placeholder="Salon Website" /></label>
        <label><span className="label-mini">GitHub URL</span>
          <input data-testid="proj-url" className="input" required value={f.github_url} onChange={(e) => up("github_url", e.target.value)} placeholder="https://github.com/owner/repo" /></label>
        <label><span className="label-mini">PAT (optional — falls back to your OAuth token)</span>
          <input data-testid="proj-pat" className="input" value={f.github_token} onChange={(e) => up("github_token", e.target.value)} placeholder="ghp_..." type="password" /></label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12 }}>
          <label><span className="label-mini">Branch</span>
            <input data-testid="proj-branch" className="input" value={f.branch} onChange={(e) => up("branch", e.target.value)} /></label>
          <label><span className="label-mini">Tech (optional)</span>
            <input data-testid="proj-tech" className="input" value={f.tech_stack} onChange={(e) => up("tech_stack", e.target.value)} placeholder="WordPress, Next.js, FastAPI…" /></label>
        </div>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" data-testid="proj-add-submit" className="btn-primary" disabled={busy}>
            <Github size={13} /> {busy ? "Connecting…" : "Connect"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ProjectDetail({ project, onRemoved }) {
  const [task, setTask] = useState("");
  const [files, setFiles] = useState("");
  const [context, setContext] = useState("");
  const [tasks, setTasks] = useState([]);
  const [activeTaskId, setActiveTaskId] = useState(null);
  const [busy, setBusy] = useState(false);

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
          setActiveTaskId(null);
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
          tasks.map((t) => <TaskRow key={t.task_id} t={t} />)
        )}
      </div>
    </div>
  );
}

function TaskRow({ t }) {
  const [open, setOpen] = useState(["pulling", "reading", "fixing", "pushing"].includes(t.status));
  const STATUS_COLOR = {
    queued: "var(--text-faint)", pulling: "#60a5fa", reading: "#60a5fa",
    fixing: "var(--accent-2)", pushing: "var(--accent-2)",
    done: "var(--ok)", failed: "var(--danger)",
  };
  const color = STATUS_COLOR[t.status] || "var(--text-faint)";
  const running = !["done", "failed"].includes(t.status);
  return (
    <div data-testid={`task-row-${t.task_id}`} style={{
      borderTop: "1px solid var(--border)", padding: "10px 0",
    }}>
      <div onClick={() => setOpen((v) => !v)} style={{
        display: "flex", alignItems: "center", gap: 10, cursor: "pointer",
      }}>
        {running ? <Loader2 size={13} style={{ color, animation: "spin 1s linear infinite" }} />
                 : t.status === "done" ? <CheckCircle2 size={13} style={{ color }} />
                 : <AlertCircle size={13} style={{ color }} />}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {t.task}
          </div>
          <div style={{ fontSize: 10, color, fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.1em" }}>
            {t.status}{t.commit_sha ? ` · ${t.commit_sha}` : ""}
          </div>
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
        </div>
      )}
    </div>
  );
}
