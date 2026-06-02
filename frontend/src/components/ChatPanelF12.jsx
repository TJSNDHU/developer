/**
 * components/ChatPanelF12.jsx
 * ============================
 * PATCH for your existing ChatPanel.jsx
 *
 * Adds:
 *   1. F12 error badge on chat input — shows error count
 *   2. F12 payload attached to every message send
 *   3. Mode indicator pill (A/B/C/D/E) showing detected mode
 *   4. "Send to ORA" button when F12 errors exist
 *
 * HOW TO INTEGRATE:
 *   Copy the relevant sections into your existing ChatPanel.jsx.
 *   Search for each "// PATCH:" comment to find insertion points.
 */

import { useState, useEffect, useRef, useCallback } from "react";

// ─── F12 error badge hook ──────────────────────────────────────────────────
export function useF12Errors() {
  const [errorCount, setErrorCount] = useState(0);
  const [hasErrors,  setHasErrors]  = useState(false);

  useEffect(() => {
    const interval = setInterval(() => {
      if (window.__auremF12) {
        const count = window.__auremF12.errorCount();
        setErrorCount(count);
        setHasErrors(window.__auremF12.hasErrors());
      }
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const flush = useCallback(() => {
    if (!window.__auremF12) return null;
    return window.__auremF12.flush();
  }, []);

  const clear = useCallback(() => {
    if (window.__auremF12) window.__auremF12.clear();
    setErrorCount(0);
    setHasErrors(false);
  }, []);

  return { errorCount, hasErrors, flush, clear };
}


// ─── Mode detector (client-side, mirrors backend logic) ───────────────────
const MODE_RULES = [
  { mode: "D", color: "#f59e0b", label: "Debug",
    pattern: /error|bug|broken|crash|failing|exception|traceback|why is|not working|something broke|f12|console error|stack trace|TypeError|ValueError|500|404|422/i },
  { mode: "E", color: "#8b5cf6", label: "Audit",
    pattern: /\baudit\b|\breview\s+(my\s+)?(code|repo|codebase)\b|what.s wrong with|security (check|scan)|tech debt|code quality|health check/i },
  { mode: "C", color: "#3b82f6", label: "Code task",
    pattern: /add |create |build |implement |fix |update |refactor |write |generate |make |ship /i },
  { mode: "B", color: "#10b981", label: "Advice",
    pattern: /should i|what.s better|which is|how should|give me ideas|compare|recommend|what do you think/i },
  { mode: "A", color: "#6b7280", label: "Chat",
    pattern: /.*/ }, // fallback
];

export function detectMode(message) {
  for (const rule of MODE_RULES) {
    if (rule.pattern.test(message)) return rule;
  }
  return MODE_RULES[MODE_RULES.length - 1];
}


// ─── F12 error badge component ────────────────────────────────────────────
export function F12Badge({ errorCount, hasErrors, onSendToORA }) {
  if (!hasErrors) return null;

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 6,
      padding: "4px 10px",
      background: "#ef444411",
      border: "1px solid #ef444433",
      borderRadius: 20,
      fontSize: 12,
      color: "#f87171",
      cursor: "pointer",
      userSelect: "none",
    }}
    onClick={onSendToORA}
    title="Click to send these errors to ORA for diagnosis"
    >
      <span style={{ width: 6, height: 6, background: "#ef4444", borderRadius: "50%", display: "inline-block" }}/>
      {errorCount} console error{errorCount !== 1 ? "s" : ""} — send to ORA
    </div>
  );
}


// ─── Mode pill component ──────────────────────────────────────────────────
export function ModePill({ mode }) {
  if (!mode || mode.mode === "A") return null;

  return (
    <div style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 4,
      padding: "2px 8px",
      background: `${mode.color}18`,
      border: `1px solid ${mode.color}44`,
      borderRadius: 12,
      fontSize: 11,
      color: mode.color,
      fontFamily: "monospace",
    }}>
      Mode {mode.mode} · {mode.label}
    </div>
  );
}


// ─── PATCH: Add to your existing ChatPanel sendMessage function ────────────
/**
 * In your existing sendMessage (or handleSubmit) function,
 * add these lines BEFORE building the request body:
 *
 *   const { flush: flushF12 } = useF12Errors();
 *
 *   // Inside sendMessage:
 *   const f12Payload = flushF12();  // grabs and clears captured errors
 *
 *   const body = {
 *     message: userMessage,
 *     // ... your existing fields ...
 *     f12_payload: f12Payload,     // ADD THIS
 *   };
 */


// ─── PATCH: Add to your existing ChatPanel JSX ───────────────────────────
/**
 * In your ChatPanel JSX, ABOVE the input box:
 *
 *   const f12 = useF12Errors();
 *   const [detectedMode, setDetectedMode] = useState(null);
 *
 *   // In onInputChange handler:
 *   setDetectedMode(detectMode(e.target.value));
 *
 *   // In JSX, just above the textarea/input:
 *   <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
 *     <ModePill mode={detectedMode} />
 *     <F12Badge
 *       errorCount={f12.errorCount}
 *       hasErrors={f12.hasErrors}
 *       onSendToORA={() => {
 *         const payload = f12.flush();
 *         const errorSummary = `F12 errors detected (${payload.console_errors?.length || 0} console, ${payload.network_errors?.length || 0} network). Please diagnose and fix.`;
 *         sendMessage(errorSummary, payload);
 *       }}
 *     />
 *   </div>
 */


// ─── PATCH: Update your api.js / backend request ──────────────────────────
/**
 * In lib/api.js or wherever you call the chat endpoint,
 * make sure f12_payload is passed through:
 *
 *   export async function sendChatMessage({ message, sessionId, f12Payload, ...rest }) {
 *     return fetch(`${BACKEND_URL}/api/chat/stream`, {
 *       method: "POST",
 *       headers: { "Content-Type": "application/json", ...authHeaders() },
 *       body: JSON.stringify({
 *         message,
 *         session_id: sessionId,
 *         f12_payload: f12Payload || null,   // ADD THIS
 *         ...rest,
 *       }),
 *     });
 *   }
 */

export default function ChatPanelF12Demo() {
  const f12           = useF12Errors();
  const [msg, setMsg] = useState("");
  const detectedMode  = msg ? detectMode(msg) : null;

  return (
    <div style={{ padding: "1rem", maxWidth: 640, fontFamily: "var(--font-sans)" }}>
      <div style={{ marginBottom: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <ModePill mode={detectedMode} />
        <F12Badge
          errorCount={f12.errorCount}
          hasErrors={f12.hasErrors}
          onSendToORA={() => alert("Would send F12 errors to ORA")}
        />
      </div>

      <textarea
        value={msg}
        onChange={e => setMsg(e.target.value)}
        placeholder="Type a message... ORA detects mode as you type"
        style={{
          width: "100%",
          minHeight: 80,
          borderRadius: "var(--border-radius-md)",
          border: "0.5px solid var(--color-border-secondary)",
          padding: "10px 12px",
          fontSize: 14,
          background: "var(--color-background-primary)",
          color: "var(--color-text-primary)",
          resize: "vertical",
          boxSizing: "border-box",
        }}
      />

      <div style={{ fontSize: 12, color: "var(--color-text-tertiary)", marginTop: 6 }}>
        Try typing: "why is my auth failing", "review my codebase", "add a login page"
      </div>
    </div>
  );
}
