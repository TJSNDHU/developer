/**
 * RenderedMessage.jsx — Parse assistant text and render code fences via
 * Monaco (CodeBlock), plain prose as-is.
 *
 * Iter 148 — pulled out of MessageBubble so the bubble file doesn't grow
 * past the 50-line/component soft-limit. Handles:
 *   - ```lang \n code \n ``` blocks → CodeBlock
 *   - ```lang filename.ext \n code \n ``` → CodeBlock with filename
 *   - ```aurem-handoff blocks are LEFT INTACT (not parsed here) because
 *     MessageBubble has its own handoff-brief renderer + ShipDialog
 *     wiring; we only operate on text BEFORE the handoff fence
 *     (handled by the caller, see MessageBubble usage).
 *   - inline `code` spans (single backtick) → <code> with monospace
 *     styling
 *   - everything else → plain text with pre-wrap
 */
import React, { useMemo } from "react";
import CodeBlock from "./CodeBlock";

const FENCE_RE = /```([a-zA-Z0-9_+\-.]*)?[ \t]*([^\n`]*)\n([\s\S]*?)```/g;

function splitFences(text) {
  if (!text) return [];
  const out = [];
  let lastIdx = 0;
  let m;
  // Reset regex state per call (global regex).
  FENCE_RE.lastIndex = 0;
  while ((m = FENCE_RE.exec(text)) !== null) {
    if (m.index > lastIdx) {
      out.push({ kind: "text", value: text.slice(lastIdx, m.index) });
    }
    out.push({
      kind: "code",
      lang: (m[1] || "").trim() || "plaintext",
      filename: (m[2] || "").trim() || null,
      code: m[3].replace(/\n$/, ""),
    });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < text.length) {
    out.push({ kind: "text", value: text.slice(lastIdx) });
  }
  return out;
}

// Inline single-backtick code spans inside a plain-text segment.
function renderInline(text, keyPrefix) {
  if (!text) return null;
  const parts = text.split(/(`[^`\n]+`)/g);
  return parts.map((p, i) => {
    if (p.startsWith("`") && p.endsWith("`") && p.length > 2) {
      return (
        <code key={`${keyPrefix}-${i}`} style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12,
          padding: "1px 5px",
          borderRadius: 3,
          background: "rgba(255,255,255,0.06)",
          color: "var(--accent-2)",
        }}>
          {p.slice(1, -1)}
        </code>
      );
    }
    return <React.Fragment key={`${keyPrefix}-${i}`}>{p}</React.Fragment>;
  });
}

export default function RenderedMessage({ text }) {
  const segments = useMemo(() => splitFences(text), [text]);
  if (segments.length === 0) {
    return <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{text}</span>;
  }
  return (
    <div data-testid="rendered-message">
      {segments.map((s, i) => {
        if (s.kind === "code") {
          // Leave aurem-handoff fences as plain text — caller renders them.
          if (s.lang === "aurem-handoff") {
            return (
              <pre key={i} style={{
                whiteSpace: "pre-wrap", margin: "8px 0",
                fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
              }}>{"```aurem-handoff\n" + s.code + "\n```"}</pre>
            );
          }
          return (
            <CodeBlock
              key={i}
              language={s.lang}
              code={s.code}
              filename={s.filename}
            />
          );
        }
        return (
          <span key={i} style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {renderInline(s.value, `t${i}`)}
          </span>
        );
      })}
    </div>
  );
}
