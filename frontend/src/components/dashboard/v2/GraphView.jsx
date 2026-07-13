/**
 * GraphView.jsx — Iter 212m-215 (GitDiagram approach)
 *
 * Replaces the earlier D3 force-directed attempt with a Mermaid.js
 * architecture diagram — mirrors GitDiagram's proven 3-step pipeline
 * (github.com/ahmedkhaleel2004/gitdiagram, MIT, 23k★):
 *
 *   1. Backend: graph_builder.py already fetches the repo tree +
 *      symbols + imports (no change to that layer).
 *   2. Backend `services/mermaid_diagram.py`:
 *        - LLM pass 1: plain-English architecture explanation
 *        - LLM pass 2: Mermaid.js flowchart with layer subgraphs,
 *          clickable file nodes, and `:::hot` orange highlights
 *          for recently modified files.
 *   3. Frontend: this component fetches the cached `mermaid_code`
 *      and hands it to MermaidBlock.jsx (which already exists and
 *      handles the strict-mode Mermaid render + SVG copy).
 *
 * Node clicks: the LLM emits `click <Id> href "github://<path>"` in
 * the Mermaid code.  Before render we rewrite the `github://` prefix
 * to a real deep-link into the user's project — we point at the
 * legacy /projects file browser for now, which knows how to open a
 * given file inside the connected repo.
 *
 * States: no-project → no-repo (CTA) → loading (build in progress)
 *         → ready (Mermaid) → error (retry).
 * The Graph tab auto-triggers a build on first open, so the user
 * never has to press a manual "Build graph" button.
 */

import React, { useEffect, useMemo, useState, useCallback } from "react";
import { api } from "../../../lib/api";
import { getActiveProjectId, useActiveProject } from "../../TabBar";
import MermaidBlock from "../../MermaidBlock";
import {
  GitBranch, LinkIcon, Loader2, RefreshCw, AlertTriangle, Sparkles,
} from "lucide-react";

const GRID_DOT = "#252540";

export function GraphView() {
  const activeProject = useActiveProject();
  const projectId = activeProject?.project_id || getActiveProjectId();

  const [graph,     setGraph]     = useState(null);
  const [status,    setStatus]    = useState("loading"); // loading|building|generating_diagram|ready|no_repo|no_project|error
  const [error,     setError]     = useState(null);
  const [tick,      setTick]      = useState(0);
  const [regenerating, setRegenerating] = useState(false);

  // ── Pipeline ────────────────────────────────────────────────────
  // 1. GET /graph?full=true — read cache.
  // 2. If graph not built → POST /build-graph, poll.
  // 3. Once graph is ready but has no mermaid_code → POST
  //    /graph/mermaid to generate it, then re-read.
  useEffect(() => {
    if (!projectId) { setStatus("no_project"); return; }
    let dead = false;
    let pollTimer = null;
    let attempts = 0;

    const _fetchGraph = async () => {
      const r = await api.get(`/cto/projects/${projectId}/graph`, {
        params: { full: true },
      });
      return r?.data;
    };

    const _kickBuild = async () => {
      try {
        await api.post(`/cto/projects/${projectId}/build-graph`);
        return { ok: true };
      } catch (e) {
        const msg = e?.response?.data?.detail || e?.message || "";
        if (/github not connected|no.*pat|token/i.test(msg)) {
          return { ok: false, kind: "no_repo" };
        }
        return { ok: false, kind: "error", msg };
      }
    };

    const _generateDiagram = async () => {
      try {
        const r = await api.post(`/cto/projects/${projectId}/graph/mermaid`);
        return { ok: true, data: r?.data };
      } catch (e) {
        return {
          ok: false,
          msg: e?.response?.data?.detail || e?.message || "diagram build failed",
        };
      }
    };

    const _loop = async () => {
      attempts += 1;
      try {
        const first = await _fetchGraph();
        if (dead) return;

        // Case A — no graph at all
        if (first?.status === "not_built") {
          if (attempts === 1) {
            const kick = await _kickBuild();
            if (!kick.ok) {
              if (kick.kind === "no_repo") setStatus("no_repo");
              else { setStatus("error"); setError(kick.msg || "build failed"); }
              return;
            }
            setStatus("building");
          }
          if (attempts <= 22) pollTimer = setTimeout(_loop, 4000);
          else {
            setStatus("error");
            setError("Graph build did not finish in time — try again.");
          }
          return;
        }

        // Case B — graph ready
        if (first?.status === "ready" && first?.graph) {
          setGraph(first.graph);
          // Iter 212m-215 — Auto-invalidate cached diagram when a
          // new commit has landed. `mermaid_tree_sha` is what we
          // rendered against; `tree_sha` is the current graph.
          // Mismatch → silently regenerate the diagram (no user
          // action needed).  If it matches, use the cache.
          const isStale =
            first.graph.mermaid_tree_sha &&
            first.graph.tree_sha &&
            first.graph.mermaid_tree_sha !== first.graph.tree_sha;
          if (first.graph.mermaid_code && !isStale) {
            setStatus("ready");
            setError(null);
            return;
          }
          // No cached diagram OR the cache is out of date.  Generate.
          setStatus("generating_diagram");
          const gen = await _generateDiagram();
          if (dead) return;
          if (!gen.ok) {
            setStatus("error");
            setError(gen.msg);
            return;
          }
          // Merge the returned payload into local graph state
          setGraph((prev) => ({ ...(prev || {}), ...gen.data }));
          setStatus("ready");
          setError(null);
          return;
        }

        // Case C — unexpected shape
        setStatus("error");
        setError("Unexpected response from graph API");
      } catch (e) {
        if (dead) return;
        setStatus("error");
        setError(e?.response?.data?.detail || e?.message || "network error");
      }
    };

    setStatus("loading");
    _loop();
    return () => {
      dead = true;
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [projectId, tick]);

  // ── Manual regenerate — bypass cache, re-run just step 2 ────────
  const _onRegenerate = useCallback(async () => {
    if (!projectId || regenerating) return;
    setRegenerating(true);
    setError(null);
    try {
      const r = await api.post(`/cto/projects/${projectId}/graph/mermaid`);
      setGraph((prev) => ({ ...(prev || {}), ...(r?.data || {}) }));
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "regenerate failed");
    } finally {
      setRegenerating(false);
    }
  }, [projectId, regenerating]);

  // ── Rewrite `github://<path>` in the LLM's click directives to a
  //     real deep-link the app can handle (legacy /projects file
  //     browser knows how to jump to a given path).
  const preparedCode = useMemo(() => {
    const raw = graph?.mermaid_code || "";
    if (!raw || !projectId) return raw;
    // Turn: click NodeId href "github://backend/routers/chat.py" _blank
    // Into: click NodeId href "/projects?open=<pid>&file=backend/routers/chat.py" _blank
    return raw.replace(
      /href\s+"github:\/\/([^"]+)"/g,
      (_m, p) => `href "/projects?open=${encodeURIComponent(projectId)}&file=${encodeURIComponent(p)}"`,
    );
  }, [graph, projectId]);

  // ── Empty / degraded states ────────────────────────────────────
  if (status === "no_project") return <_EmptyNoProject />;
  if (status === "no_repo")    return <_EmptyNoRepo projectId={projectId} />;
  if (status === "loading" || status === "building" || status === "generating_diagram")
    return <_LoadingGraph phase={status} />;
  if (status === "error")
    return <_ErrorGraph error={error} onRetry={() => setTick((n) => n + 1)} />;

  return (
    <div data-testid="ds2-graph"
         className="relative h-full overflow-hidden p-4 md:p-6">
      <div className="relative h-full overflow-hidden rounded-xl border border-border bg-card">
        {/* dot grid */}
        <div className="absolute inset-0 opacity-[0.35] pointer-events-none"
             style={{
               backgroundImage: `radial-gradient(${GRID_DOT} 1px, transparent 1px)`,
               backgroundSize: "22px 22px",
             }} />

        {/* Header */}
        <div className="relative z-10 flex items-start justify-between border-b border-border/40 p-4">
          <div>
            <h2 className="text-sm font-semibold tracking-tight text-foreground"
                data-testid="ds2-graph-title">
              Architecture diagram
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {graph?.file_count || 0} files
              {graph?.mermaid_model ? ` · rendered by ${graph.mermaid_model.split("/").pop()}` : ""}
              {graph?.mermaid_generated_at
                ? ` · updated ${_relTime(graph.mermaid_generated_at)}`
                : ""}
            </p>
          </div>
          <button
            data-testid="ds2-graph-regenerate"
            onClick={_onRegenerate}
            disabled={regenerating}
            className="flex items-center gap-1.5 rounded-full border border-border bg-background/80 px-3 py-1 text-[11px] text-muted-foreground backdrop-blur-sm transition-colors hover:text-foreground disabled:opacity-50"
          >
            {regenerating
              ? <><Loader2 className="size-3 animate-spin" /><span>Regenerating…</span></>
              : <><RefreshCw className="size-3" /><span>Regenerate diagram</span></>}
          </button>
        </div>

        {/* Optional explanation — small, quiet, above the diagram */}
        {graph?.mermaid_explanation && (
          <div className="relative z-10 mx-4 mt-3 rounded-lg border border-border/40 bg-background/60 p-3 text-[11px] leading-relaxed text-muted-foreground backdrop-blur-sm"
               data-testid="ds2-graph-explanation">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-orange-400">
              <Sparkles className="size-3" /> Architecture summary
            </div>
            {graph.mermaid_explanation}
          </div>
        )}

        {/* Mermaid canvas */}
        <div className="relative z-10 mx-4 my-3 overflow-auto rounded-lg"
             data-testid="ds2-graph-mermaid-wrap"
             style={{ height: "calc(100% - 190px)" }}>
          <MermaidBlock code={preparedCode} title="Codebase architecture" />
        </div>

        {error && (
          <p className="relative z-10 mx-4 mb-2 text-[11px] text-red-300"
             data-testid="ds2-graph-inline-error">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────

function _relTime(unixSecs) {
  if (!unixSecs) return "";
  const s = Math.max(0, Date.now() / 1000 - unixSecs);
  if (s < 60)  return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// ══════════════════════════════════════════════════════════════════
//  Empty / degraded states
// ══════════════════════════════════════════════════════════════════

function _EmptyNoProject() {
  return (
    <div data-testid="ds2-graph-empty-no-project"
         className="flex h-full items-center justify-center p-8">
      <div className="max-w-sm rounded-xl border border-border bg-card p-6 text-center">
        <GitBranch className="mx-auto size-6 text-muted-foreground" />
        <h3 className="mt-3 text-sm font-semibold">Select a project</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Pick a repo from the sidebar to see its architecture diagram.
        </p>
      </div>
    </div>
  );
}

function _EmptyNoRepo({ projectId }) {
  return (
    <div data-testid="ds2-graph-empty-no-repo"
         className="flex h-full items-center justify-center p-8">
      <div className="relative w-full max-w-md overflow-hidden rounded-xl border border-border bg-card p-6 text-center">
        <div className="absolute inset-0 opacity-[0.35] pointer-events-none"
             style={{
               backgroundImage: `radial-gradient(${GRID_DOT} 1px, transparent 1px)`,
               backgroundSize: "20px 20px",
             }} />
        <div className="relative">
          <GitBranch className="mx-auto size-7 text-orange-400" />
          <h3 className="mt-3 text-base font-semibold">
            Connect your repo to see the dependency graph
          </h3>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            The diagram is built from your live GitHub repository — files,
            layers, and import relationships.
            <br />Attach a fine-grained PAT to unlock it.
          </p>
          <button
            data-testid="ds2-graph-connect-cta"
            onClick={() => {
              window.location.href = projectId
                ? `/projects?open=${encodeURIComponent(projectId)}`
                : "/projects";
            }}
            className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-orange-500 px-4 py-1.5 text-xs font-medium text-black transition-opacity hover:opacity-90"
          >
            <LinkIcon className="size-3" />
            Connect repo
          </button>
        </div>
      </div>
    </div>
  );
}

function _LoadingGraph({ phase }) {
  const label =
    phase === "building"
      ? "Building the graph from your GitHub repo…  first build can take up to a minute."
      : phase === "generating_diagram"
        ? "Generating architecture diagram… (~10s)"
        : "Loading graph…";
  return (
    <div data-testid="ds2-graph-loading"
         data-phase={phase}
         className="flex h-full items-center justify-center p-8">
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <Loader2 className="size-5 animate-spin text-orange-400" />
        <p className="text-xs">{label}</p>
      </div>
    </div>
  );
}

function _ErrorGraph({ error, onRetry }) {
  return (
    <div data-testid="ds2-graph-error"
         className="flex h-full items-center justify-center p-8">
      <div className="max-w-sm rounded-xl border border-red-500/40 bg-red-500/5 p-6 text-center">
        <AlertTriangle className="mx-auto size-6 text-red-400" />
        <h3 className="mt-3 text-sm font-semibold text-red-200">Diagram unavailable</h3>
        <p className="mt-1 text-xs text-red-100/80">{error || "Something went wrong."}</p>
        <button
          data-testid="ds2-graph-retry"
          onClick={onRetry}
          className="mt-4 inline-flex items-center gap-1.5 rounded-full border border-red-400/50 px-3 py-1 text-xs text-red-100 hover:bg-red-500/10"
        >
          <RefreshCw className="size-3" />
          Retry
        </button>
      </div>
    </div>
  );
}
