/**
 * components/AdminRobotGuide.jsx — Iter 212m-187
 *
 * Admin editor for the ORA robot welcome messages shown on the
 * /signup and /login windows. Empty field = built-in default message.
 *
 * Backend:
 *   GET /api/aurem-dev/admin/robot-guide
 *   PUT /api/aurem-dev/admin/robot-guide
 *   (public read: GET /api/aurem-dev/auth/robot-guide)
 */
import React, { useState, useEffect } from "react";
import { Save, Loader2, RotateCcw } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";
import RobotGuide, { RobotGuideKeyframes } from "./RobotGuide";

const MAX_LEN = 600;

const DEFAULTS = {
  signup_message: `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — creates your account instantly, no password needed.`,
  login_message:  `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — one tap, no password.`,
};

function Field({ label, hint, value, onChange, previewFallback, testid }) {
  return (
    <div style={{ marginBottom: 26 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginBottom: 8 }}>{hint}</div>
      <textarea
        data-testid={testid}
        value={value}
        maxLength={MAX_LEN}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Leave empty to use the default message"
        rows={3}
        style={{
          width: "100%", resize: "vertical", padding: "10px 12px",
          background: "var(--bg-elev)", color: "var(--text)",
          border: "1px solid var(--border)", borderRadius: 6,
          fontSize: 13, fontFamily: "inherit", lineHeight: 1.5,
        }}
      />
      <div style={{ fontSize: 10.5, color: "var(--text-faint)", margin: "4px 0 10px", textAlign: "right" }}>
        {value.length}/{MAX_LEN}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 6 }}>Live preview:</div>
      <RobotGuide
        testid={`${testid}-preview`}
        message={value.trim() || previewFallback}
      />
    </div>
  );
}

export default function AdminRobotGuide() {
  const [signupMsg, setSignupMsg] = useState("");
  const [loginMsg, setLoginMsg] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get("/admin/robot-guide")
      .then((r) => {
        setSignupMsg(r.data.signup_message || "");
        setLoginMsg(r.data.login_message || "");
      })
      .catch(() => toast({ message: "Failed to load robot guide settings", kind: "error" }))
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/admin/robot-guide", {
        signup_message: signupMsg,
        login_message: loginMsg,
      });
      toast({ message: "Robot guide messages saved — live immediately", kind: "success" });
    } catch {
      toast({ message: "Save failed", kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 40, display: "grid", placeItems: "center", color: "var(--text-faint)" }}>
        <Loader2 size={18} className="spin" />
      </div>
    );
  }

  return (
    <div data-testid="admin-robot-guide" style={{ padding: "24px 20px", maxWidth: 720 }}>
      <RobotGuideKeyframes />
      <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 6px", color: "var(--text)" }}>
        Robot Guide Messages
      </h1>
      <p style={{ fontSize: 12.5, color: "var(--text-dim)", margin: "0 0 22px", lineHeight: 1.6 }}>
        Customize the ORA robot welcome wording on the signup and login windows.
        Basic HTML like <code>&lt;strong&gt;</code> and{" "}
        <code>&lt;span class="ora-arrow"&gt;👇&lt;/span&gt;</code> works.
        Leave a field empty to fall back to the default message.
      </p>

      <Field
        label="Signup window welcome message"
        hint="Shown to new visitors on /signup before they start typing."
        value={signupMsg}
        onChange={setSignupMsg}
        previewFallback={DEFAULTS.signup_message}
        testid="robot-guide-signup-input"
      />
      <Field
        label="Login window welcome message"
        hint="Shown to returning users on /login before they start typing."
        value={loginMsg}
        onChange={setLoginMsg}
        previewFallback={DEFAULTS.login_message}
        testid="robot-guide-login-input"
      />

      <div style={{ display: "flex", gap: 10 }}>
        <button
          data-testid="robot-guide-save-btn"
          onClick={save}
          disabled={saving}
          style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            padding: "10px 18px", borderRadius: 6, border: "none",
            background: "var(--accent, #f59e0b)", color: "#111",
            fontSize: 13, fontWeight: 600, cursor: "pointer",
            opacity: saving ? 0.7 : 1,
          }}
        >
          {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
          Save changes
        </button>
        <button
          data-testid="robot-guide-reset-btn"
          onClick={() => { setSignupMsg(""); setLoginMsg(""); }}
          style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            padding: "10px 14px", borderRadius: 6,
            border: "1px solid var(--border)", background: "transparent",
            color: "var(--text-dim)", fontSize: 13, cursor: "pointer",
          }}
        >
          <RotateCcw size={13} />
          Reset to defaults
        </button>
      </div>
    </div>
  );
}
