/**
 * PreviewPane.jsx — split-pane live preview.
 *
 * Two modes:
 *   • blob — inline HTML/CSS/JS from ORA's last shipped task, rendered
 *            inside a sandboxed iframe blob URL.
 *   • url  — Vercel/Netlify preview URL once the deploy lands (the
 *            backend writes `preview_url` on the cto_tasks doc once
 *            services/vercel_preview.py resolves).
 *
 * Inspired by Bolt.diy Preview.tsx — split-pane chat-on-left,
 * preview-on-right is the activation pattern that turns "AUREM is a
 * fancy chatbot" into "AUREM ships visible work".
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, ExternalLink, Eye } from "lucide-react";
import { api } from "../lib/api";

const ACCENT = "var(--accent, #ff8a2a)";

export default function PreviewPane({ taskId }) {
  const [task, setTask] = useState(null);
  const [mode, setMode] = useState("idle"); // idle | blob | url
  const [reloadKey, setReloadKey] = useState(0);
  const iframeRef = useRef(null);

  // Poll the task until it has edits or a preview URL.
  useEffect(() => {
    if (!taskId) { setTask(null); setMode("idle"); return; }
    let cancelled = false;
    let timer = null;
    const tick = async () => {
      try {
        const r = await api.get(`/cto/tasks/${taskId}`);
        const t = r.data?.task || r.data;
        if (!cancelled && t) {
          setTask(t);
          if (t.preview_url) setMode("url");
          else if (t.edits && Object.keys(t.edits).length) setMode("blob");
          // Stop polling once we have something OR the task ended.
          const settled = !!t.preview_url
            || !!t.edits
            || ["done", "failed"].includes(t.status);
          if (settled) return;
        }
      } catch { /* keep polling */ }
      if (!cancelled) timer = setTimeout(tick, 2500);
    };
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [taskId, reloadKey]);

  const blobUrl = useMemo(() => {
    if (mode !== "blob" || !task?.edits) return null;
    return buildBlobUrl(task.edits);
  }, [mode, task]);

  // Revoke blob URL when it changes.
  useEffect(() => {
    if (!blobUrl) return;
    return () => { try { URL.revokeObjectURL(blobUrl); } catch { /* ignore */ } };
  }, [blobUrl]);

  const iframeSrc = mode === "blob" ? blobUrl
                   : mode === "url"  ? task?.preview_url
                   : null;

  return (
    <div data-testid="preview-pane" style={paneStyle}>
      <div style={toolbarStyle}>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <Eye size={12} style={{ color: ACCENT }} />
          {task?.preview_url && (
            <button data-testid="preview-tab-live"
                    onClick={() => setMode("url")}
                    style={pill(mode === "url", "#6dd4a1")}>Live</button>
          )}
          {task?.edits && Object.keys(task.edits).length > 0 && (
            <button data-testid="preview-tab-blob"
                    onClick={() => setMode("blob")}
                    style={pill(mode === "blob", ACCENT)}>Preview</button>
          )}
        </div>
        <div style={urlBarStyle}>
          {mode === "blob" ? "preview://local"
           : mode === "url"  ? task?.preview_url
           : "—"}
        </div>
        {task?.commit_sha && (
          <span style={shaStyle}>{task.commit_sha.slice(0, 7)}</span>
        )}
        {iframeSrc && (
          <button data-testid="preview-reload"
                  onClick={() => setReloadKey(k => k + 1)}
                  title="Reload preview"
                  style={iconBtn}>
            <RefreshCw size={11} />
          </button>
        )}
        {task?.preview_url && (
          <a data-testid="preview-open" href={task.preview_url}
             target="_blank" rel="noopener noreferrer" style={iconBtn}
             title="Open in new tab">
            <ExternalLink size={11} />
          </a>
        )}
      </div>

      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        {iframeSrc ? (
          <iframe
            key={reloadKey}
            ref={iframeRef}
            src={iframeSrc}
            sandbox="allow-scripts allow-same-origin allow-forms"
            title="Live preview"
            style={{ width: "100%", height: "100%", border: "none" }}
          />
        ) : task && task.status === "failed" ? (
          <Empty title="Task failed" sub={task.error || "Open the chat to retry."} />
        ) : task ? (
          <Empty title="ORA is working…" sub="Live preview will appear here when the task ships." spin />
        ) : (
          <Empty title="No preview yet"
                 sub="Ship a frontend task to see the result live." />
        )}
      </div>
    </div>
  );
}

function Empty({ title, sub, spin = false }) {
  return (
    <div style={{
      position: "absolute", inset: 0,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: 10,
      color: "var(--text-faint, #6b6557)",
    }}>
      {spin ? (
        <div style={{
          width: 28, height: 28, borderRadius: "50%",
          border: "2px solid rgba(255,138,42,0.18)",
          borderTopColor: ACCENT,
          animation: "aurem-spin 1s linear infinite",
        }}/>
      ) : (
        <span style={{ fontSize: 26, opacity: 0.35 }}>◈</span>
      )}
      <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{title}</span>
      <span style={{ fontSize: 11, opacity: 0.7, maxWidth: 260, textAlign: "center" }}>{sub}</span>
    </div>
  );
}

function buildBlobUrl(edits) {
  const entries = Object.entries(edits);
  const html = entries.find(([p]) => p.toLowerCase().endsWith(".html"));
  const cssFiles = entries.filter(([p]) => p.toLowerCase().endsWith(".css"));
  const jsFiles  = entries.filter(([p]) => /\.(js|jsx|ts|tsx)$/i.test(p));
  let doc = html ? html[1] : (
    `<!DOCTYPE html><html><head><meta charset="UTF-8">` +
    `<meta name="viewport" content="width=device-width,initial-scale=1">` +
    `<style>body{margin:0;font-family:system-ui;background:#07080d;color:#f4ecdc;padding:24px}</style>` +
    `</head><body><div id="root"></div></body></html>`
  );
  cssFiles.forEach(([, css]) => {
    doc = doc.replace(/<\/head>/i, `<style>${escapeBlock(css)}</style></head>`);
  });
  jsFiles.forEach(([, js]) => {
    doc = doc.replace(/<\/body>/i, `<script type="module">${escapeBlock(js)}</script></body>`);
  });
  return URL.createObjectURL(new Blob([doc], { type: "text/html" }));
}

function escapeBlock(s) {
  return (s || "").replace(/<\/script>/gi, "<\\/script>")
                  .replace(/<\/style>/gi,  "<\\/style>");
}

const paneStyle = {
  height: "100%", display: "flex", flexDirection: "column",
  background: "var(--bg, #07080d)",
  borderLeft: "1px solid var(--border, rgba(255,200,120,0.10))",
};
const toolbarStyle = {
  height: 42, display: "flex", alignItems: "center", gap: 8,
  padding: "0 12px", flexShrink: 0,
  borderBottom: "1px solid var(--border, rgba(255,200,120,0.10))",
};
const urlBarStyle = {
  flex: 1, fontSize: 10, color: "var(--text-faint, #6b6557)",
  fontFamily: "'JetBrains Mono', monospace",
  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
};
const shaStyle = {
  fontSize: 9, fontFamily: "'JetBrains Mono', monospace",
  color: ACCENT, flexShrink: 0,
};
const iconBtn = {
  background: "transparent", border: "none", cursor: "pointer",
  color: "var(--text-faint)", padding: 4, borderRadius: 4,
  display: "inline-flex", alignItems: "center",
};
const pill = (active, color) => ({
  fontSize: 10, fontWeight: 600, padding: "3px 9px", borderRadius: 12,
  border: "none", cursor: "pointer",
  background: active ? "rgba(255,138,42,0.16)" : "rgba(255,255,255,0.04)",
  color: active ? color : "var(--text-faint)",
});
