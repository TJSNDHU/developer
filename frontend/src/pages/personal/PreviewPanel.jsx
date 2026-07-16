/**
 * pages/personal/PreviewPanel.jsx — Iter 212m-239 — Tier 2.5
 *
 * Live preview embedded inside the DraftReview screen.
 *
 * Routing decision (revised Tier 2.5 scope):
 *   - JS-based stacks (nextjs-node, vue-express, plain-html) → Sandpack
 *     in-browser preview. Zero server cost, <2s to first paint.
 *   - react-fastapi (Python) → E2B via backend API. TTL 20 min,
 *     auto-cleanup by cron. Falls back to a disabled-state card if
 *     E2B isn't configured.
 */
import React, { useMemo, useState, useEffect } from "react";
import { Sandpack } from "@codesandbox/sandpack-react";
import { Loader, ExternalLink, Terminal } from "lucide-react";
import { api } from "../../lib/api";

/** Convert an AUREM draft's `files` array → Sandpack's virtual FS shape. */
function toSandpackFiles(files) {
  const out = {};
  for (const f of files || []) {
    if (!f?.path || typeof f.content !== "string") continue;
    // Sandpack expects absolute-style keys (leading '/').
    const key = f.path.startsWith("/") ? f.path : `/${f.path}`;
    out[key] = { code: f.content };
  }
  return out;
}

/** Pick Sandpack's template for the stack. */
function templateFor(stack) {
  if (stack === "nextjs-node") return "nextjs";
  if (stack === "vue-express") return "vue";
  if (stack === "plain-html")  return "static";
  return null;   // signals E2B path
}

export default function PreviewPanel({ draft }) {
  const stack = draft?.stack_detected || "";
  const files = draft?.files || [];
  const template = templateFor(stack);

  if (template) return <SandpackBrowserPreview files={files} template={template} />;
  return <E2BBackendPreview draftId={draft?.draft_id} />;
}


function SandpackBrowserPreview({ files, template }) {
  const sp = useMemo(() => toSandpackFiles(files), [files]);
  if (Object.keys(sp).length === 0) {
    return (
      <div data-testid="preview-empty" style={{ textAlign: "center", padding: 40, color: "#8B8B7D" }}>
        Preview will appear here once your draft has files.
      </div>
    );
  }
  return (
    <div data-testid="preview-sandpack" style={{ borderRadius: 12, overflow: "hidden" }}>
      <Sandpack
        template={template}
        files={sp}
        options={{
          showNavigator: false,
          showTabs:      true,
          showLineNumbers: true,
          editorHeight: 520,
          editorWidthPercentage: 40,
        }}
        theme={{
          colors: {
            surface1: "#FFFFFF",
            surface2: "#F4F3EE",
            surface3: "#E5E5DF",
            accent:   "#E07A5F",
          },
          font: { body: "Manrope, system-ui, sans-serif" },
        }}
      />
    </div>
  );
}


function E2BBackendPreview({ draftId }) {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.post(`/scaffold/${draftId}/preview`, {});
        if (cancelled) return;
        setState({ loading: false, ok: true, ...r.data });
      } catch (e) {
        if (cancelled) return;
        const status = e?.response?.status;
        setState({
          loading: false, ok: false,
          disabled: status === 503,
          reason: e?.response?.data?.detail?.reason || "unknown",
        });
      }
    })();
    return () => { cancelled = true; };
  }, [draftId]);

  if (state.loading) {
    return (
      <div data-testid="preview-loading" style={loadingStyle}>
        <Loader size={20} className="spin" />
        <p style={{ margin: 0 }}>Spinning up a live sandbox…</p>
        <p style={{ fontSize: 12, color: "#8B8B7D", margin: 0 }}>
          This takes about 20 seconds for the first preview.
        </p>
      </div>
    );
  }

  if (state.disabled) {
    return (
      <div data-testid="preview-disabled" style={{ ...loadingStyle, background: "rgba(224,122,95,0.05)" }}>
        <Terminal size={20} color="#E07A5F" />
        <p style={{ fontWeight: 600, margin: 0 }}>Live preview isn&apos;t available yet.</p>
        <p style={{ fontSize: 13, color: "#6B6B63", margin: 0, textAlign: "center", maxWidth: 320 }}>
          You can still ship this draft — the preview feature just isn&apos;t
          switched on in this environment.
        </p>
      </div>
    );
  }

  if (!state.ok) {
    return (
      <div data-testid="preview-error" style={loadingStyle}>
        <p style={{ margin: 0, color: "#4B4B45" }}>
          The preview didn&apos;t start this time. Try again in a moment.
        </p>
      </div>
    );
  }

  return (
    <div data-testid="preview-e2b" style={{ borderRadius: 12, border: "1px solid #E5E5DF", overflow: "hidden" }}>
      <div style={{
        padding: "8px 14px", background: "#F4F3EE",
        display: "flex", alignItems: "center", gap: 10,
        fontSize: 12, color: "#6B6B63", borderBottom: "1px solid #E5E5DF",
      }}>
        <span>Live preview</span>
        <a href={state.url} target="_blank" rel="noreferrer noopener"
           style={{ color: "#E07A5F", display: "inline-flex", alignItems: "center", gap: 4 }}>
          Open in new tab <ExternalLink size={12} />
        </a>
        <span style={{ marginLeft: "auto" }}>
          expires in ~{Math.max(0, Math.round((state.expires_at - Date.now() / 1000) / 60))}m
        </span>
      </div>
      <iframe
        data-testid="preview-e2b-iframe"
        src={state.url}
        title="Live preview"
        style={{ width: "100%", height: 520, border: "none", background: "#fff" }}
      />
    </div>
  );
}


const loadingStyle = {
  display: "flex", flexDirection: "column", alignItems: "center", gap: 12,
  padding: 60, textAlign: "center",
  background: "#FFFFFF", borderRadius: 12, border: "1px solid #E5E5DF",
};
