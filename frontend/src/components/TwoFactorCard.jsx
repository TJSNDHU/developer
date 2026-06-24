/**
 * TwoFactorCard.jsx — Iter 212m-20
 *
 * Admin Settings card for enabling/disabling TOTP-based 2FA.
 *
 * Three states:
 *   - LOADING       — fetching /admin/2fa/status
 *   - DISABLED      — "Enable two-factor authentication" CTA
 *   - ENROLLING     — modal showing QR + secret + 8 backup codes,
 *                     plus a 6-digit code input to confirm
 *   - ENABLED       — green badge + "Disable" CTA (requires current
 *                     TOTP code OR a backup code)
 *
 * Backend contract: /api/aurem-dev/admin/2fa/status |
 *                   /enroll-start | /enroll-verify | /disable.
 */
import React, { useEffect, useState, useCallback } from "react";
import { ShieldCheck, ShieldAlert, QrCode, Copy as CopyIcon } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";

export default function TwoFactorCard() {
  const [status,  setStatus]  = useState(null);   // {enabled, has_pending, backup_codes_remaining}
  const [busy,    setBusy]    = useState(false);
  const [enroll,  setEnroll]  = useState(null);   // {otpauth_url, qr_png, secret, backup_codes}
  const [code,    setCode]    = useState("");
  const [disableMode, setDisableMode] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/admin/2fa/status");
      setStatus(r.data);
    } catch (e) {
      setStatus({ enabled: false, has_pending: false, backup_codes_remaining: 0 });
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function startEnrollment() {
    setBusy(true);
    try {
      const r = await api.post("/admin/2fa/enroll-start");
      setEnroll(r.data);
      setCode("");
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Could not start enrollment", kind: "error" });
    } finally { setBusy(false); }
  }

  async function confirmEnrollment(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/admin/2fa/enroll-verify", { code: code.replace(/\D/g, "") });
      toast({ message: "Two-factor authentication enabled ✓", kind: "success" });
      setEnroll(null); setCode("");
      await refresh();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Invalid code", kind: "error" });
    } finally { setBusy(false); }
  }

  async function disable2fa(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const cleaned = code.replace(/\D/g, "");
      // 6 digits = TOTP code; longer = backup code.
      const payload = cleaned.length === 6
        ? { code: cleaned }
        : { backup_code: code.trim() };
      await api.post("/admin/2fa/disable", payload);
      toast({ message: "Two-factor disabled", kind: "info" });
      setDisableMode(false); setCode("");
      await refresh();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Invalid code", kind: "error" });
    } finally { setBusy(false); }
  }

  if (status === null) return (
    <div style={{ padding: 14, color: "var(--text-faint)", fontSize: 12 }}>
      Loading 2FA status…
    </div>
  );

  return (
    <div
      data-testid="admin-2fa-card"
      data-enabled={status.enabled ? "true" : "false"}
      style={{
        marginTop: 28, paddingTop: 20,
        borderTop: "1px solid var(--line, rgba(255,255,255,0.06))",
      }}
    >
      <h3 style={{ fontSize: 13, margin: "0 0 14px", display: "flex", alignItems: "center", gap: 8 }}>
        {status.enabled
          ? <ShieldCheck size={14} color="#6DD4A1" />
          : <ShieldAlert  size={14} color="#F59E0B" />}
        Two-factor authentication
      </h3>

      <div style={{
        padding: 14, borderRadius: 6,
        background: status.enabled
          ? "rgba(109,212,161,0.06)"
          : "rgba(245,158,11,0.06)",
        border: status.enabled
          ? "1px solid rgba(109,212,161,0.25)"
          : "1px solid rgba(245,158,11,0.25)",
      }}>
        {status.enabled ? (
          <>
            <div style={{ fontSize: 12, color: "#6DD4A1", marginBottom: 6 }}>
              ✓ Enabled — admin login requires a 6-digit code from your authenticator app.
            </div>
            <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 12 }}>
              {status.backup_codes_remaining} backup code
              {status.backup_codes_remaining === 1 ? "" : "s"} remaining.
            </div>
            {!disableMode ? (
              <button
                data-testid="admin-2fa-disable-cta"
                onClick={() => setDisableMode(true)}
                className="btn-ghost"
                style={{ fontSize: 12 }}
              >
                Disable 2FA…
              </button>
            ) : (
              <form data-testid="admin-2fa-disable-form" onSubmit={disable2fa}
                style={{ display: "grid", gap: 8, maxWidth: 280 }}>
                <span className="label-mini">
                  Enter your current 6-digit code (or a backup code) to disable:
                </span>
                <input
                  data-testid="admin-2fa-disable-code-input"
                  className="input" autoFocus
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="000000  or  XXXX-XXXX-XXXX"
                  style={{ fontFamily: "'JetBrains Mono', monospace", letterSpacing: ".2em", textAlign: "center" }}
                  maxLength={14}
                />
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    data-testid="admin-2fa-disable-confirm"
                    type="submit" disabled={busy || code.length < 6}
                    className="btn-primary" style={{ flex: 1, justifyContent: "center" }}
                  >
                    {busy ? "Disabling…" : "Disable"}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setDisableMode(false); setCode(""); }}
                    className="btn-ghost" style={{ flex: 1, justifyContent: "center" }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </>
        ) : (
          <>
            <div style={{ fontSize: 12, color: "#F59E0B", marginBottom: 6 }}>
              ⚠ Not enabled — your admin account is protected by password only.
            </div>
            <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 12 }}>
              Add a second factor (Google Authenticator, 1Password, Authy)
              so a stolen password alone can&apos;t reach the admin panel.
            </div>
            {!enroll ? (
              <button
                data-testid="admin-2fa-enroll-cta"
                onClick={startEnrollment}
                disabled={busy}
                className="btn-primary"
                style={{ fontSize: 12 }}
              >
                <QrCode size={13} style={{ marginRight: 6 }} />
                {busy ? "Generating…" : "Enable two-factor authentication"}
              </button>
            ) : (
              <EnrollPanel
                enroll={enroll}
                code={code}
                onCode={setCode}
                onConfirm={confirmEnrollment}
                onCancel={() => { setEnroll(null); setCode(""); }}
                busy={busy}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}


function EnrollPanel({ enroll, code, onCode, onConfirm, onCancel, busy }) {
  const copy = async (text, label) => {
    try { await navigator.clipboard.writeText(text); toast({ message: `${label} copied`, kind: "info" }); }
    catch { /* ignore */ }
  };
  return (
    <div data-testid="admin-2fa-enroll-panel" style={{ display: "grid", gap: 16, marginTop: 8 }}>
      <div style={{
        padding: 14, borderRadius: 6,
        border: "1px solid rgba(255,200,120,0.20)",
        background: "rgba(255,200,120,0.04)",
        color: "#FFD58A", fontSize: 11, lineHeight: 1.5,
      }}>
        <strong>Step 1.</strong> Scan the QR with Google Authenticator / 1Password / Authy.
        <br />
        <strong>Step 2.</strong> Save the backup codes below — they&apos;re shown ONCE.
        <br />
        <strong>Step 3.</strong> Type the 6-digit code your app shows to activate.
      </div>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <img
          data-testid="admin-2fa-qr"
          src={enroll.qr_png}
          alt="2FA QR code"
          style={{ width: 180, height: 180, borderRadius: 6, background: "white", padding: 6 }}
        />
        <div style={{ flex: 1, minWidth: 220 }}>
          <span className="label-mini">Manual entry secret</span>
          <div
            data-testid="admin-2fa-secret"
            onClick={() => copy(enroll.secret, "Secret")}
            style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
              padding: "6px 8px", marginTop: 4, marginBottom: 12,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 4, cursor: "pointer",
              display: "flex", alignItems: "center", gap: 6,
              wordBreak: "break-all",
            }}
            title="Click to copy"
          >
            <CopyIcon size={11} />
            <span>{enroll.secret}</span>
          </div>

          <span className="label-mini">Backup codes (save these!)</span>
          <div
            data-testid="admin-2fa-backup-codes"
            style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
              padding: "8px 10px", marginTop: 4,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,138,42,0.30)",
              borderRadius: 4,
              display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4,
            }}
          >
            {(enroll.backup_codes || []).map((c) => (
              <span key={c} style={{ color: "var(--text)" }}>{c}</span>
            ))}
          </div>
          <button
            type="button"
            data-testid="admin-2fa-copy-backups"
            onClick={() => copy((enroll.backup_codes || []).join("\n"), "Backup codes")}
            className="btn-ghost"
            style={{ fontSize: 10, marginTop: 6 }}
          >
            <CopyIcon size={10} style={{ marginRight: 4 }} /> Copy all
          </button>
        </div>
      </div>

      <form onSubmit={onConfirm} style={{ display: "grid", gap: 8, maxWidth: 320 }}>
        <span className="label-mini">Enter the 6-digit code from your authenticator</span>
        <input
          data-testid="admin-2fa-confirm-input"
          className="input"
          value={code}
          onChange={(e) => onCode(e.target.value)}
          placeholder="000000"
          inputMode="numeric"
          pattern="[0-9]{6}"
          maxLength={6}
          autoFocus
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.4em", fontSize: 18, textAlign: "center",
          }}
        />
        <div style={{ display: "flex", gap: 8 }}>
          <button
            data-testid="admin-2fa-confirm-submit"
            type="submit" disabled={busy || code.replace(/\D/g, "").length < 6}
            className="btn-primary" style={{ flex: 1, justifyContent: "center" }}
          >
            {busy ? "Activating…" : "Activate 2FA"}
          </button>
          <button
            type="button"
            data-testid="admin-2fa-enroll-cancel"
            onClick={onCancel}
            className="btn-ghost" style={{ flex: 1, justifyContent: "center" }}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
