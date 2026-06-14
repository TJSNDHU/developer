/**
 * CodeBlock.jsx — Monaco-powered code viewer for chat messages.
 *
 * Iter 148 — Replaces the plain `whiteSpace: pre-wrap` rendering of
 * fenced code in MessageBubble. Gives users:
 *   - VS Code "vs-dark" syntax highlighting (matches the app palette)
 *   - Line numbers
 *   - Copy-to-clipboard button (top-right, fades in on hover)
 *   - File-path / language chip header
 *   - Read-only — Monaco is rendered with editable=false; no IntelliSense
 *     popups, no cursor flicker.
 *
 * Lazy-loaded via React.Suspense so the 1.4 MB Monaco bundle never ships
 * with the initial JS — only loads when a message actually contains a
 * code fence.
 */
import React, { Suspense, lazy, useMemo, useState } from "react";
import { Copy, Check } from "lucide-react";

const MonacoEditor = lazy(() => import("@monaco-editor/react"));

// Map common fence languages → Monaco language ids.
const LANG_MAP = {
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  py: "python",
  python: "python",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  yml: "yaml",
  yaml: "yaml",
  json: "json",
  md: "markdown",
  markdown: "markdown",
  html: "html",
  css: "css",
  sql: "sql",
  rb: "ruby",
  go: "go",
  rs: "rust",
  java: "java",
  c: "c",
  cpp: "cpp",
  csharp: "csharp",
  php: "php",
  dockerfile: "dockerfile",
  toml: "ini",
};

function normalizeLang(raw) {
  if (!raw) return "plaintext";
  const k = String(raw).toLowerCase().trim();
  return LANG_MAP[k] || k || "plaintext";
}

export default function CodeBlock({ language, code, filename }) {
  const [copied, setCopied] = useState(false);
  const lang = useMemo(() => normalizeLang(language), [language]);
  const lineCount = useMemo(() => (code || "").split("\n").length, [code]);
  // Each line is ~19px in Monaco vs-dark; cap height at 480px so very
  // long files stay scrollable inside the chat bubble.
  const height = Math.min(Math.max(lineCount * 19 + 16, 80), 480);

  function doCopy() {
    if (!code) return;
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div data-testid="code-block" style={{
      position: "relative", margin: "12px 0", borderRadius: 8,
      border: "1px solid var(--border)", overflow: "hidden",
      background: "#1e1e1e",
    }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "6px 10px 6px 12px",
        background: "rgba(255,255,255,0.02)",
        borderBottom: "1px solid var(--border)",
        fontSize: 11, color: "var(--text-faint)",
        fontFamily: "'JetBrains Mono', monospace",
      }}>
        <span data-testid="code-block-lang">
          {filename ? <span style={{ color: "var(--text-dim)" }}>{filename} · </span> : null}
          {lang}
        </span>
        <button
          data-testid="code-block-copy"
          onClick={doCopy}
          className="btn-ghost"
          style={{
            padding: "3px 8px", fontSize: 10, display: "flex",
            alignItems: "center", gap: 4, background: "transparent",
            border: "1px solid var(--border)",
          }}
          aria-label="Copy code"
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <Suspense fallback={
        <pre style={{
          margin: 0, padding: 12, fontSize: 12,
          fontFamily: "'JetBrains Mono', monospace",
          color: "#d4d4d4", whiteSpace: "pre",
          overflowX: "auto", background: "#1e1e1e",
        }}>{code}</pre>
      }>
        <MonacoEditor
          height={height}
          defaultLanguage={lang}
          value={code}
          theme="vs-dark"
          options={{
            readOnly: true,
            domReadOnly: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            wordWrap: "on",
            fontSize: 12,
            fontFamily: "'JetBrains Mono', monospace",
            lineNumbers: "on",
            lineNumbersMinChars: 3,
            glyphMargin: false,
            folding: false,
            renderLineHighlight: "none",
            scrollbar: { vertical: "auto", horizontal: "auto" },
            padding: { top: 8, bottom: 8 },
            // Disable interactive cursor / context menu — read only.
            contextmenu: false,
            quickSuggestions: false,
            occurrencesHighlight: "off",
            selectionHighlight: false,
            renderWhitespace: "none",
          }}
        />
      </Suspense>
    </div>
  );
}
