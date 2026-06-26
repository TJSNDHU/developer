/**
 * components/FounderOfferCard.jsx — In-chat card promoting the
 * founder's free SEO fix.
 *
 * Visibility rules (computed once `/status` + `/user-status` resolve):
 *   - Hidden when `has_fully_claimed === true`
 *   - Hidden when `remaining === 0`
 *   - Hidden when `days_since_signup > 3`
 *   - Hidden when no project_id is active (nothing to fix)
 *
 * Mounted above the chat input in `ChatPanel.jsx`. Polls `/status`
 * every 30 s so the badge stays roughly real-time even when the user
 * leaves the tab open.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { toast } from "./Toast";

const POLL_MS = 30_000;

export default function FounderOfferCard({ projectId }) {
  const [status, setStatus] = useState(null);          // { remaining, total, is_active }
  const [userStatus, setUserStatus] = useState(null);  // { has_fully_claimed, days_since_signup, repos_claimed }
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("idle");          // idle | preview | running | done | error
  const [claim, setClaim] = useState(null);            // { claim_id, preview }
  const [error, setError] = useState(null);

  // ── Polling ──────────────────────────────────────────────────
  const refresh = useCallback(async () => {
    try {
      const [s, u] = await Promise.allSettled([
        api.get("/founder-offer/status"),
        api.get("/founder-offer/user-status"),
      ]);
      if (s.status === "fulfilled") setStatus(s.value.data);
      if (u.status === "fulfilled") setUserStatus(u.value.data);
    } catch (_e) {
      // Best-effort polling — never raise to the UI.
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  // ── Visibility ───────────────────────────────────────────────
  const visible = useMemo(() => {
    if (!projectId || projectId === "home") return false;
    if (!status || !userStatus) return false;
    if (!status.is_active) return false;
    if ((status.remaining ?? 0) <= 0) return false;
    if (userStatus.has_fully_claimed) return false;
    if (typeof userStatus.days_since_signup === "number" &&
        userStatus.days_since_signup > 3) return false;
    return true;
  }, [projectId, status, userStatus]);

  // ── Counter color ────────────────────────────────────────────
  const counterColor = useMemo(() => {
    const r = status?.remaining ?? 0;
    if (r <= 10) return "#ef4444";    // red
    if (r <= 50) return "#f97316";    // orange
    return "#22c55e";                  // green
  }, [status]);

  // ── Handlers ─────────────────────────────────────────────────
  const handleClaim = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.post("/founder-offer/claim", {
        repo_id: projectId,
        site_url: "",
      });
      if (!r.data?.success) {
        if (r.data?.action === "upgrade") {
          setError("You've already claimed the maximum of 3 repos. Upgrade to fix more.");
        } else if (r.data?.action === "sold_out") {
          setError("Sold out — all 500 spots have been claimed.");
        } else {
          setError("Couldn't reserve a spot — try again in a moment.");
        }
        setStage("error");
        await refresh();
        return;
      }
      setClaim(r.data);
      setStage("preview");
      await refresh();
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to start the fix.");
      setStage("error");
    } finally {
      setLoading(false);
    }
  }, [projectId, refresh]);

  const handleConfirm = useCallback(async () => {
    if (!claim?.claim_id) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.post("/founder-offer/confirm", {
        claim_id: claim.claim_id,
      });
      if (r.data?.success === false) {
        setError(r.data?.reason || "Couldn't confirm — try again.");
        setStage("error");
        return;
      }
      setStage("running");
      toast(
        "🛠️ Fix running — we'll commit the changes to your repo. " +
        "You'll see a notification when it's done.",
        "success",
      );
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to confirm.");
      setStage("error");
    } finally {
      setLoading(false);
    }
  }, [claim]);

  const handleCancel = useCallback(async () => {
    if (!claim?.claim_id) return;
    setLoading(true);
    try {
      await api.post("/founder-offer/cancel", { claim_id: claim.claim_id });
      setStage("idle");
      setClaim(null);
      await refresh();
    } catch (_e) {
      // best-effort
    } finally {
      setLoading(false);
    }
  }, [claim, refresh]);

  if (!visible) return null;

  // ── Render ───────────────────────────────────────────────────
  return (
    <div
      data-testid="founder-offer-card"
      style={{
        margin: "0 16px 8px",
        padding: "12px 14px",
        background: "linear-gradient(135deg, rgba(234,179,8,0.10) 0%, rgba(234,179,8,0.04) 100%)",
        border: "1px solid rgba(234,179,8,0.40)",
        borderRadius: 8,
        display: "flex", flexDirection: "column", gap: 10,
        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <div
            data-testid="founder-offer-headline"
            style={{ fontWeight: 600, fontSize: 14, color: "var(--text-strong, #0b0b0b)" }}
          >
            Free SEO fix — from the founder
          </div>
          <div
            data-testid="founder-offer-counter"
            style={{ fontSize: 11, color: counterColor, fontFamily: "'JetBrains Mono', monospace",
                     letterSpacing: "0.04em" }}
          >
            {status?.remaining ?? 0} spots remaining
          </div>
        </div>

        {stage === "idle" && (
          <button
            type="button"
            data-testid="founder-offer-fix-btn"
            disabled={loading}
            onClick={handleClaim}
            className="btn-primary"
            style={{ padding: "6px 14px", fontSize: 12 }}
          >
            {loading ? "Reserving…" : "Fix my site"}
          </button>
        )}
      </div>

      {stage === "preview" && claim?.preview && (
        <PreviewBlock
          preview={claim.preview}
          loading={loading}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
        />
      )}

      {stage === "running" && (
        <div
          data-testid="founder-offer-running"
          style={{ fontSize: 12, color: "var(--text-dim, #555)" }}
        >
          Running the fix in your repo… you can keep chatting; we&apos;ll
          ping you when the commit lands.
        </div>
      )}

      {stage === "error" && error && (
        <div
          data-testid="founder-offer-error"
          style={{ fontSize: 12, color: "#b91c1c" }}
        >
          {error}
        </div>
      )}
    </div>
  );
}


function PreviewBlock({ preview, loading, onConfirm, onCancel }) {
  const issues = preview?.issues_found ?? 0;
  const files = preview?.files_affected || [];
  return (
    <div
      data-testid="founder-offer-preview"
      style={{ display: "flex", flexDirection: "column", gap: 8 }}
    >
      <div style={{ fontSize: 12, color: "var(--text-dim, #555)" }}>
        Found <strong data-testid="founder-offer-issues-count">{issues}</strong>{" "}
        issue{issues === 1 ? "" : "s"} across{" "}
        <strong data-testid="founder-offer-files-count">{files.length}</strong>{" "}
        file{files.length === 1 ? "" : "s"}. Want me to commit these fixes?
      </div>
      {files.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11,
                     color: "var(--text-dim, #555)" }}>
          {files.slice(0, 6).map((f) => (
            <li key={f}>
              <code>{f}</code>
            </li>
          ))}
          {files.length > 6 && <li>… and {files.length - 6} more</li>}
        </ul>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          data-testid="founder-offer-confirm-btn"
          disabled={loading || issues === 0}
          onClick={onConfirm}
          className="btn-primary"
          style={{ padding: "6px 14px", fontSize: 12 }}
        >
          {loading ? "Working…" : (issues === 0 ? "Nothing to commit" : "Commit fixes")}
        </button>
        <button
          type="button"
          data-testid="founder-offer-cancel-btn"
          disabled={loading}
          onClick={onCancel}
          className="btn-ghost"
          style={{ padding: "6px 14px", fontSize: 12 }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
