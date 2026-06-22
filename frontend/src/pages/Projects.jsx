/**
 * Projects.jsx — Multi-project CTO dashboard.
 *
 * Workflow:
 *   1. Connect a client's existing GitHub repo (PAT or OAuth-derived token)
 *   2. Pick a project → submit a natural-language task
 *   3. Background worker clones → AI edits → commits → pushes
 *   4. Live step log + commit SHA + task history
 */
import React, { useEffect, useState, useCallback, useRef } from "react";
import {
  Plus, FolderGit2, Github, Send, Trash2, Loader2,
  CheckCircle2, AlertCircle, RefreshCw, ExternalLink,
  Pencil, Info, Undo2, Copy as CopyIcon,
  Check, Lock, Key, ArrowRight,
} from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import RobotGuide, { RobotGuideKeyframes, escapeHtml, oraPulseRingStyle } from "../components/RobotGuide";

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
  // Iter 206 — per-project quick actions
  const [editingProject, setEditingProject] = useState(null);
  const [patProject, setPatProject] = useState(null);

  function openAdd() {
    // Always start fresh — never carry over a previously-selected
    // project into the "+ Add Project" flow.
    setActive(null);
    setShowAdd(true);
  }

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

  // Iter 206 — Dashboard "+" button (TabBar) deep-links here with
  // `?add=1`. We auto-open the Add Project dialog AND deselect any
  // currently-active project so the user always lands in a fresh
  // "create new" flow (never accidentally edits an existing one).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const gh = params.get("github");
    const wantsAdd = params.get("add") === "1";
    const patId = params.get("pat");
    if (!gh && !wantsAdd && !patId) return;

    if (wantsAdd) {
      setActive(null);
      setShowAdd(true);
      window.history.replaceState({}, "", "/projects");
      return;
    }
    // Iter 206 — `?pat=<projectId>` opens the PatModal directly for that
    // project (deep-linked from the chat-side "Add PAT" CTA).
    if (patId) {
      // Resolve once projects load
      const findAndOpen = () => {
        const p = (projects || []).find((x) => x.project_id === patId);
        if (p) {
          setPatProject(p);
          window.history.replaceState({}, "", "/projects");
        }
      };
      if (projects.length) {
        findAndOpen();
      } else {
        // wait one tick — refresh() will populate `projects`
        api.get("/cto/projects/list").then((r) => {
          const p = (r.data?.projects || []).find((x) => x.project_id === patId);
          if (p) {
            setPatProject(p);
            window.history.replaceState({}, "", "/projects");
          }
        }).catch(() => { /* silent */ });
      }
      return;
    }
    if (!gh) return;
    const reason = params.get("reason") || params.get("msg") || "";
    const msg = gh === "cancelled"
      ? `GitHub authorization cancelled${reason ? ` (${reason})` : ""}. You can paste a Personal Access Token instead.`
      : `GitHub connection failed${reason ? `: ${reason}` : ""}. Please retry or paste a Personal Access Token.`;
    toast({ message: msg, kind: "error" });
    setShowAdd(true);
    window.history.replaceState({}, "", "/projects");
  }, []);

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
          {/* Iter 198 — promote the +Add button to a primary accent
              CTA. Was `btn-ghost` (transparent, 11px) which users were
              missing entirely — they'd see "No projects yet" and not
              realise the way to add one was a tiny faint button. */}
          <button
            data-testid="proj-add-btn"
            onClick={openAdd}
            style={{
              padding: "6px 14px",
              fontSize: 12,
              fontWeight: 600,
              background: "var(--accent, #ff8a2a)",
              color: "#0a0e1a",
              border: "none",
              borderRadius: 6,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
            }}
          >
            <Plus size={14} /> Add Project
          </button>
        </div>
        {projects.length === 0 && (
          <div style={{
            padding: "18px 14px",
            background: "rgba(255,138,42,0.05)",
            border: "1px dashed rgba(255,138,42,0.25)",
            borderRadius: 8,
            textAlign: "center",
            marginTop: 4,
          }}>
            <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 12px",
                         lineHeight: 1.5 }}>
              No projects yet. Connect a GitHub repo to get started.
            </p>
            <button
              data-testid="proj-empty-add-btn"
              onClick={openAdd}
              style={{
                width: "100%",
                padding: "10px 12px",
                fontSize: 12,
                fontWeight: 600,
                background: "var(--accent, #ff8a2a)",
                color: "#0a0e1a",
                border: "none",
                borderRadius: 6,
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.04em",
              }}
            >
              <Plus size={14} /> Connect your first repo
            </button>
          </div>
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
                display: "flex", alignItems: "center", gap: 8, minWidth: 0,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, color: sel ? "var(--accent-2)" : "var(--text)" }}>
                  {p.name}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-faint)",
                              fontFamily: "'JetBrains Mono', monospace",
                              overflow: "hidden", textOverflow: "ellipsis",
                              whiteSpace: "nowrap" }}>
                  {p.github_owner}/{p.github_repo}
                  {p.tasks_done ? ` · ${p.tasks_done} tasks` : ""}
                </div>
              </div>
              {/* Iter 206 — per-row quick actions */}
              <button
                type="button"
                data-testid={`proj-row-pat-${p.project_id}`}
                title={p.has_pat ? "Update PAT" : "Add PAT"}
                onClick={(e) => { e.stopPropagation(); setPatProject(p); }}
                style={rowActionBtn(p.has_pat ? "#22c55e" : "#f59e0b")}
              >
                <Key size={12} />
                <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.04em" }}>
                  PAT
                </span>
              </button>
              <button
                type="button"
                data-testid={`proj-row-edit-${p.project_id}`}
                title="Edit project"
                onClick={(e) => { e.stopPropagation(); setEditingProject(p); }}
                style={rowActionBtn("var(--text-faint)")}
              >
                <Pencil size={12} />
              </button>
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
            <p style={{ marginBottom: 18 }}>Select or add a project to start submitting tasks.</p>
            {/* Iter 198 — large CTA in the right-pane empty state too,
                so first-time users have a second, much bigger entry
                point. The sidebar +Add button still works; this just
                makes the action impossible to miss. */}
            {projects.length === 0 && (
              <button
                data-testid="proj-empty-pane-add"
                onClick={openAdd}
                style={{
                  padding: "12px 22px",
                  fontSize: 13,
                  fontWeight: 600,
                  background: "var(--accent, #ff8a2a)",
                  color: "#0a0e1a",
                  border: "none",
                  borderRadius: 8,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  fontFamily: "'JetBrains Mono', monospace",
                  letterSpacing: "0.04em",
                }}
              >
                <Plus size={14} /> Add your first project
              </button>
            )}
          </div>
        )}
      </section>

      {showAdd && <AddDialog projects={projects} onClose={() => setShowAdd(false)} onAdded={() => { setShowAdd(false); refresh(); }} />}
      {editingProject && (
        <EditDialog
          project={editingProject}
          onClose={() => setEditingProject(null)}
          onSaved={() => { setEditingProject(null); refresh(); }}
        />
      )}
      {patProject && (
        <PatModal
          project={patProject}
          onClose={() => setPatProject(null)}
          onSaved={() => { setPatProject(null); refresh(); }}
        />
      )}
    </div>
  );
}

// Iter 206 — per-row PAT/Edit action button.
function rowActionBtn(color) {
  return {
    background: "transparent",
    border: "1px solid rgba(255,255,255,0.1)",
    color,
    padding: "5px 7px",
    borderRadius: 4,
    cursor: "pointer",
    display: "inline-flex", alignItems: "center", gap: 4,
    flexShrink: 0,
    transition: "background .15s, border-color .15s",
  };
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

function AddDialog({ onClose, onAdded, projects = [] }) {
  const [f, setF] = useState({ name: "", github_url: "", github_token: "", branch: "main", tech_stack: "", preview_url: "" });
  const [busy, setBusy] = useState(false);
  // Iter 49 — OAuth-first picker
  const [ghStatus, setGhStatus] = useState({ loading: true, connected: false, login: null });
  const [repos, setRepos] = useState([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [showManualPAT, setShowManualPAT] = useState(false);
  // Iter 199 — 2-step flow:
  //   step 1 = "Continue with GitHub" + how-it-works + PAT fallback
  //   step 2 = "Select a repository" + per-repo cards + Connect
  // Auto-advances to step 2 once /github/oauth/status reports connected.
  const [connectStep, setConnectStep] = useState(1);
  const [selectedRepo, setSelectedRepo] = useState(null);
  // Iter 204 — popup OAuth so the modal stays alive while the user
  // authorises on GitHub. Top-level redirects kicked the user back to
  // /settings, destroyed the modal, and the ORA robot guide vanished.
  const popupRef = useRef(null);
  const pollRef  = useRef(null);
  const [oauthBusy, setOauthBusy] = useState(false);
  // Iter 211 — PAT is required for every project, even when the repo
  // was chosen via the OAuth picker. Separate state from the Step-1
  // form so the OAuth path can collect it cleanly.
  const [repoPat, setRepoPat] = useState("");

  // Iter 212 — Debounced PAT verification.
  // After 800ms of no typing, if format is valid AND a repo is picked,
  // we POST /cto/projects/verify-pat (stateless GitHub check) and show
  // an inline pill. The Connect button stays disabled until `ok:true`,
  // so the user can't submit a bad token.
  const [patCheck, setPatCheck] = useState({
    status: "idle",   // 'idle' | 'loading' | 'ok' | 'error'
    error:  null,     // e.g. 'invalid_token' | 'missing_scope' | 'repo_not_found'
    detail: "",
    scopes: [],
  });
  useEffect(() => {
    const trimmed = (repoPat || "").trim();
    // Reset on no PAT / no repo.
    if (!trimmed || !selectedRepo) {
      setPatCheck({ status: "idle", error: null, detail: "", scopes: [] });
      return;
    }
    // Format gate — same regex the Connect handler uses.
    if (!/^(ghp_|github_pat_)/.test(trimmed) || trimmed.length < 20) {
      setPatCheck({
        status: "error",
        error:  "bad_format",
        detail: "PAT should start with ghp_ or github_pat_.",
        scopes: [],
      });
      return;
    }
    const id = setTimeout(async () => {
      setPatCheck({ status: "loading", error: null, detail: "", scopes: [] });
      try {
        const r = await api.post("/cto/projects/verify-pat", {
          repo: selectedRepo.full_name,
          pat:  trimmed,
        });
        const d = r.data || {};
        if (d.ok) {
          setPatCheck({
            status: "ok", error: null,
            detail: `Verified${d.scopes?.length ? ` — scopes: ${d.scopes.join(", ")}` : " — fine-grained PAT"}`,
            scopes: d.scopes || [],
          });
        } else {
          setPatCheck({
            status: "error", error: d.error || "unknown",
            detail: d.detail || "Verification failed.",
            scopes: d.has_scopes || [],
          });
        }
      } catch (e) {
        setPatCheck({
          status: "error", error: "network_error",
          detail: e?.response?.data?.detail || "Couldn't reach the verifier.",
          scopes: [],
        });
      }
    }, 800);
    return () => clearTimeout(id);
  }, [repoPat, selectedRepo]);

  // Iter 212 — show ALL repos in the picker (no filtering). Already-
  // connected repos are marked with a "Connected" pill and disabled
  // so the user sees the full list of their GitHub account rather
  // than hitting a confusing empty/"ALL SET" dead-end when every repo
  // happens to be connected already.
  const connectedKeys = new Set(
    (projects || []).map((p) =>
      `${(p.github_owner || "").toLowerCase()}/${(p.github_repo || "").toLowerCase()}`
    )
  );
  const availableRepos = repos; // keep variable name for backwards compat
  const isRepoConnected = (r) =>
    connectedKeys.has((r.full_name || "").toLowerCase());

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await api.get("/github/oauth/status");
        if (!alive) return;
        const connected = !!r.data?.connected;
        setGhStatus({ loading: false, connected, login: r.data?.login || null });
        // Iter 208 — DO NOT auto-advance to Step 2 just because the user
        // OAuth'd in a previous session. Always land on Step 1 ("Connect
        // a repo") so "+ Add Project" feels like a fresh flow every
        // time. The user explicitly clicks "Pick a repo" to advance.
      } catch {
        if (alive) setGhStatus({ loading: false, connected: false, login: null });
      }
    })();
    return () => { alive = false; };
  }, []);

  // Iter 208 — Step 1 → Step 2 advance for already-connected users.
  // Fetches the repo list lazily on click (instead of on mount) so
  // every "+ Add Project" click starts blank.
  async function advanceToRepoPicker() {
    setReposLoading(true);
    setConnectStep(2);
    try {
      const rr = await api.get("/github/oauth/repos");
      setRepos(rr.data?.repos || []);
    } catch {
      toast({ message: "Couldn't load your GitHub repos. Try reconnecting GitHub.", kind: "error" });
    } finally {
      setReposLoading(false);
    }
  }

  const up = (k, v) => setF((p) => ({ ...p, [k]: v }));

  async function submit(e) {
    e?.preventDefault?.();
    // PAT-fallback submit (step 1, no OAuth)
    if (!f.name.trim() || !f.github_url.trim() || !f.github_token.trim()) {
      toast({ message: "Project name, GitHub URL and PAT are all required.", kind: "error" });
      return;
    }
    setBusy(true);
    try {
      await api.post("/cto/projects/add", f);
      toast({ message: `Connected ${f.name}`, kind: "success" });
      onAdded();
    } catch (e2) {
      toast({ message: e2?.response?.data?.detail || "Connect failed", kind: "error" });
    } finally { setBusy(false); }
  }

  // OAuth path — submit straight from the repo card.
  async function handleConnectRepo() {
    if (!selectedRepo) return;
    const trimmedPat = (repoPat || "").trim();
    if (!trimmedPat) {
      toast({ message: "Paste a GitHub PAT for this repo first.", kind: "warn" });
      return;
    }
    if (!/^(ghp_|github_pat_)/.test(trimmedPat)) {
      toast({
        message: "PAT format invalid — should start with ghp_ or github_pat_",
        kind: "warn",
      });
      return;
    }
    setBusy(true);
    try {
      await api.post("/cto/projects/add", {
        name: selectedRepo.name,
        github_url: selectedRepo.url || `https://github.com/${selectedRepo.full_name}`,
        branch: selectedRepo.default_branch || "main",
        github_token: trimmedPat,
      });
      toast({ message: `Connected ${selectedRepo.name} — PAT verified ✓`, kind: "success" });
      onAdded();
    } catch (e2) {
      toast({ message: e2?.response?.data?.detail || "Connect failed", kind: "error" });
    } finally { setBusy(false); }
  }

  function startOAuth(forceReauth = false) {
    const token = localStorage.getItem("aurem_token") || localStorage.getItem("token") || "";
    if (!token) {
      toast({ message: "Session expired — please log in again.", kind: "error" });
      return;
    }
    // Iter 204 — open in a popup so we don't lose the modal. Backend's
    // /github/oauth/connect supports `?auth=` for cookieless flow.
    // Iter 212 — `force_reauth=1` appends `prompt=select_account` so
    // GitHub re-shows the authorize page (used by the Switch GitHub
    // account link).
    const base = window.location.origin;
    const qs = `auth=${encodeURIComponent(token)}` + (forceReauth ? "&force_reauth=1" : "");
    const url = `${base}/api/aurem-dev/github/oauth/connect?${qs}`;
    const w = 560, h = 720;
    const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
    const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    popupRef.current = window.open(
      url, "aurem_github_oauth",
      `width=${w},height=${h},left=${left},top=${top}`,
    );
    setOauthBusy(true);

    // Poll /github/oauth/status every 2 s until connected, popup closed,
    // or 90 s timeout.
    if (pollRef.current) clearInterval(pollRef.current);
    const started = Date.now();
    pollRef.current = setInterval(async () => {
      try {
        const r = await api.get("/github/oauth/status");
        if (r.data?.connected) {
          clearInterval(pollRef.current); pollRef.current = null;
          try { popupRef.current?.close?.(); } catch { /* xorigin */ }
          setGhStatus({ loading: false, connected: true, login: r.data?.login || null });
          setConnectStep(2);
          setReposLoading(true);
          setOauthBusy(false);
          try {
            const rr = await api.get("/github/oauth/repos");
            setRepos(rr.data?.repos || []);
          } catch { /* silent */ }
          finally { setReposLoading(false); }
        }
      } catch { /* keep polling */ }
      if (popupRef.current?.closed) {
        clearInterval(pollRef.current); pollRef.current = null;
        setOauthBusy(false);
      }
      if (Date.now() - started > 90_000) {
        clearInterval(pollRef.current); pollRef.current = null;
        setOauthBusy(false);
      }
    }, 2000);
  }

  // Cleanup polling on unmount so a closed modal doesn't keep hitting
  // /github/oauth/status in the background.
  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current);
    try { popupRef.current?.close?.(); } catch { /* xorigin */ }
  }, []);

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 9000, background: "rgba(0,0,0,0.65)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
    }}>
      <div onClick={(e) => e.stopPropagation()} data-testid="proj-add-dialog"
            style={{ maxWidth: 540, width: "100%", padding: 24,
                     background: "var(--panel)", border: "1px solid var(--border-strong)",
                     borderRadius: 10, display: "block" }}>
        <RobotGuideKeyframes />

        {/* ──────────── Step 1 — Connect (OAuth + PAT fallback) ──────────── */}
        {connectStep === 1 && (
          <div>
            <p style={{ fontSize: 18, fontWeight: 500, color: "var(--text)", margin: "0 0 6px" }}>
              Connect a repo
            </p>
            <p style={{ fontSize: 13, color: "var(--text-dim)", margin: "0 0 20px", lineHeight: 1.6 }}>
              ORA will read your code and commit fixes directly to GitHub.
            </p>

            <RobotGuide
              testid="proj-robot-guide"
              kind={busy ? "info" : "info"}
              message={
                ghStatus.loading
                  ? `Checking your GitHub connection…`
                  : oauthBusy
                    ? `Waiting for GitHub… <strong>complete the authorization in the popup</strong> and I&rsquo;ll show your repos here. <span class="ora-arrow">⏳</span>`
                    : busy
                      ? `Connecting… <span class="ora-arrow">⏳</span>`
                      : showManualPAT
                        ? `Manual mode — paste your <strong>Personal Access Token</strong> below. (Or click <strong>Continue with GitHub</strong> above to skip this.) <span class="ora-arrow">👇</span>`
                        : ghStatus.connected
                          ? `Welcome back! Your GitHub is already connected as <strong>@${escapeHtml(ghStatus.login || "you")}</strong>. Click <strong>Pick a repo</strong> below <span class="ora-arrow">👇</span> to choose a new one.`
                          : `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — opens in a popup, takes 10 seconds, no PAT needed.`
              }
            />

            {/* Iter 208 — primary CTA adapts to OAuth state:
                • not connected → "Continue with GitHub" (popup) flow
                • already connected → "Pick a repo" (advance to Step 2)
                Either way the user lands on Step 1 first, so every
                +Add click feels like a fresh start. */}
            <div style={{ position: "relative", marginBottom: 16 }}>
              {!showManualPAT && !ghStatus.loading && (
                <div data-testid="proj-pulse-ring" style={oraPulseRingStyle} />
              )}
              {ghStatus.connected && !showManualPAT ? (
                <button
                  type="button"
                  data-testid="oauth-pick-repo-cta"
                  onClick={advanceToRepoPicker}
                  disabled={reposLoading}
                  style={{
                    width: "100%", padding: 13,
                    background: "#f59e0b", color: "#0a0c10",
                    border: "none", borderRadius: 10,
                    fontSize: 14, fontWeight: 600,
                    cursor: reposLoading ? "wait" : "pointer",
                    display: "flex", alignItems: "center",
                    justifyContent: "center", gap: 10,
                    position: "relative", zIndex: 1,
                  }}>
                  {reposLoading ? <Loader2 size={16} className="animate-spin" /> : <Github size={18} />}
                  {reposLoading ? "Loading repos…" : `Pick a repo (connected as @${ghStatus.login || "you"})`}
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="oauth-connect-cta"
                  onClick={startOAuth}
                  style={{
                    width: "100%", padding: 13, background: "#24292e", color: "#fff",
                    border: showManualPAT ? "none" : "2px solid #f59e0b",
                    borderRadius: 10, fontSize: 14, fontWeight: 500,
                    cursor: "pointer", display: "flex", alignItems: "center",
                    justifyContent: "center", gap: 10,
                    position: "relative", zIndex: 1,
                  }}>
                  <Github size={18} /> Continue with GitHub
                </button>
              )}
            </div>

            {/* Iter 212 — Switch GitHub account.
                Lets the user trigger a fresh OAuth popup to authorize a
                different GitHub account. Critical for builders managing
                multiple client orgs — without this, the modal kept
                reusing the cached @<previous> login. */}
            {ghStatus.connected && !showManualPAT && (
              <div style={{ textAlign: "center", marginBottom: 12 }}>
                <button
                  type="button"
                  data-testid="oauth-switch-account-link"
                  onClick={() => startOAuth(true)}
                  disabled={oauthBusy}
                  style={{
                    background: "none", border: "none",
                    color: "var(--text-faint)",
                    fontSize: 11, cursor: oauthBusy ? "wait" : "pointer",
                    textDecoration: "underline",
                  }}>
                  {oauthBusy
                    ? "Waiting for GitHub popup…"
                    : `Switch GitHub account (currently @${ghStatus.login || "you"})`}
                </button>
              </div>
            )}

            {/* repo access info box */}
            <div style={{
              background: "var(--bg-elev, rgba(255,255,255,0.03))",
              borderRadius: 8, padding: "12px 14px", marginBottom: 16,
              display: "flex", gap: 10, alignItems: "flex-start",
              border: "0.5px solid var(--border, rgba(255,255,255,0.08))",
            }}>
              <Info size={16} style={{ color: "#f59e0b", flexShrink: 0, marginTop: 1 }} />
              <p style={{ fontSize: 12, color: "var(--text-dim)", margin: 0, lineHeight: 1.6 }}>
                ORA only requests <strong style={{ color: "var(--text)" }}>repo access</strong> —
                read your files and commit code. We never touch your GitHub account settings or
                other data.
              </p>
            </div>

            {/* How it works */}
            <div style={{
              border: "0.5px solid var(--border, rgba(255,255,255,0.08))",
              borderRadius: 8, overflow: "hidden", marginBottom: 16,
            }}>
              <div style={{
                padding: "10px 14px",
                background: "var(--bg-elev, rgba(255,255,255,0.03))",
                borderBottom: "0.5px solid var(--border, rgba(255,255,255,0.08))",
              }}>
                <p style={{ fontSize: 12, fontWeight: 500, color: "var(--text-dim)", margin: 0 }}>
                  How it works
                </p>
              </div>
              <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
                {[
                  "Click Continue with GitHub above",
                  "Authorize ORA on GitHub — takes 10 seconds",
                  "Select your repo — ORA starts reading it immediately",
                ].map((text, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div style={{
                      width: 22, height: 22, borderRadius: "50%",
                      background: "rgba(245,158,11,0.15)",
                      display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
                    }}>
                      <span style={{ fontSize: 11, fontWeight: 500, color: "#f59e0b" }}>{i + 1}</span>
                    </div>
                    <p style={{ fontSize: 12, color: "var(--text-dim)", margin: 0, lineHeight: 1.5 }}>
                      {text}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            {/* PAT fallback */}
            {!showManualPAT ? (
              <p style={{ textAlign: "center", margin: "0 0 16px" }}>
                <button
                  type="button"
                  data-testid="proj-pat-fallback-toggle"
                  onClick={() => setShowManualPAT(true)}
                  style={{
                    background: "none", border: "none", color: "var(--text-faint)",
                    fontSize: 11, cursor: "pointer", textDecoration: "underline",
                  }}>
                  Can&apos;t use GitHub OAuth? Use a token instead
                </button>
              </p>
            ) : (
              <form onSubmit={submit}
                    style={{
                      borderTop: "0.5px solid var(--border, rgba(255,255,255,0.08))",
                      paddingTop: 16, marginBottom: 16, display: "grid", gap: 10,
                    }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 2 }}>
                  <label style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)",
                                   letterSpacing: "0.06em", textTransform: "uppercase" }}>
                    Personal Access Token
                  </label>
                  <a href="https://github.com/settings/tokens/new?scopes=repo&description=ORA+by+Aurem+CTO"
                     target="_blank" rel="noreferrer"
                     style={{ fontSize: 11, color: "#f59e0b", textDecoration: "none",
                              display: "inline-flex", alignItems: "center", gap: 4 }}>
                    Generate token <ExternalLink size={11} />
                  </a>
                </div>
                <input data-testid="proj-pat" className="input" value={f.github_token}
                       onChange={(e) => up("github_token", e.target.value)}
                       required placeholder="github_pat_xxx or ghp_xxx" type="password" />
                <input data-testid="proj-name" className="input" required value={f.name}
                       onChange={(e) => up("name", e.target.value)} placeholder="Project name" />
                <input data-testid="proj-url" className="input" required value={f.github_url}
                       onChange={(e) => up("github_url", e.target.value)}
                       placeholder="https://github.com/owner/repo" />
                <p style={{ fontSize: 11, color: "var(--text-faint)", margin: "2px 0 6px", lineHeight: 1.5 }}>
                  GitHub → Settings → Developer settings → Personal access tokens → Select{" "}
                  <strong>repo</strong> scope → Generate
                </p>
                <button type="submit" disabled={busy}
                        style={{
                          padding: "10px 0", background: "#f59e0b", color: "#0a0e1a",
                          border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600,
                          cursor: busy ? "default" : "pointer",
                          fontFamily: "'JetBrains Mono', monospace",
                        }}>
                  {busy ? "Connecting…" : "Connect with token"}
                </button>
              </form>
            )}

            <div style={{
              display: "flex", gap: 8, paddingTop: 16,
              borderTop: "0.5px solid var(--border, rgba(255,255,255,0.08))",
            }}>
              <button type="button" onClick={onClose} className="btn-ghost"
                      style={{ flex: 1, padding: "10px 0", fontSize: 12 }}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* ──────────── Step 2 — Repo selector ──────────── */}
        {connectStep === 2 && (
          <div>
            <div data-testid="oauth-connected-banner"
                 style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
              <div style={{
                width: 20, height: 20, borderRadius: "50%",
                background: "rgba(34,197,94,0.15)",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                <Check size={12} style={{ color: "#22c55e" }} />
              </div>
              <p style={{ fontSize: 13, color: "#22c55e", margin: 0, fontWeight: 500 }}>
                GitHub connected as @{ghStatus.login}
              </p>
            </div>

            <p style={{ fontSize: 18, fontWeight: 500, color: "var(--text)", margin: "0 0 6px" }}>
              Select a repository
            </p>
            <p style={{ fontSize: 13, color: "var(--text-dim)", margin: "0 0 16px" }}>
              Choose which repo ORA should work on.
            </p>

            <RobotGuide
              testid="proj-robot-guide-step2"
              kind={busy ? "info" : "success"}
              message={
                reposLoading
                  ? `Loading your GitHub repos…`
                  : busy
                    ? `Connecting <strong>${escapeHtml(selectedRepo?.name || "repo")}</strong>… <span class="ora-arrow">⏳</span>`
                    : selectedRepo
                      ? patCheck.status === "ok"
                        ? `Token verified ✓ — hit <strong>Connect repo</strong> below and I&rsquo;ll wire <strong>${escapeHtml(selectedRepo.full_name || selectedRepo.name)}</strong> up. <span class="ora-arrow">👇</span>`
                        : patCheck.status === "loading"
                          ? `Checking your token against <strong>${escapeHtml(selectedRepo.full_name || selectedRepo.name)}</strong>… <span class="ora-arrow">⏳</span>`
                          : patCheck.status === "error"
                            ? `Token didn&rsquo;t pass — ${escapeHtml(patCheck.detail || "try a fresh one")}. Generate a new PAT above <span class="ora-arrow">👆</span> and paste again.`
                            : `Nice — <strong>${escapeHtml(selectedRepo.full_name || selectedRepo.name)}</strong> picked. Now click <strong>Open GitHub → Create PAT</strong> below <span class="ora-arrow">👇</span>, set <strong>Contents: Read &amp; Write</strong>, and paste the token here.`
                      : availableRepos.length === 0
                        ? `No repos visible to this token. Re-authorize GitHub with broader access, or <strong>Switch GitHub account</strong> from Step 1.`
                        : `Connected as <strong>@${escapeHtml(ghStatus.login || "you")}</strong>! Pick a repo below <span class="ora-arrow">👇</span> — then I&rsquo;ll walk you through creating a PAT.`
              }
            />

            {reposLoading && (
              <div style={{ fontSize: 12, color: "var(--text-faint)", padding: "8px 0" }}>
                Loading your repos…
              </div>
            )}

            <div data-testid="proj-repo-picker"
                 style={{ display: "flex", flexDirection: "column", gap: 8,
                          marginBottom: 16, maxHeight: 260, overflowY: "auto" }}>
              {availableRepos.map((repo) => {
                const isSel = selectedRepo?.full_name === repo.full_name;
                const isConn = isRepoConnected(repo);
                return (
                  <button
                    key={repo.full_name}
                    type="button"
                    data-testid={`proj-repo-row-${repo.full_name}`}
                    disabled={isConn}
                    onClick={() => !isConn && setSelectedRepo(repo)}
                    title={isConn ? "Already connected as a project" : ""}
                    style={{
                      width: "100%", padding: "12px 14px",
                      background: isSel ? "rgba(245,158,11,0.06)"
                                        : "var(--bg-elev, rgba(255,255,255,0.03))",
                      border: isSel ? "2px solid #f59e0b"
                                    : "0.5px solid var(--border, rgba(255,255,255,0.08))",
                      borderRadius: 8,
                      cursor: isConn ? "not-allowed" : "pointer",
                      opacity: isConn ? 0.45 : 1,
                      display: "flex", alignItems: "center", gap: 10, textAlign: "left",
                    }}>
                    {repo.private
                      ? <Lock size={16} style={{ color: "var(--text-dim)" }} />
                      : <FolderGit2 size={16} style={{ color: "var(--text-dim)" }} />}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p style={{ fontSize: 13, fontWeight: 500, color: "var(--text)",
                                   margin: 0, whiteSpace: "nowrap", overflow: "hidden",
                                   textOverflow: "ellipsis" }}>
                        {repo.name}
                      </p>
                      <p style={{ fontSize: 11, color: "var(--text-faint)", margin: 0,
                                   fontFamily: "'JetBrains Mono', monospace" }}>
                        {repo.full_name}{" · "}
                        {repo.private ? "private · " : ""}
                        {repo.default_branch || "main"}
                      </p>
                    </div>
                    {isConn ? (
                      <span style={{
                        fontSize: 10, fontWeight: 600, color: "#22c55e",
                        background: "rgba(34,197,94,0.12)",
                        border: "0.5px solid rgba(34,197,94,0.3)",
                        padding: "3px 8px", borderRadius: 999,
                        fontFamily: "'JetBrains Mono', monospace",
                        letterSpacing: "0.04em", textTransform: "uppercase",
                      }}>Connected</span>
                    ) : (
                      isSel && <Check size={16} style={{ color: "#f59e0b" }} />
                    )}
                  </button>
                );
              })}
              {!reposLoading && !repos.length && (
                <div style={{ fontSize: 12, color: "var(--text-faint)",
                               padding: "16px 0", textAlign: "center" }}>
                  No repos visible to this token. Re-authorize with broader access.
                </div>
              )}
            </div>

            {selectedRepo && (
              <div style={{
                background: "rgba(245,158,11,0.06)",
                border: "0.5px solid rgba(245,158,11,0.2)",
                borderRadius: 8, padding: "10px 14px", marginBottom: 12,
                display: "flex", alignItems: "center", gap: 8,
              }}>
                <Info size={14} style={{ color: "#f59e0b", flexShrink: 0 }} />
                <p style={{ fontSize: 12, color: "var(--text-dim)", margin: 0, lineHeight: 1.5 }}>
                  ORA will read{" "}
                  <strong style={{ color: "var(--text)" }}>{selectedRepo.name}</strong>{" "}
                  and commit fixes directly to{" "}
                  <strong style={{ color: "var(--text)" }}>
                    {selectedRepo.default_branch || "main"}
                  </strong>.
                </p>
              </div>
            )}

            {/* Iter 212 — PAT entry surface inside Step 2.
                After repo is picked the user MUST paste a fine-grained
                or classic PAT scoped to this repo. Robot guide above
                walks them through the GitHub flow; the big amber CTA
                deep-links straight to GitHub's PAT creation page with
                the project name pre-filled. */}
            {selectedRepo && (
              <div data-testid="proj-step2-pat-block"
                   style={{ display: "grid", gap: 10, marginBottom: 14 }}>
                <a
                  href={
                    "https://github.com/settings/personal-access-tokens/new" +
                    "?name=" + encodeURIComponent(`ORA · ${selectedRepo.name}`) +
                    "&description=" + encodeURIComponent("AUREM CTO (ORA) — read & commit on this repo.") +
                    "&expiration=" + encodeURIComponent("90")
                  }
                  target="_blank" rel="noopener noreferrer"
                  data-testid="proj-step2-pat-github-link"
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
                    padding: 13, background: "#24292e", color: "#fff",
                    border: "2px solid #f59e0b", borderRadius: 10,
                    textDecoration: "none", fontSize: 14, fontWeight: 500,
                  }}
                >
                  <Github size={18} />
                  Open GitHub → Create PAT
                  <ExternalLink size={13} style={{ opacity: 0.7 }} />
                </a>

                <ol style={{
                  margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.6,
                  color: "var(--text-dim, #94a3b8)",
                }}>
                  <li><strong style={{ color: "var(--text)" }}>Repository access:</strong> select <em>Only select repositories</em> → pick <code style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", padding: "1px 6px", borderRadius: 4, fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{selectedRepo.full_name}</code>.</li>
                  <li><strong style={{ color: "var(--text)" }}>Permissions:</strong> under <em>Repository permissions</em> set <code style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", padding: "1px 6px", borderRadius: 4, fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>Contents: Read and write</code>.</li>
                  <li>Click <em>Generate token</em>, copy it, and paste below.</li>
                </ol>

                <label style={{ display: "grid", gap: 6 }}>
                  <span style={{ fontSize: 11, color: "var(--text-dim)",
                                  fontFamily: "'JetBrains Mono', monospace",
                                  letterSpacing: "0.04em", textTransform: "uppercase" }}>
                    Paste your PAT
                  </span>
                  <input
                    data-testid="proj-step2-pat-input"
                    type="password"
                    autoComplete="off" autoCorrect="off" spellCheck={false}
                    value={repoPat}
                    onChange={(e) => setRepoPat(e.target.value)}
                    placeholder="github_pat_xxx or ghp_xxx"
                    className="input"
                    style={{
                      fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
                      // Iter 212 — color the border by verification status so
                      // builders see green/red feedback instantly.
                      borderColor:
                        patCheck.status === "ok"      ? "rgba(34,197,94,0.5)"  :
                        patCheck.status === "error"   ? "rgba(239,68,68,0.5)"  :
                        patCheck.status === "loading" ? "rgba(245,158,11,0.4)" :
                        undefined,
                    }}
                  />

                  {/* Iter 212 — inline verification pill, debounced 800ms. */}
                  {patCheck.status === "loading" && (
                    <div data-testid="proj-pat-verify-loading"
                         style={{
                           fontSize: 11, color: "#94a3b8",
                           display: "inline-flex", alignItems: "center", gap: 6,
                           padding: "4px 10px",
                           background: "rgba(255,255,255,0.04)",
                           border: "0.5px solid rgba(255,255,255,0.12)",
                           borderRadius: 999, alignSelf: "flex-start",
                         }}>
                      <Loader2 size={11} className="animate-spin" />
                      Checking token…
                    </div>
                  )}
                  {patCheck.status === "ok" && (
                    <div data-testid="proj-pat-verify-ok"
                         style={{
                           fontSize: 11, color: "#22c55e",
                           display: "inline-flex", alignItems: "center", gap: 6,
                           padding: "4px 10px",
                           background: "rgba(34,197,94,0.10)",
                           border: "0.5px solid rgba(34,197,94,0.35)",
                           borderRadius: 999, alignSelf: "flex-start",
                           fontFamily: "'JetBrains Mono', monospace",
                         }}>
                      <Check size={12} /> {patCheck.detail}
                    </div>
                  )}
                  {patCheck.status === "error" && (
                    <div data-testid="proj-pat-verify-error"
                         style={{
                           fontSize: 11,
                           color: patCheck.error === "repo_not_found" ? "#f59e0b" : "#ef4444",
                           display: "inline-flex", alignItems: "flex-start", gap: 6,
                           padding: "6px 10px",
                           background: patCheck.error === "repo_not_found"
                             ? "rgba(245,158,11,0.10)" : "rgba(239,68,68,0.10)",
                           border: `0.5px solid ${patCheck.error === "repo_not_found"
                             ? "rgba(245,158,11,0.35)" : "rgba(239,68,68,0.35)"}`,
                           borderRadius: 8, alignSelf: "flex-start",
                           maxWidth: "100%",
                         }}>
                      <AlertCircle size={12} style={{ flexShrink: 0, marginTop: 1 }} />
                      <span>{patCheck.detail}</span>
                    </div>
                  )}
                </label>
              </div>
            )}

            <div style={{
              display: "flex", gap: 8, paddingTop: 16,
              borderTop: "0.5px solid var(--border, rgba(255,255,255,0.08))",
            }}>
              <button type="button" onClick={() => setConnectStep(1)} className="btn-ghost"
                      style={{ padding: "10px 16px", fontSize: 12 }}>
                Back
              </button>
              <button type="button"
                      data-testid="proj-connect-repo-btn"
                      onClick={handleConnectRepo}
                      disabled={!selectedRepo || patCheck.status !== "ok" || busy}
                      style={{
                        flex: 1, padding: "10px 0",
                        background: selectedRepo && patCheck.status === "ok" && !busy ? "#f59e0b"
                                                          : "var(--bg-elev, rgba(255,255,255,0.06))",
                        color: selectedRepo && patCheck.status === "ok" && !busy ? "#0a0e1a" : "var(--text-faint)",
                        border: "none", borderRadius: 8, fontSize: 14, fontWeight: 600,
                        cursor: selectedRepo && patCheck.status === "ok" && !busy ? "pointer" : "default",
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>
                {busy ? "Connecting…"
                      : patCheck.status === "loading" ? "Checking token…"
                      : patCheck.status === "ok"      ? "Connect repo ✓"
                      : "Connect repo & verify PAT"}
              </button>
            </div>
          </div>
        )}
      </div>
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

// ───────────────────────────────────────────────────────────────────
// Iter 206 — PatModal: focused per-project PAT setup with robot guide,
// GitHub deep-link, and step-by-step instructions. Opens when the user
// clicks the small "PAT" pill on a project row, or from the chat-side
// "Add PAT" CTA when a tool hits a 401.
// ───────────────────────────────────────────────────────────────────
export function PatModal({ project, onClose, onSaved }) {
  const [pat, setPat] = useState("");
  const [busy, setBusy] = useState(false);
  const [reveal, setReveal] = useState(false);
  // Iter 207 — multi-stage flow: input → testing → success | failed.
  // Replaces the old "save and pray" close-on-success behaviour.
  const [stage, setStage] = useState("input"); // input | testing | success | failed
  const [testResult, setTestResult] = useState(null); // {ok, repo, private, error}

  // Pre-filled deep-link to GitHub's fine-grained PAT creation page,
  // scoped to this exact repo + the contents:read & contents:write
  // permissions ORA needs. GitHub auto-selects the repo in the UI when
  // both `target_name` and `repository_ids` are absent, so we just pass
  // a sensible name + description and let the user pick the repo.
  const ghPatUrl =
    "https://github.com/settings/personal-access-tokens/new" +
    "?name=" + encodeURIComponent(`ORA · ${project.name}`) +
    "&description=" + encodeURIComponent("AUREM CTO (ORA) — read & commit on this repo.") +
    "&expiration=" + encodeURIComponent("90");

  async function runConnectionTest() {
    setStage("testing");
    setTestResult(null);
    try {
      const r = await api.get(`/cto/projects/${project.project_id}/test-pat`);
      const data = r.data || {};
      setTestResult(data);
      setStage(data.ok ? "success" : "failed");
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Connection test failed.";
      setTestResult({ ok: false, error: String(msg) });
      setStage("failed");
    }
  }

  async function save(e) {
    e?.preventDefault?.();
    const trimmed = pat.trim();
    if (!trimmed) {
      toast({ message: "Paste a GitHub PAT first.", kind: "warn" });
      return;
    }
    if (!/^(ghp_|github_pat_)/.test(trimmed)) {
      toast({ message: "That doesn't look like a GitHub PAT — should start with ghp_ or github_pat_", kind: "warn" });
      return;
    }
    setBusy(true);
    try {
      await api.patch(`/cto/projects/${project.project_id}`, { github_token: trimmed });
      // Don't close the modal yet — run the connection test so the
      // user sees a definitive green ✓ or red ✗ before leaving.
      toast({ message: "PAT saved — testing connection…", kind: "success" });
      await runConnectionTest();
    } catch (e2) {
      toast({ message: e2?.response?.data?.detail || "PAT save failed", kind: "error" });
    } finally { setBusy(false); }
  }

  function tryNewToken() {
    setStage("input");
    setTestResult(null);
    setPat("");
  }

  function close() {
    // If we got a green light, persist the refresh so the sidebar
    // pill flips amber → green.
    if (stage === "success") onSaved?.();
    else onClose?.();
  }

  return (
    <div onClick={close} data-testid="proj-pat-modal" style={{
      position: "fixed", inset: 0, zIndex: 9000,
      background: "rgba(8,10,14,0.72)",
      backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
    }}>
      <form onSubmit={save} onClick={(e) => e.stopPropagation()} style={{
        width: "min(520px, 100%)",
        background: "#0f172a",
        border: "0.5px solid rgba(255,255,255,0.1)",
        borderRadius: 14,
        boxShadow: "0 24px 60px -16px rgba(245,158,11,0.18)",
        padding: 22, display: "grid", gap: 14,
      }}>
        <RobotGuideKeyframes />

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontSize: 11, color: "#f59e0b", letterSpacing: "0.08em",
                          fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>
              PERSONAL ACCESS TOKEN
            </div>
            <h3 style={{ margin: "2px 0 0", fontSize: 17, color: "#f8fafc" }}>
              {project.name}
            </h3>
            <div style={{ fontSize: 11, color: "#64748b",
                          fontFamily: "'JetBrains Mono', monospace" }}>
              {project.github_owner}/{project.github_repo}
            </div>
          </div>
          <button type="button" onClick={close}
                  data-testid="proj-pat-close"
                  style={{ background: "transparent", border: "none",
                           color: "#64748b", cursor: "pointer", padding: 4 }}>
            <Trash2 size={14} />
          </button>
        </div>

        {/* ─────────── Stage: success ─────────── */}
        {stage === "success" && (
          <div data-testid="proj-pat-success" style={successBoxStyle}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{
                width: 32, height: 32, borderRadius: "50%",
                background: "rgba(34,197,94,0.18)", color: "#22c55e",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                <Check size={16} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "#22c55e" }}>
                  Connected to {testResult?.repo || `${project.github_owner}/${project.github_repo}`}
                </div>
                <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
                  {testResult?.private ? "Private repo" : "Public repo"} · ORA can now scan and commit.
                </div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 10 }}>
              <button type="button" data-testid="proj-pat-done" onClick={close}
                      style={primaryAmberBtn}>
                Done
              </button>
            </div>
          </div>
        )}

        {/* ─────────── Stage: failed ─────────── */}
        {stage === "failed" && (
          <div data-testid="proj-pat-failed" style={failBoxStyle}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <div style={{
                width: 32, height: 32, borderRadius: "50%",
                background: "rgba(239,68,68,0.18)", color: "#ef4444",
                display: "flex", alignItems: "center", justifyContent: "center",
                flexShrink: 0,
              }}>
                <AlertCircle size={16} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "#ef4444" }}>
                  Connection failed
                </div>
                <div data-testid="proj-pat-failed-msg"
                     style={{ fontSize: 12, color: "#f8fafc", marginTop: 4, lineHeight: 1.55 }}
                     dangerouslySetInnerHTML={{ __html: (testResult?.error || "Unknown error").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") }} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 10 }}>
              <button type="button" onClick={close} className="btn-ghost">Close</button>
              <button type="button" data-testid="proj-pat-try-new"
                      onClick={tryNewToken}
                      style={primaryAmberBtn}>
                Try a new token <ArrowRight size={12} style={{ marginLeft: 4 }} />
              </button>
            </div>
          </div>
        )}

        {/* ─────────── Stage: testing ─────────── */}
        {stage === "testing" && (
          <div data-testid="proj-pat-testing" style={{
            padding: "14px 16px",
            background: "rgba(245,158,11,0.06)",
            border: "1px solid rgba(245,158,11,0.25)",
            borderRadius: 10,
            display: "flex", alignItems: "center", gap: 10,
          }}>
            <Loader2 size={16} style={{ color: "#f59e0b" }} className="animate-spin" />
            <span style={{ fontSize: 13, color: "#f8fafc" }}>
              Testing connection to <code style={codeChip}>{project.github_owner}/{project.github_repo}</code>…
            </span>
          </div>
        )}

        {/* ─────────── Stage: input (default) ─────────── */}
        {stage === "input" && (
          <>
            <RobotGuide
              testid="proj-pat-robot"
              kind="info"
              message={
                pat && /^(ghp_|github_pat_)/.test(pat.trim())
                  ? `Looks good! Hit <strong>Save &amp; Test</strong> below — I&rsquo;ll verify the token works against your repo right after. <span class="ora-arrow">👇</span>`
                  : `Click <strong>Open GitHub → Create PAT</strong> below — page opens in a new tab with everything pre-filled. Pick the right repo, check <strong>Contents: Read &amp; Write</strong>, then paste the token here. <span class="ora-arrow">👇</span>`
              }
            />

            {/* Big deep-link CTA */}
            <a
              href={ghPatUrl}
              target="_blank" rel="noopener noreferrer"
              data-testid="proj-pat-github-link"
              style={{
                display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
                padding: 13, background: "#24292e", color: "#fff",
                border: "2px solid #f59e0b", borderRadius: 10,
                textDecoration: "none", fontSize: 14, fontWeight: 500,
              }}
            >
              <Github size={18} />
              Open GitHub → Create PAT
              <ExternalLink size={13} style={{ opacity: 0.7 }} />
            </a>

            <ol style={{
              margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.6,
              color: "var(--text-dim, #94a3b8)",
            }}>
              <li><strong style={{ color: "#f8fafc" }}>Repository access:</strong> select <em>Only select repositories</em> → pick <code style={codeChip}>{project.github_owner}/{project.github_repo}</code>.</li>
              <li><strong style={{ color: "#f8fafc" }}>Permissions:</strong> under <em>Repository permissions</em> set <code style={codeChip}>Contents: Read and write</code>.</li>
              <li>Click <em>Generate token</em>, copy it (starts with <code style={codeChip}>github_pat_…</code> or <code style={codeChip}>ghp_…</code>).</li>
              <li>Paste below and hit <strong style={{ color: "#f8fafc" }}>Save &amp; Test</strong>.</li>
            </ol>

            <label style={{ display: "grid", gap: 6 }}>
              <span style={{ fontSize: 11, color: "#94a3b8",
                              fontFamily: "'JetBrains Mono', monospace",
                              letterSpacing: "0.04em" }}>
                Paste your PAT
              </span>
              <div style={{ position: "relative" }}>
                <input
                  data-testid="proj-pat-input"
                  type={reveal ? "text" : "password"}
                  autoComplete="off" autoCorrect="off" spellCheck={false}
                  value={pat}
                  onChange={(e) => setPat(e.target.value)}
                  placeholder="github_pat_…"
                  style={{
                    width: "100%", padding: "10px 38px 10px 12px", fontSize: 13,
                    background: "#0a0e1a", color: "#f8fafc",
                    border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8,
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                />
                <button
                  type="button"
                  data-testid="proj-pat-reveal"
                  onClick={() => setReveal(v => !v)}
                  style={{
                    position: "absolute", right: 6, top: "50%",
                    transform: "translateY(-50%)",
                    background: "transparent", border: "none",
                    color: "#64748b", cursor: "pointer", padding: 6,
                  }}
                  title={reveal ? "Hide" : "Show"}
                >
                  {reveal ? <Lock size={13} /> : <Info size={13} />}
                </button>
              </div>
            </label>

            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button type="button" onClick={close}
                      data-testid="proj-pat-cancel" className="btn-ghost">Cancel</button>
              <button type="submit" data-testid="proj-pat-save" disabled={busy}
                      style={primaryAmberBtn}>
                {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                {busy ? "Saving…" : "Save & Test"}
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  );
}

const primaryAmberBtn = {
  padding: "10px 18px",
  background: "#f59e0b", color: "#0a0c10",
  border: "none", borderRadius: 8,
  fontSize: 13, fontWeight: 600, cursor: "pointer",
  display: "inline-flex", alignItems: "center", gap: 6,
};

const successBoxStyle = {
  padding: "14px 16px",
  background: "rgba(34,197,94,0.07)",
  border: "1px solid rgba(34,197,94,0.3)",
  borderRadius: 10,
};

const failBoxStyle = {
  padding: "14px 16px",
  background: "rgba(239,68,68,0.07)",
  border: "1px solid rgba(239,68,68,0.3)",
  borderRadius: 10,
};

const codeChip = {
  padding: "1px 6px", background: "rgba(245,158,11,0.1)",
  border: "1px solid rgba(245,158,11,0.25)", borderRadius: 4,
  fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
  color: "#f59e0b",
};


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
        if (t && ["done", "failed", "cancelled", "blocked"].includes(t.status)) {
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
