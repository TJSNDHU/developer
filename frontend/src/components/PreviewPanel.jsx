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
import { X, Copy, RefreshCw, Code2, Eye, ExternalLink } from "lucide-react";
import { toast } from "./Toast";

const RENDERABLE = new Set([
  "html", "htm",
  "jsx", "tsx",
  "js", "javascript",
  "live_url",  // iframe of an arbitrary preview URL
]);

function filename(block, idx) {
  if (!block) return `file_${idx}`;
  if (block.label) return block.label;
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

export default function PreviewPanel({ blocks, onClose }) {
  const [activeTab, setActiveTab] = useState(0);
  const [viewMode, setViewMode] = useState("preview"); // 'preview' | 'code'
  const [refreshKey, setRefreshKey] = useState(0);

  const block = blocks?.[activeTab];
  const isRenderable = !!block && RENDERABLE.has((block.lang || "").toLowerCase());
  const isLiveUrl = (block?.lang || "").toLowerCase() === "live_url";
  const srcDoc = useMemo(
    () => (isRenderable && !isLiveUrl ? buildIframeDoc(block) : ""),
    [block, isRenderable, isLiveUrl, refreshKey]
  );

  const copyCode = () => {
    if (!block?.code) return;
    navigator.clipboard.writeText(block.code);
    toast({ message: "Code copied.", kind: "success", duration: 1800 });
  };

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
        <span style={{
          color: "var(--accent-2)",
          fontSize: 10, letterSpacing: "0.18em",
          fontFamily: "'JetBrains Mono', monospace",
          textTransform: "uppercase", marginRight: 6,
        }}>
          live preview
        </span>

        {/* File tabs */}
        <div style={{
          display: "flex", gap: 4, flex: 1,
          overflowX: "auto", minWidth: 0,
        }}>
          {(blocks || []).map((b, i) => (
            <button
              key={i}
              data-testid={`preview-tab-${i}`}
              onClick={() => setActiveTab(i)}
              style={{
                fontSize: 11, padding: "4px 10px", borderRadius: 4,
                background: activeTab === i ? "var(--accent)" : "transparent",
                color: activeTab === i ? "#0a0a0a" : "var(--text-dim)",
                border: `1px solid ${activeTab === i ? "var(--accent)" : "var(--border)"}`,
                cursor: "pointer",
                fontFamily: "'JetBrains Mono', monospace",
                whiteSpace: "nowrap",
              }}
            >
              {filename(b, i)}
            </button>
          ))}
        </div>

        {/* preview/code toggle */}
        {isRenderable && (
          <button
            data-testid="preview-view-toggle"
            onClick={() => setViewMode((v) => (v === "preview" ? "code" : "preview"))}
            className="btn-ghost"
            style={{ padding: "4px 10px", fontSize: 11 }}
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
            padding: 4,
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
        ) : viewMode === "preview" && isRenderable ? (
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
            <code>{block?.code || ""}</code>
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
          lang: {block?.lang || "—"} · {block?.code?.length || 0} chars
        </span>
      </div>
    </aside>
  );
}
