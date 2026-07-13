/**
 * components/AddLiveSiteModal.jsx — Iter 212m-203
 *
 * Lightweight prompt shown the FIRST time a user clicks the Preview
 * tab on a project that has no `preview_url` set.  User enters their
 * live site URL, hits Save, and the iframe loads immediately — no
 * bouncing to the legacy /projects edit form.
 *
 * Props
 *   projectName   string    Display name for the header.
 *   onSave        (url) => Promise   Persist the URL upstream.
 *   onCancel      () => void         Close the modal without saving.
 */
import React, { useState } from "react";

const RX = /^https?:\/\/[^\s]+$/i;

export default function AddLiveSiteModal({ projectName, onSave, onCancel }) {
  const [url, setUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    const clean = url.trim();
    if (!RX.test(clean)) {
      setErr("Enter a full URL (https://…)");
      return;
    }
    setErr("");
    setSaving(true);
    try {
      await onSave(clean);
    } catch (e) {
      setErr(e?.message || "Couldn't save. Try again.");
      setSaving(false);
    }
  };

  return (
    <div
      data-testid="add-live-site-overlay"
      onClick={onCancel}
      style={{
        position: "fixed", inset: 0, zIndex: 1200,
        background: "rgba(4,7,16,0.62)",
        backdropFilter: "blur(6px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        data-testid="add-live-site-dialog"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(460px, 100%)",
          background: "linear-gradient(180deg, rgba(20,24,34,0.98), rgba(13,16,24,0.98))",
          border: "1px solid rgba(245,158,11,0.4)",
          borderRadius: 12,
          padding: "22px 24px",
          color: "#e5e7eb",
          fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif',
          boxShadow: "0 30px 80px rgba(0,0,0,0.6)",
        }}
      >
        <div style={{
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: 10, letterSpacing: "0.18em", color: "#f59e0b",
          marginBottom: 6,
        }}>
          PREVIEW · SETUP
        </div>
        <div style={{ fontSize: 17, fontWeight: 700, color: "#f8fafc", marginBottom: 6 }}>
          Add your live site
        </div>
        <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.55, marginBottom: 14 }}>
          Paste the URL of the running deployment for{" "}
          <b style={{ color: "#e5e7eb" }}>{projectName || "this project"}</b>.
          The Preview tab will load it inside an iframe so you can see the
          site next to the chat.
        </div>

        <label style={{
          display: "block",
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: 10, letterSpacing: "0.14em", color: "#94a3b8",
          marginBottom: 5,
        }}>
          LIVE SITE URL
        </label>
        <input
          data-testid="add-live-site-input"
          type="url"
          autoFocus
          value={url}
          onChange={(e) => { setUrl(e.target.value); if (err) setErr(""); }}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          placeholder="https://your-app.vercel.app"
          style={{
            width: "100%",
            padding: "10px 12px",
            background: "#0a0e18",
            border: `1px solid ${err ? "#ef4444" : "#334155"}`,
            borderRadius: 8,
            fontSize: 13,
            color: "#e5e7eb",
            fontFamily: '"JetBrains Mono", monospace',
            outline: "none",
          }}
        />
        {err && (
          <div data-testid="add-live-site-error" style={{
            marginTop: 6, fontSize: 11, color: "#ef4444",
          }}>{err}</div>
        )}
        <div style={{ marginTop: 10, fontSize: 10, color: "#64748b" }}>
          🔒 Only used to render your site inside the Preview iframe. You can
          change it any time from Projects.
        </div>

        <div style={{
          marginTop: 18,
          display: "flex", gap: 8, justifyContent: "flex-end",
        }}>
          <button
            type="button"
            data-testid="add-live-site-cancel"
            onClick={onCancel}
            disabled={saving}
            style={{
              padding: "8px 14px",
              background: "transparent",
              border: "1px solid #334155",
              color: "#94a3b8",
              borderRadius: 8,
              fontSize: 12,
              fontFamily: '"JetBrains Mono", monospace',
              letterSpacing: "0.05em",
              cursor: saving ? "default" : "pointer",
            }}
          >
            CANCEL
          </button>
          <button
            type="button"
            data-testid="add-live-site-save"
            onClick={submit}
            disabled={saving || !url.trim()}
            style={{
              padding: "8px 18px",
              background: (saving || !url.trim()) ? "#3a2b0f" : "#f59e0b",
              color: (saving || !url.trim()) ? "#94a3b8" : "#000",
              border: "none",
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 700,
              fontFamily: '"JetBrains Mono", monospace',
              letterSpacing: "0.05em",
              cursor: (saving || !url.trim()) ? "default" : "pointer",
            }}
          >
            {saving ? "SAVING…" : "SAVE & OPEN"}
          </button>
        </div>
      </div>
    </div>
  );
}
