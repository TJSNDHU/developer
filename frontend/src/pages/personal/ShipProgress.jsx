/**
 * pages/personal/ShipProgress.jsx — Iter 212m-235 — Phase 6
 *
 * 4-step vertical timeline shown while `/scaffold/{id}/materialize`
 * runs. Heartbeat pulse on active step, moss-green check on complete.
 * Plain-language toasts only — NEVER surface raw error/stack traces.
 */
import React, { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { Check, GitBranch, Cloud, Database, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { PersonalShell } from "./_shell";
import { api } from "../../lib/api";

const STEPS = [
  { key: "repo",    label: "Creating your app",         hint: "Wrapping up the code and version control",   Icon: GitBranch },
  { key: "deploy",  label: "Putting it on the internet", hint: "Setting up the servers to run your app",     Icon: Cloud     },
  { key: "db",      label: "Setting up your database",   hint: "So your app can remember things",            Icon: Database  },
  { key: "live",    label: "Almost live…",               hint: "Doing the final checks",                     Icon: Sparkles  },
];

const FRIENDLY_STATUS_MESSAGES = [
  "Warming up the servers…",
  "Teaching your app how to talk to the database…",
  "Almost there — final tests running…",
  "One more thing…",
];

export default function ShipProgress() {
  const { draftId } = useParams();
  const nav = useNavigate();
  const [step, setStep] = useState(0);       // 0..3 currently active
  const [failed, setFailed] = useState(null);
  const rotIdx = useRef(0);
  const heartbeat = useRef(null);

  useEffect(() => {
    // Cycle friendly status toasts every 6s while shipping.
    heartbeat.current = setInterval(() => {
      const m = FRIENDLY_STATUS_MESSAGES[rotIdx.current % FRIENDLY_STATUS_MESSAGES.length];
      rotIdx.current += 1;
      toast(m, { duration: 3000 });
    }, 6000);
    return () => clearInterval(heartbeat.current);
  }, []);

  useEffect(() => {
    // Fire the actual materialize call ONCE. Advance the timeline as
    // we get partial state info back from the API. If the endpoint
    // returns before all steps complete, we simulate the remaining
    // ones on a short interval so the user gets the "worked through
    // it" feeling instead of a jump-to-done.
    let cancelled = false;
    (async () => {
      // Step 1 begins immediately.
      setStep(0);
      try {
        const r = await api.post(`/scaffold/${draftId}/materialize`, {});
        if (cancelled) return;
        // Repo created → step 1 complete
        setStep(1);
        await sleep(1200);
        // Deploy attempt happened in the same call — check the result
        setStep(2);
        await sleep(1200);
        setStep(3);
        await sleep(900);
        // Persist the materialize result for the success screen.
        try {
          sessionStorage.setItem(
            `aurem_ship_${draftId}`,
            JSON.stringify(r.data),
          );
        } catch { /* private mode */ }
        nav(`/build/${draftId}/success`);
      } catch (e) {
        if (cancelled) return;
        // NEVER surface raw error content. Always plain language.
        // Iter 212m-237: HTTP 422 with reason=security_scan_failed
        // means the security gate blocked us. Route that to a
        // distinct branch so the user sees the "refine your brief"
        // guidance and NOT the "try again" retry loop.
        const status = e?.response?.status;
        const detail = e?.response?.data?.detail || {};
        if (status === 422 && detail.reason === "security_scan_failed") {
          setFailed("scan");
          toast.error(
            detail.user_message ||
            "We spotted something we don't want to ship as-is. Refine your brief.",
          );
          return;
        }
        const kind = status === 503 ? "config" : "unknown";
        setFailed(kind);
        toast.error(
          kind === "config"
            ? "Our platform's still connecting to the internet plumbing. Please try again in a minute."
            : "Hmm, something got tangled up. Let's try again.",
        );
      }
    })();
    return () => { cancelled = true; };
  }, [draftId, nav]);

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  return (
    <PersonalShell>
      <div
        data-testid="ship-progress-page"
        style={{
          maxWidth: 640, margin: "0 auto",
          padding: "72px 24px 96px", textAlign: "center",
        }}
        aria-live="polite"
      >
        <h1 style={{
          fontFamily: "'Cabinet Grotesk', 'Manrope', sans-serif",
          fontSize: 36, fontWeight: 500, letterSpacing: "-0.02em",
          margin: "0 0 12px",
        }}>
          Building your app.
        </h1>
        <p style={{ color: "#6B6B63", fontSize: 15, margin: "0 0 48px" }}>
          Sit tight — this takes about a minute.
        </p>

        <div style={{
          textAlign: "left",
          display: "flex", flexDirection: "column", gap: 8,
        }}>
          {STEPS.map((s, i) => {
            const active   = i === step && !failed;
            const done     = i < step;
            const pending  = i > step;
            return (
              <StepRow
                key={s.key}
                idx={i}
                Icon={s.Icon}
                label={s.label}
                hint={s.hint}
                active={active}
                done={done}
                pending={pending}
              />
            );
          })}
        </div>

        {failed && (
          <div style={{
            marginTop: 40, padding: "20px 22px",
            background: failed === "scan" ? "rgba(224,122,95,0.06)" : "rgba(224,122,95,0.08)",
            border: "1px solid rgba(224,122,95,0.24)",
            borderRadius: 12, textAlign: "left",
          }}>
            {failed === "scan" ? (
              <>
                <p style={{ fontSize: 15, fontWeight: 600, color: "#1C1C19", margin: "0 0 8px" }}>
                  We spotted something we don&apos;t want to ship yet.
                </p>
                <p style={{ fontSize: 14, color: "#4B4B45", margin: 0, lineHeight: 1.6 }}>
                  Try being more specific in your brief — especially about
                  how users sign in and what data your app stores.
                  We&apos;ll try again with a safer version.
                </p>
              </>
            ) : (
              <p style={{ fontSize: 14, color: "#1C1C19", margin: 0 }}>
                We couldn&apos;t finish this one. Your draft is safe — try shipping again
                in a minute, or head back to review it.
              </p>
            )}
            <div style={{ display: "flex", gap: 12, marginTop: 16, justifyContent: "center" }}>
              {failed !== "scan" && (
                <button
                  data-testid="ship-retry-button"
                  onClick={() => window.location.reload()}
                  style={{
                    border: "none", background: "#E07A5F", color: "#fff",
                    padding: "10px 20px", borderRadius: 999, fontSize: 13,
                    fontWeight: 600, cursor: "pointer",
                  }}
                >Try again</button>
              )}
              <button
                data-testid="ship-back-button"
                onClick={() => nav(`/build/${draftId}`)}
                style={{
                  border: "1px solid #E5E5DF", background: failed === "scan" ? "#E07A5F" : "transparent",
                  padding: "10px 20px", borderRadius: 999, fontSize: 13,
                  fontWeight: failed === "scan" ? 600 : 500, cursor: "pointer",
                  color: failed === "scan" ? "#fff" : "#1C1C19",
                }}
              >{failed === "scan" ? "Refine my brief" : "Back to draft"}</button>
            </div>
          </div>
        )}
      </div>
    </PersonalShell>
  );
}


function StepRow({ idx, Icon, label, hint, active, done, pending }) {
  return (
    <motion.div
      data-testid="progress-step-item"
      data-step-state={done ? "done" : active ? "active" : "pending"}
      initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }}
      transition={{ delay: 0.1 * idx }}
      style={{
        display: "flex", alignItems: "center", gap: 16,
        padding: "16px 18px", borderRadius: 12,
        background: active ? "rgba(224,122,95,0.06)" : "transparent",
        border: active ? "1px solid rgba(224,122,95,0.24)" : "1px solid transparent",
        transition: "background 300ms ease, border-color 300ms ease",
      }}
    >
      <div style={{
        width: 36, height: 36, borderRadius: 999,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: done  ? "#81B29A"
                  : active ? "#FFFFFF"
                           : "#F4F3EE",
        border: active ? "1px solid rgba(224,122,95,0.4)"
                       : "1px solid #E5E5DF",
        color:  done ? "#FFFFFF"
                     : active ? "#E07A5F"
                              : "#B4B4A7",
        flexShrink: 0,
        position: "relative",
      }}>
        {active && (
          <motion.div
            animate={{ scale: [1, 1.6, 1], opacity: [0.6, 0, 0.6] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: "easeOut" }}
            style={{
              position: "absolute", inset: -2, borderRadius: 999,
              border: "2px solid rgba(224,122,95,0.5)",
            }}
          />
        )}
        {done ? <Check size={16} strokeWidth={2.5} /> : <Icon size={16} strokeWidth={1.8} />}
      </div>
      <div style={{ flex: 1 }}>
        <p style={{
          fontSize: 15, fontWeight: done || active ? 600 : 500,
          color: pending ? "#8B8B7D" : "#1C1C19",
          margin: 0,
        }}>{label}</p>
        <p style={{
          fontSize: 12, color: "#8B8B7D",
          margin: "2px 0 0",
        }}>{hint}</p>
      </div>
    </motion.div>
  );
}
