/**
 * components/SuggestionBoxModal.jsx — Iter 212m-193
 *
 * Minimal user-facing surface for the Founder Suggestion Box.
 * Submits to POST /suggestions and shows the server's own rate-limit
 * message on 429 (no client-side counter — the server is the single
 * source of truth for "already submitted today").
 */
import React, { useState } from "react";
import { api } from "../lib/api";

export default function SuggestionBoxModal({ open, onClose }) {
  const [text, setText] = useState("");
  const [state, setState] = useState({ kind: "idle" });

  if (!open) return null;

  async function submit(e) {
    e.preventDefault();
    if (state.kind === "sending") return;
    if (text.trim().length < 8) {
      setState({ kind: "error", message: "Please share a bit more — at least 8 characters." });
      return;
    }
    setState({ kind: "sending" });
    try {
      const r = await api.post("/suggestions", { text: text.trim() });
      setState({ kind: "success", message: r.data?.message || "Sent." });
      setText("");
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.message || "Something went wrong.";
      setState({
        kind: "error",
        message: status === 429
          ? detail
          : `Failed to send: ${detail}`,
      });
    }
  }

  return (
    <div
      data-testid="suggestion-box-modal"
      onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
      style={{
        position: "fixed", inset: 0, zIndex: 100,
        background: "rgba(0,0,0,0.65)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 20,
      }}>
      <div style={{
        background: "var(--panel-1, #161616)",
        border: "1px solid var(--border, #2a2a2a)",
        borderRadius: 8, width: "100%", maxWidth: 520,
        padding: "20px 22px",
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <h2 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "var(--text, #e5e5e5)" }}>
            Suggest a feature to the founder
          </h2>
          <button
            data-testid="suggestion-box-close"
            onClick={onClose}
            style={{
              background: "transparent", border: "none", color: "var(--text-faint, #999)",
              fontSize: 18, cursor: "pointer", padding: 4, lineHeight: 1,
            }}>×</button>
        </div>
        <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--text-dim, #a3a3a3)", lineHeight: 1.55 }}>
          One suggestion per day. Your idea is briefly analysed by an AI (benefits, risks, effort)
          for the founder&apos;s review, but the tick/cross decision is always human.
        </p>

        {state.kind === "success" ? (
          <div
            data-testid="suggestion-box-success"
            style={{
              padding: "14px 16px", background: "rgba(74,222,128,0.08)",
              border: "1px solid rgba(74,222,128,0.35)", borderRadius: 6,
              color: "#4ade80", fontSize: 13, lineHeight: 1.55, marginBottom: 12,
            }}>
            {state.message}
          </div>
        ) : (
          <form onSubmit={submit}>
            <textarea
              data-testid="suggestion-box-textarea"
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={4000}
              rows={6}
              placeholder="What would make Aurem CTO better for you?"
              style={{
                width: "100%", padding: "10px 12px", fontSize: 13,
                background: "var(--panel-2, #1c1c1c)",
                color: "var(--text, #e5e5e5)",
                border: "1px solid var(--border, #2a2a2a)", borderRadius: 6,
                fontFamily: "inherit", resize: "vertical", lineHeight: 1.5,
              }}
            />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 10 }}>
              <span style={{ fontSize: 11, color: "var(--text-faint, #888)" }}>
                {text.length} / 4000
              </span>
              <button
                type="submit"
                data-testid="suggestion-box-submit"
                disabled={state.kind === "sending"}
                style={{
                  padding: "7px 16px", fontSize: 12, fontWeight: 600,
                  background: state.kind === "sending" ? "var(--panel-2)" : "var(--accent-2, #f97316)",
                  color: state.kind === "sending" ? "var(--text-faint)" : "#000",
                  border: "none", borderRadius: 4, cursor: "pointer",
                  textTransform: "uppercase", letterSpacing: 0.6,
                }}>
                {state.kind === "sending" ? "Sending…" : "Send suggestion"}
              </button>
            </div>
          </form>
        )}

        {state.kind === "error" && (
          <div
            data-testid="suggestion-box-error"
            style={{
              marginTop: 10, padding: "8px 12px", fontSize: 12,
              background: "rgba(251,113,133,0.08)",
              border: "1px solid rgba(251,113,133,0.32)", borderRadius: 4,
              color: "#fb7185",
            }}>
            {state.message}
          </div>
        )}
      </div>
    </div>
  );
}
