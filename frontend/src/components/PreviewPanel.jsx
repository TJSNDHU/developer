/**
 * PreviewPanel.jsx — Right-side live preview for code blocks parsed from
 * the latest assistant message.
 *
 *   - HTML/CSS/JS → rendered inside a sandboxed iframe via `srcDoc`
 *   - JSX/React   → transpiled at runtime via @babel/standalone (loaded
 *                    inside the iframe so it never touches the host page)
 *   - Other langs (python, bash, …) → syntax-coloured code viewer
 *
 * The iframe is sandboxed with `allow-scripts` only — no same-origin —
 * so any code rendered here cannot read parent state.
 */
import React, { useMemo, useState } from "react";
import { X, Copy, RefreshCw, Code2, Eye, ExternalLink, Loader2 } from "lucide-react";
import { toast } from "./Toast";
import { api } from "../lib/api";

const RENDERABLE = new Set([
  "html", "htm",
  "jsx", "tsx",
  "js", "javascript",
  "live_url",  // iframe of an arbitrary preview URL
]);

function filename(block, idx) {
  if (!block) return `file_${idx}`;
  if (block.label) {
    // For codebase tabs, show only the basename so 20+ tabs fit
    // without each being squished to a single character. The full
    // path is preserved in the tab's `title` tooltip.
    const parts = block.label.split("/");
    return parts[parts.length - 1] || block.label;
  }
  const l = block.lang.toLowerCase();
  if (l === "live_url") return "Live Site";
  if (l === "jsx" || l === "tsx") return `App.${l}`;
  if (l === "html" || l === "htm") return "index.html";
  if (l === "css") return "styles.css";
  if (l === "js" || l === "javascript") return "script.js";
  if (l === "python" || l === "py") return "main.py";
  if (l === "yaml" || l === "yml") return "config.yml";
  if (l === "json") return "data.json";
  if (l === "bash" || l === "sh") return "script.sh";
  return `file_${idx}.${l}`;
}

function buildIframeDoc(block) {
  const code = block?.code || "";
  const l = (block?.lang || "").toLowerCase();
  // Direct HTML
  if (l === "html" || l === "htm") return code;
  // JSX — load React + Babel inside the iframe
  if (l === "jsx" || l === "tsx") {
    // Strip ES module syntax so we can run with `new Function()` after Babel
    let stripped = code
      .replace(/^\s*import\s+[^;]+;?\s*$/gm, "")      // import lines
      .replace(/^\s*export\s+default\s+/gm, "const __default__ = ")
      .replace(/^\s*export\s+\{[^}]*\}\s*;?\s*$/gm, "")
      .replace(/^\s*export\s+(const|let|var|function|class)\s+/gm, "$1 ");
    // If they exported "App" via default but defined it inline, our replace
    // turned `export default App;` into `const __default__ = App;` — fine.
    // If they exported an arrow fn directly: `export default () => …`
    // we still capture it in __default__.
    return `<!doctype html>
<html><head><meta charset="utf-8" /><style>
  html,body{margin:0;font-family:system-ui,sans-serif;color:#0a0a0a;background:#fff;padding:14px}
  pre{color:#b00;background:#fee;padding:12px;border-radius:4px;white-space:pre-wrap}
</style></head>
<body><div id="root"></div>
<script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<script>
  try {
    const src = ${JSON.stringify(stripped)};
    const out = Babel.transform(src, { presets: ['react'] }).code;
    const fn = new Function('React', 'ReactDOM',
      out + '\\n; return (typeof __default__!=="undefined") ? __default__ : (typeof App!=="undefined" ? App : (typeof Component!=="undefined" ? Component : null));');
    const Comp = fn(React, ReactDOM);
    const root = ReactDOM.createRoot(document.getElementById('root'));
    if (Comp) root.render(React.createElement(Comp));
    else document.getElementById('root').innerHTML = '<pre>No exported component found. Define a function named <b>App</b> or <code>export default</code>.</pre>';
  } catch (e) {
    document.getElementById('root').innerHTML = '<pre>'+(e && e.message || e)+'</pre>';
  }
</script>
</body></html>`;
  }
  // Plain JS — run in body
  return `<!doctype html>
<html><head><meta charset="utf-8" /><style>
  html,body{margin:0;padding:14px;font-family:system-ui;color:#0a0a0a;background:#fff}
  pre{color:#b00;background:#fee;padding:12px;border-radius:4px;white-space:pre-wrap}
</style></head>
<body><div id="output"></div>
<script>
  try {
    const log = [];
    const _c = console.log;
    console.log = (...args) => { log.push(args.map(a => typeof a === 'string' ? a : JSON.stringify(a)).join(' ')); _c(...args); };
    ${code}
    if (log.length) document.getElementById('output').innerHTML = '<pre>'+log.join('\\n')+'</pre>';
  } catch (e) {
    document.getElementById('output').innerHTML = '<pre>'+(e && e.message || e)+'</pre>';
  }
</script>
</body></html>`;
}

export default function PreviewPanel({ blocks, onClose, activeProject }) {
  const [activeTab, setActiveTab] = useState(0);
  const [viewMode, setViewMode] = useState("preview"); // 'preview' | 'code'
  const [refreshKey, setRefreshKey] = useState(0);
  // Iter 170c — Codebase browse mode.
  //   `codebase`        — { files: [{path, size}], owner, repo, branch } once loaded
  //   `codebaseLoading` — true while the tree fetch is in flight
  //   `codebaseErr`     — populated when the GitHub call fails (bad PAT, missing
  //                       branch, etc.) so we can show a one-line hint instead of a blank panel
  //   `fileContents`    — Map<path, {code, loading, err}> populated on tab click
  const [codebase, setCodebase] = useState(null);
  const [codebaseLoading, setCodebaseLoading] = useState(false);
  const [codebaseErr, setCodebaseErr] = useState(null);
  const [fileContents, setFileContents] = useState({});

  // Note: We deliberately don't useEffect to reset on project change.
  // ChatPanel passes `key={activeProject?.project_id}` so a project
  // switch unmounts and remounts the panel — cleaner than effects.

  // Fired when the user toggles into Code mode and the chat hasn't
  // produced any real code blocks yet (only the live_url placeholder).
  // We fetch the repo tree once per project and lazy-load files on tab
  // click. Errors are surfaced inline.
  const fetchCodebaseTree = async () => {
    if (!activeProject?.project_id) return;
    let go = true;
    setCodebaseLoading((cur) => {
      if (cur) { go = false; return cur; }   // already loading
      return true;
    });
    if (!go) return;
    let alreadyHave = false;
    setCodebase((cur) => { if (cur) alreadyHave = true; return cur; });
    if (alreadyHave) { setCodebaseLoading(false); return; }
    setCodebaseErr(null);
    try {
      const r = await api.get(
        `/cto/projects/${activeProject.project_id}/tree`
      );
      setCodebase({
        files: r.data?.files || [],
        owner: r.data?.owner,
        repo:  r.data?.repo,
        branch: r.data?.branch,
        truncated: !!r.data?.truncated,
      });
    } catch (e) {
      const msg = e?.response?.data?.detail
        || e?.message
        || "Couldn't load codebase.";
      setCodebaseErr(msg);
    } finally {
      setCodebaseLoading(false);
    }
  };

  const fetchFileContent = async (path) => {
    if (!activeProject?.project_id || !path) return;
    // Use functional setState so we don't need `fileContents` in deps.
    let shouldFetch = true;
    setFileContents((m) => {
      if (m[path]?.code !== undefined || m[path]?.loading) {
        shouldFetch = false;
        return m;
      }
      return { ...m, [path]: { loading: true } };
    });
    if (!shouldFetch) return;
    try {
      const r = await api.get(
        `/cto/projects/${activeProject.project_id}/file`,
        { params: { path } }
      );
      setFileContents((m) => ({
        ...m,
        [path]: { code: r.data?.content || "", truncated: !!r.data?.truncated },
      }));
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Failed to load file.";
      setFileContents((m) => ({ ...m, [path]: { err: msg } }));
    }
  };

  // Iter 170c — Compute the actual blocks list. When the chat hasn't
  // produced real code yet and we've fetched the GitHub tree, append
  // each file as a lazy-load tab. This is how the `</> Code` toggle
  // becomes useful even before the user has shipped a task.
  const codebaseBlocks = (() => {
    if (!codebase?.files) return [];
    const extLang = {
      py: "python", js: "javascript", jsx: "jsx", ts: "typescript",
      tsx: "tsx", html: "html", htm: "html", css: "css", scss: "css",
      json: "json", yml: "yaml", yaml: "yaml", md: "markdown",
      sh: "bash", bash: "bash", sql: "sql", toml: "toml", env: "bash",
      go: "go", rs: "rust", java: "java", rb: "ruby", php: "php",
    };
    return codebase.files.map((f) => {
      const ext = (f.path.split(".").pop() || "").toLowerCase();
      return {
        lang: extLang[ext] || "text",
        label: f.path,
        isCodebase: true,
        size: f.size,
      };
    });
  })();

  const realBlocks = blocks || [];
  const onlyLiveOrPlaceholder = realBlocks.length === 0 || realBlocks.every(
    (b) => {
      const l = (b?.lang || "").toLowerCase();
      return l === "live_url" || l === "text";
    }
  );
  const effectiveBlocks = (onlyLiveOrPlaceholder && codebaseBlocks.length > 0)
    ? [...realBlocks.filter((b) => (b?.lang || "").toLowerCase() === "live_url"), ...codebaseBlocks]
    : realBlocks;

  const block = effectiveBlocks[activeTab];
  const isRenderable = !!block && RENDERABLE.has((block.lang || "").toLowerCase());
  const isLiveUrl = (block?.lang || "").toLowerCase() === "live_url";
  // Note: We deliberately don't useEffect to lazy-load file content
  // on tab change. The tab button's onClick triggers the fetch inline
  // — see the file-tabs map below.
  const effectiveCode = block?.isCodebase
    ? (fileContents[block.label]?.code || "")
    : (block?.code || "");
  const codebaseTabState = block?.isCodebase
    ? fileContents[block.label]
    : null;
  const srcDoc = useMemo(
    () => (isRenderable && !isLiveUrl && !block?.isCodebase ? buildIframeDoc(block) : ""),
    [block, isRenderable, isLiveUrl, refreshKey]
  );

  const copyCode = () => {
    if (!effectiveCode) return;
    navigator.clipboard.writeText(effectiveCode);
    toast({ message: "Code copied.", kind: "success", duration: 1800 });
  };

  const canShowCodeToggle = (
    isRenderable
    || effectiveBlocks.some((b) => (b?.lang || "").toLowerCase() !== "live_url")
    || !!activeProject?.project_id  // can fetch repo on demand
  );

  return (
    <aside
      data-testid="preview-panel"
      style={{
        display: "flex", flexDirection: "column",
        height: "100vh", width: "100%",
        background: "var(--bg)",
        borderLeft: "1px solid var(--border)",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "8px 12px",
        borderBottom: "1px solid var(--border)",
        background: "var(--bg-elev)",
      }}>
        {viewMode === "preview" && (
          <span style={{
            color: "var(--accent-2)",
            fontSize: 10, letterSpacing: "0.18em",
            fontFamily: "'JetBrains Mono', monospace",
            textTransform: "uppercase", marginRight: 6,
            flexShrink: 0,
          }}>
            live preview
          </span>
        )}

        {/* File tabs */}
        <div style={{
          display: "flex", gap: 4, flex: 1,
          overflowX: "auto", minWidth: 0,
        }}>
          {effectiveBlocks.map((b, i) => (
            <button
              key={`${i}-${b.label || b.lang}`}
              data-testid={`preview-tab-${i}`}
              onClick={() => {
                setActiveTab(i);
                if (b?.isCodebase && b.label) fetchFileContent(b.label);
              }}
              title={b.label || filename(b, i)}
              style={{
                fontSize: 11, padding: "4px 10px", borderRadius: 4,
                background: activeTab === i ? "var(--accent)" : "transparent",
                color: activeTab === i ? "#0a0a0a" : "var(--text-dim)",
                border: `1px solid ${activeTab === i ? "var(--accent)" : "var(--border)"}`,
                cursor: "pointer",
                fontFamily: "'JetBrains Mono', monospace",
                whiteSpace: "nowrap",
                // Tabs must NOT shrink under flex pressure — otherwise
                // 20+ codebase tabs collapse to single characters
                // ("s(", ".a") as reported by users.
                flexShrink: 0,
                maxWidth: 200,
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {filename(b, i)}
            </button>
          ))}
          {codebaseLoading && (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              fontSize: 11, color: "var(--text-faint)",
              padding: "4px 8px", flexShrink: 0,
            }}>
              <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} />
              loading repo…
            </span>
          )}
        </div>

        {/* preview/code toggle — Iter 169: when toggling FROM the
            Live Site URL block INTO Code mode, auto-jump to the
            first actual code/file block so the user sees real code
            instead of the raw URL string.
            Iter 170c: if no real code blocks exist but the project
            has GitHub connected, fetch the repo tree on first toggle
            so `</> Code` browses the live codebase. */}
        {canShowCodeToggle && (
          <button
            data-testid="preview-view-toggle"
            onClick={() => {
              setViewMode((v) => {
                const next = v === "preview" ? "code" : "preview";
                if (next === "code") {
                  // If we only have live_url placeholders and a project
                  // is connected, kick off the codebase fetch.
                  if (onlyLiveOrPlaceholder && !codebase
                      && activeProject?.project_id) {
                    fetchCodebaseTree();
                  }
                  // Jump to the first non-live_url tab if currently on one.
                  if (isLiveUrl) {
                    const idx = effectiveBlocks.findIndex(
                      (b) => (b?.lang || "").toLowerCase() !== "live_url"
                    );
                    if (idx >= 0) {
                      setActiveTab(idx);
                      const tgt = effectiveBlocks[idx];
                      if (tgt?.isCodebase && tgt.label) fetchFileContent(tgt.label);
                    }
                  }
                }
                if (next === "preview") {
                  const liveIdx = effectiveBlocks.findIndex(
                    (b) => (b?.lang || "").toLowerCase() === "live_url"
                  );
                  if (liveIdx >= 0) setActiveTab(liveIdx);
                }
                return next;
              });
            }}
            className="btn-ghost"
            style={{ padding: "4px 10px", fontSize: 11, flexShrink: 0 }}
            title="Toggle preview/code"
          >
            {viewMode === "preview"
              ? <><Code2 size={11} /> Code</>
              : <><Eye size={11} /> Preview</>}
          </button>
        )}
        <button
          data-testid="preview-close"
          onClick={onClose}
          title="Close preview"
          style={{
            background: "none", border: "none",
            color: "var(--text-faint)", cursor: "pointer",
            padding: 4, flexShrink: 0,
          }}
        >
          <X size={15} />
        </button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
        {viewMode === "preview" && isLiveUrl ? (
          <iframe
            key={`liveurl-${block.code}-${refreshKey}`}
            data-testid="preview-iframe-live"
            src={block.code}
            title="live-site"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
            style={{
              width: "100%", height: "100%",
              border: "none", background: "white",
            }}
          />
        ) : viewMode === "preview" && isRenderable && !block?.isCodebase ? (
          <iframe
            key={`iframe-${activeTab}-${refreshKey}`}
            data-testid="preview-iframe"
            srcDoc={srcDoc}
            sandbox="allow-scripts"
            title="preview"
            style={{
              width: "100%", height: "100%",
              border: "none", background: "white",
            }}
          />
        ) : codebaseErr && viewMode === "code" ? (
          <div
            data-testid="preview-codebase-err"
            style={{
              padding: 20, color: "var(--danger, #ef4444)",
              fontSize: 12,
            }}
          >
            ⚠ {codebaseErr}
          </div>
        ) : block?.isCodebase && codebaseTabState?.loading ? (
          <div
            data-testid="preview-codebase-loading"
            style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: 20, color: "var(--text-faint)", fontSize: 12,
            }}
          >
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
            loading {block.label}…
          </div>
        ) : block?.isCodebase && codebaseTabState?.err ? (
          <div
            data-testid="preview-codebase-file-err"
            style={{
              padding: 20, color: "var(--danger, #ef4444)", fontSize: 12,
            }}
          >
            ⚠ {codebaseTabState.err}
          </div>
        ) : (
          <pre
            data-testid="preview-code"
            style={{
              margin: 0, padding: 16,
              overflow: "auto", height: "100%",
              fontSize: 12, lineHeight: 1.5,
              fontFamily: "'JetBrains Mono', monospace",
              color: "var(--text)",
              background: "var(--bg)",
              whiteSpace: "pre",
            }}
          >
            <code>{effectiveCode}</code>
          </pre>
        )}
      </div>

      {/* Footer */}
      <div style={{
        display: "flex", gap: 8,
        padding: "6px 12px",
        borderTop: "1px solid var(--border)",
        background: "var(--bg-elev)",
        fontSize: 11,
      }}>
        <button
          data-testid="preview-copy"
          className="btn-ghost"
          onClick={copyCode}
          style={{ padding: "4px 10px", fontSize: 11 }}
        >
          <Copy size={11} /> Copy
        </button>
        {isRenderable && (
          <button
            data-testid="preview-refresh"
            className="btn-ghost"
            onClick={() => setRefreshKey((k) => k + 1)}
            style={{ padding: "4px 10px", fontSize: 11 }}
          >
            <RefreshCw size={11} /> Refresh
          </button>
        )}
        {isLiveUrl && (
          <a
            data-testid="preview-open-newtab"
            href={block.code}
            target="_blank"
            rel="noreferrer"
            className="btn-ghost"
            style={{ padding: "4px 10px", fontSize: 11, textDecoration: "none" }}
            title="Open in new tab"
          >
            <ExternalLink size={11} /> Open
          </a>
        )}
        <span style={{
          marginLeft: "auto", color: "var(--text-faint)",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {block?.isCodebase && codebase
            ? <>{codebase.owner}/{codebase.repo}@{codebase.branch} · {effectiveCode.length} chars{codebaseTabState?.truncated ? " (truncated)" : ""}</>
            : <>lang: {block?.lang || "—"} · {effectiveCode.length || 0} chars</>
          }
        </span>
      </div>
    </aside>
  );
}
