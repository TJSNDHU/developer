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
 *   - Iter 212m-12 — line-leading [ISSUE]/[FIX]/[OK] tags → color
 *     pills (red / green / blue) so non-technical founders can
 *     scan findings, fixes and OK-confirmations at a glance.
 *   - everything else → plain text with pre-wrap
 */
import React, { useMemo } from "react";
import CodeBlock from "./CodeBlock";

const FENCE_RE = /```([a-zA-Z0-9_+\-.]*)?[ \t]*([^\n`]*)\n([\s\S]*?)```/g;

// Iter 212m-105 — Internal orchestrator markers that must NEVER reach
// the bubble. Same list mirrored in pytest test_iter212m105_sanitize.
// `INTERNAL_FENCES` covers BOTH legacy + future tool-protocol fences.
const INTERNAL_FENCES = new Set([
  "tool_call", "tool_calls", "tool_use", "tool_result", "tool_results",
  "tool_response", "function_call", "function_result", "function_response",
  "system", "system_prompt", "internal", "orchestrator", "scratchpad",
  "thinking", "chain_of_thought",
]);

function sanitizeForDisplay(raw) {
  if (!raw) return "";
  let out = raw;
  // 1. Strip LOOP_PHASE:plan / :execute / :verify / :scan / :ship prefixes
  //    (the leading metadata token send() injects, never user-visible).
  out = out.replace(/^LOOP_PHASE:[a-z_]+\s*\n+/i, "");
  // 2. Strip the [Working on project: …] context preamble we auto-prepend
  //    on every prompt — internal scoping context, not content.
  out = out.replace(/^\[Working on project:[^\]]+\]\s*\n+/i, "");
  // 3. Strip any fenced block whose lang label is in the internal set.
  //    The fence regex must match the same shape as FENCE_RE above.
  out = out.replace(
    /```([a-zA-Z0-9_+\-.]*)[ \t]*[^\n`]*\n[\s\S]*?```/g,
    (match, lang) => INTERNAL_FENCES.has((lang || "").toLowerCase().trim()) ? "" : match,
  );
  // 4. Collapse any 3+ consecutive newlines left over from the strips
  //    so the bubble doesn't have giant gaps where the fences used to be.
  out = out.replace(/\n{3,}/g, "\n\n");
  return out.trim() ? out : "";
}

// Iter 212m-12 — line-leading color tags. Case-insensitive. The
// matching tag is stripped from the displayed text; the rest of
// the line is wrapped in a tinted pill so the user sees ONE clean
// "🔴 it's broken because…" / "🟢 fix this…" / "🔵 already fine"
// row instead of dense paragraphs. Common aliases supported too
// (Issue:/Problem:/Bug:/Error: → red; Fix:/Action:/TODO: → green;
//  OK:/Working:/Looks good: → blue) so older ORA responses still
// colorize without the assistant having to relearn the tag scheme.
const TAG_RULES = [
  {
    kind: "issue",
    re: /^(\s*)(\[ISSUE\]|Issue:|Problem:|Bug:|Error:)\s*/i,
    color:      "var(--danger)",
    background: "rgba(239, 68, 68, 0.08)",
    border:     "rgba(239, 68, 68, 0.35)",
    label:      "ISSUE",
  },
  {
    kind: "fix",
    re: /^(\s*)(\[FIX\]|Fix:|Action:|TODO:|To fix:)\s*/i,
    color:      "rgb(74, 222, 128)",
    background: "rgba(74, 222, 128, 0.08)",
    border:     "rgba(74, 222, 128, 0.35)",
    label:      "FIX",
  },
  {
    kind: "ok",
    re: /^(\s*)(\[OK\]|OK:|Working:|Looks good:|Already fine:|Verified:)\s*/i,
    color:      "rgb(96, 165, 250)",
    background: "rgba(96, 165, 250, 0.08)",
    border:     "rgba(96, 165, 250, 0.35)",
    label:      "OK",
  },
];

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
          fontSize: 13,
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

// Iter 212m-12 — wrap each text segment's lines in optional color
// pills. Lines without a tag are passed through to the existing
// inline-code renderer untouched.
function renderTextSegment(text, keyPrefix) {
  if (!text) return null;
  const lines = text.split("\n");
  return lines.map((line, li) => {
    const rule = TAG_RULES.find((r) => r.re.test(line));
    if (!rule) {
      return (
        <React.Fragment key={`${keyPrefix}-l${li}`}>
          {renderInline(line, `${keyPrefix}-l${li}`)}
          {li < lines.length - 1 ? "\n" : null}
        </React.Fragment>
      );
    }
    const stripped = line.replace(rule.re, "$1");
    return (
      <React.Fragment key={`${keyPrefix}-l${li}`}>
        <span
          data-testid={`ora-line-${rule.kind}`}
          style={{
            display: "inline-block",
            padding: "2px 8px",
            margin: "2px 0",
            borderRadius: 4,
            background: rule.background,
            border: `1px solid ${rule.border}`,
            color: rule.color,
          }}
        >
          <span style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: "0.08em",
            marginRight: 8,
            opacity: 0.85,
          }}>{rule.label}</span>
          <span style={{ color: "var(--text)" }}>
            {renderInline(stripped, `${keyPrefix}-l${li}-i`)}
          </span>
        </span>
        {li < lines.length - 1 ? "\n" : null}
      </React.Fragment>
    );
  });
}

export default function RenderedMessage({ text }) {
  // Iter 212m-105 — Strip internal orchestrator markers before render.
  // Tool-call / tool-result / system_prompt fences and LOOP_PHASE
  // prefixes are MACHINE-only and were leaking into the chat bubble as
  // raw "```tool_call …```" code blocks. Sanitize at the boundary.
  const cleaned = useMemo(() => sanitizeForDisplay(text || ""), [text]);
  const segments = useMemo(() => splitFences(cleaned), [cleaned]);
  if (segments.length === 0) {
    return (
      <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {renderTextSegment(cleaned, "root")}
      </span>
    );
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
                fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
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
            {renderTextSegment(s.value, `t${i}`)}
          </span>
        );
      })}
    </div>
  );
}
