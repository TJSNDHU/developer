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
  const [userStatus, setUserStatus] = useState(null);  // { has_fully_claimed, days_since_signup, repos_claimed, claimed_repo_ids }
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("idle");          // idle | preview | running | done | error
  const [claim, setClaim] = useState(null);            // { claim_id, preview }
  const [error, setError] = useState(null);
  // Iter 212m-44 — instant per-project dismissal. Once the user
  // claims/confirms the offer on this project, push the projectId
  // here so the banner hides immediately (without waiting for the
  // next 30 s poll to refresh `claimed_repo_ids` from the server).
  const [locallyClaimedRepos, setLocallyClaimedRepos] = useState(() => {
    try {
      const raw = localStorage.getItem("aurem_founder_offer_claimed_repos");
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  });
  const markRepoClaimed = useCallback((repoId) => {
    if (!repoId) return;
    setLocallyClaimedRepos((prev) => {
      if (prev.includes(repoId)) return prev;
      const next = [...prev, repoId];
      try {
        localStorage.setItem("aurem_founder_offer_claimed_repos", JSON.stringify(next));
      } catch { /* ignore */ }
      return next;
    });
  }, []);

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
    // Iter 212m-44 — per-project dismissal. Hide the banner the
    // moment the user has claimed the offer for THIS project. Reads
    // from both the server-side `claimed_repo_ids` (authoritative)
    // and a local cache (so the banner disappears instantly without
    // waiting for the next 30 s poll).
    const serverClaimed = Array.isArray(userStatus.claimed_repo_ids)
      ? userStatus.claimed_repo_ids
      : [];
    if (serverClaimed.includes(projectId)) return false;
    if (locallyClaimedRepos.includes(projectId)) return false;
    return true;
  }, [projectId, status, userStatus, locallyClaimedRepos]);

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
      setStage("celebrate");
      // Iter 212m-44 — true claim landed (commit fired). Persist
      // the projectId locally so the banner stays hidden on this
      // project window across reloads, even before the next server
      // poll updates `claimed_repo_ids`.
      markRepoClaimed(projectId);
      toast(
        "🎉 Free SEO fix queued — we'll commit the changes to your repo shortly.",
        "success",
      );
      // Iter 212m-45 — auto-dismiss the celebration pill after 5 s so
      // it doesn't linger; once `stage` is back to "idle" the `visible`
      // check (claimed_repo_ids includes projectId) hides the banner
      // entirely from this project window.
      setTimeout(() => setStage("idle"), 5000);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to confirm.");
      setStage("error");
    } finally {
      setLoading(false);
    }
  }, [claim, projectId, markRepoClaimed]);

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

  // Iter 212m-44 — `visible` hides the banner once the offer has
  // been claimed on this project. But while the user is actively
  // mid-flow (preview / running / error) we must keep the UI
  // mounted so they can confirm or read the error — `isInteracting`
  // is the escape hatch.
  const isInteracting = stage !== "idle";
  if (!visible && !isInteracting) return null;

  // ── Render ───────────────────────────────────────────────────
  // Banner attached to the TOP of the chat composer. Rounded top
  // corners flow visually into the composer's flat top edge below.
  // Brighter, fully readable copy (no dim/muddy text).
  return (
    <div
      data-testid="founder-offer-card"
      style={{
        // Iter 212m-196 — Founder request: banner width should hug
        // the chat window's inner column, not span the full chat pane.
        // Match the LoopStepBar's `margin: 8px clamp(16px, 17.25%, 240px)`
        // and the composer's inner `padding: 14px clamp(16px, 17.25%, 240px)`
        // so banner ↔ LOOP bar ↔ composer content all sit on the
        // same left/right rail. Previous `margin: 0` (edge-to-edge)
        // made the banner look ~2x wider than the actual chat input.
        margin: "0 clamp(16px, 17.25%, 240px)",
        padding: "10px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        background: "linear-gradient(180deg, rgba(234,179,8,0.10) 0%, rgba(234,179,8,0.04) 100%)",
        borderTopLeftRadius: 12,
        borderTopRightRadius: 12,
        borderBottomLeftRadius: 0,
        borderBottomRightRadius: 0,
        // Side + top border only — bottom edge fuses with composer.
        borderTop: "1px solid rgba(234,179,8,0.45)",
        borderLeft: "1px solid rgba(234,179,8,0.45)",
        borderRight: "1px solid rgba(234,179,8,0.45)",
        borderBottom: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          <span aria-hidden="true" style={{ fontSize: 16 }}>🎁</span>
          <span
            data-testid="founder-offer-headline"
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "#fde68a",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            Free SEO fix from the founder
          </span>
          <span
            data-testid="founder-offer-counter"
            style={{
              fontSize: 12,
              color: counterColor,
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
              fontWeight: 600,
              whiteSpace: "nowrap",
            }}
          >
            · {status?.remaining ?? 0} spots remaining
          </span>
        </div>

        {stage === "idle" && (
          <button
            type="button"
            data-testid="founder-offer-fix-btn"
            disabled={loading}
            onClick={handleClaim}
            style={{
              padding: "6px 14px",
              fontSize: 12,
              fontWeight: 700,
              color: "#0b0b0b",
              background: "#facc15",
              border: "none",
              borderRadius: 6,
              cursor: loading ? "wait" : "pointer",
              fontFamily: "inherit",
              whiteSpace: "nowrap",
              transition: "background 120ms ease, transform 120ms ease",
              boxShadow: "0 1px 2px rgba(0,0,0,0.25)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "#fde047";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "#facc15";
            }}
          >
            {loading ? "Reserving…" : "Fix my site →"}
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

      {stage === "celebrate" && (
        <div
          data-testid="founder-offer-celebrate"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontSize: 13,
            fontWeight: 600,
            color: "#86efac",
            padding: "2px 0",
            animation: "auremFounderCelebrate 600ms cubic-bezier(0.2, 0.9, 0.3, 1.4)",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              fontSize: 16,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 22, height: 22,
              borderRadius: "50%",
              background: "rgba(34,197,94,0.18)",
              border: "1px solid rgba(34,197,94,0.55)",
              color: "#22c55e",
              fontWeight: 800,
            }}
          >
            ✓
          </span>
          <span>
            <span style={{ color: "#fde68a" }}>🎉 Congratulations!</span>{" "}
            <span style={{ color: "#86efac" }}>
              Your free SEO fix is queued —
            </span>{" "}
            <span style={{ color: "#fde68a", opacity: 0.9 }}>
              we&apos;ll commit the changes to your repo shortly.
            </span>
          </span>
          <style>{`
            @keyframes auremFounderCelebrate {
              0%   { transform: translateY(-4px) scale(0.96); opacity: 0; }
              60%  { transform: translateY(0)    scale(1.02); opacity: 1; }
              100% { transform: translateY(0)    scale(1);     opacity: 1; }
            }
          `}</style>
        </div>
      )}

      {stage === "error" && error && (
        <div
          data-testid="founder-offer-error"
          style={{ fontSize: 12, color: "#fca5a5" }}
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
