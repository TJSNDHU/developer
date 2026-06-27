/**
 * MermaidBlock.jsx — Iter 212m-61
 *
 * Renders a Mermaid.js diagram inside the chat.  Used by the
 * `/diagram <prompt>` chat command:
 *
 *   • Initialises mermaid lazily on first mount (heavy module —
 *     ~800 KB — kept out of the cold path).
 *   • Dark theme tuned to AuremCTO's UI (#0a0e1a background, the
 *     #e8a020 brand orange as the primary accent, monospaced
 *     `JetBrains Mono`).
 *   • Copy SVG / Copy Code buttons, same pattern as CodeBlock.
 *   • Parse errors are caught and shown inline — never crashes the
 *     chat.
 *   • Responsive: SVG is `viewBox`-sized + `width: 100%` so it
 *     scales on phones.
 */
import React, {
  useEffect, useRef, useState, useId, useCallback,
} from "react";
import mermaid from "mermaid";
import { Copy, Check, AlertTriangle, Network } from "lucide-react";

let _mermaidInitialised = false;
function _initMermaid() {
  if (_mermaidInitialised) return;
  mermaid.initialize({
    startOnLoad:    false,
    securityLevel:  "strict",
    theme:          "dark",
    fontFamily:     "'JetBrains Mono', monospace",
    themeVariables: {
      // Dark base — matches the chat surface.
      background:        "#0a0e1a",
      primaryColor:      "#1a1f2e",
      primaryTextColor:  "#e8ecf3",
      primaryBorderColor:"#e8a020",
      lineColor:         "#9aa3b2",
      secondaryColor:    "#1c2233",
      tertiaryColor:     "#11151f",
      noteBkgColor:      "#1c2233",
      noteTextColor:     "#e8ecf3",
      noteBorderColor:   "#e8a020",
    },
  });
  _mermaidInitialised = true;
}

export default function MermaidBlock({ code, title }) {
  const id = useId().replace(/:/g, "_");
  const [svg, setSvg] = useState("");
  const [error, setError] = useState(null);
  const [copiedSvg, setCopiedSvg] = useState(false);
  const [copiedCode, setCopiedCode] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        _initMermaid();
        const renderId = `mmd_${id}_${Date.now()}`;
        const { svg: out } = await mermaid.render(renderId, code || "");
        if (!cancelled) {
          setSvg(out);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setSvg("");
          setError(e?.message || String(e));
        }
      }
    })();
    return () => { cancelled = true; };
  }, [code, id]);

  const onCopySvg = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(svg);
      setCopiedSvg(true);
      setTimeout(() => setCopiedSvg(false), 1600);
    } catch { /* ignore */ }
  }, [svg]);

  const onCopyCode = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code || "");
      setCopiedCode(true);
      setTimeout(() => setCopiedCode(false), 1600);
    } catch { /* ignore */ }
  }, [code]);

  return (
    <div
      data-testid="mermaid-block"
      style={{
        marginTop: 10,
        borderRadius: 10,
        background: "#0a0e1a",
        border: "1px solid rgba(232,160,32,0.20)",
        overflow: "hidden",
        boxShadow: "0 0 24px -12px rgba(232,160,32,0.35)",
      }}
    >
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "8px 12px",
        background: "rgba(232,160,32,0.06)",
        borderBottom: "1px solid rgba(232,160,32,0.18)",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10.5,
      }}>
        <Network size={12} color="#e8a020" />
        <span data-testid="mermaid-block-title"
              style={{ color: "#e8a020", flex: 1, minWidth: 0,
                       overflow: "hidden", textOverflow: "ellipsis",
                       whiteSpace: "nowrap" }}>
          {title || "Diagram"}
        </span>
        <button
          type="button"
          data-testid="mermaid-copy-svg"
          onClick={onCopySvg}
          disabled={!svg}
          title="Copy SVG"
          style={_btn(copiedSvg)}
        >
          {copiedSvg ? <Check size={11} /> : <Copy size={11} />}
          {copiedSvg ? "Copied" : "SVG"}
        </button>
        <button
          type="button"
          data-testid="mermaid-copy-code"
          onClick={onCopyCode}
          title="Copy Mermaid source"
          style={_btn(copiedCode)}
        >
          {copiedCode ? <Check size={11} /> : <Copy size={11} />}
          {copiedCode ? "Copied" : "Code"}
        </button>
      </div>

      {/* Body */}
      {error ? (
        <div
          data-testid="mermaid-block-error"
          style={{
            padding: 14,
            background: "rgba(239,68,68,0.05)",
            color: "#fca5a5",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
            lineHeight: 1.5,
          }}
        >
          <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 6 }}>
            <AlertTriangle size={12} />
            <strong>Mermaid parse error</strong>
          </div>
          {error}
          <pre style={{
            marginTop: 8, padding: 8,
            background: "rgba(0,0,0,0.35)",
            borderRadius: 6,
            overflowX: "auto",
            color: "#c2c9d6",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
            fontSize: 10.5,
          }}>{code}</pre>
        </div>
      ) : (
        <div
          ref={wrapRef}
          data-testid="mermaid-block-svg"
          style={{
            padding: 14,
            overflowX: "auto",
            display: "flex",
            justifyContent: "center",
          }}
          // Render mermaid SVG.  `svg` is produced by `mermaid.render`
          // (which we initialised with securityLevel: 'strict' — it
          // strips event handlers + arbitrary HTML).
          dangerouslySetInnerHTML={{
            __html: svg ||
              "<div style='color:#9aa3b2; font-family:\"JetBrains Mono\", monospace; font-size:11px'>Rendering diagram…</div>",
          }}
        />
      )}
    </div>
  );
}

function _btn(active) {
  return {
    display: "inline-flex", alignItems: "center", gap: 4,
    padding: "3px 7px",
    background: active ? "rgba(34,197,94,0.18)" : "transparent",
    color: active ? "#86efac" : "var(--text-dim, #9aa3b2)",
    border: `1px solid ${active
      ? "rgba(34,197,94,0.45)"
      : "rgba(255,255,255,0.10)"}`,
    borderRadius: 5,
    fontSize: 9.5,
    cursor: "pointer",
    fontFamily: "'JetBrains Mono', monospace",
    fontWeight: 600,
    letterSpacing: 0.3,
  };
}
