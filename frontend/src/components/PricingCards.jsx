/**
 * PricingCards.jsx — 4-tier flat-fee pricing display (USD).
 *
 *   Free     $0          10 tasks/month
 *   Starter  $9/mo USD   50 tasks, Standard mode
 *   Pro      $19/mo USD  Unlimited tasks, Maxx mode (100/mo), Project Brain
 *   Team     $49/mo USD  Pro + admin + priority queue + unlimited Maxx
 *
 * Iter 94 (Feb 2026) — migrated CAD → USD per founder direction, Team
 * raised from $35 → $49 to protect margin against multi-seat teams.
 * Pro tier now advertises the 100 Maxx-tasks/mo cap explicitly so the
 * commercial expectation is set up-front.
 */
import React, { useState } from "react";
import { Check, Sparkles, ShieldCheck, Users } from "lucide-react";
import { api } from "../lib/api";

export const PRICING_TIERS = [
  {
    id: "free",
    name: "Free",
    price: "$0",
    period: "forever",
    icon: Sparkles,
    tagline: "Kick the tires",
    features: [
      "10 tasks per month",
      "Standard mode",
      "Direct commits to your repo",
      "F12 error debugger",
      "Community support",
    ],
    cta: "Current — get started",
    paid: false,
  },
  {
    id: "starter",
    name: "Starter",
    price: "$9",
    period: "/ month USD",
    icon: ShieldCheck,
    tagline: "For weekend builders",
    features: [
      "50 tasks per month",
      "Standard mode",
      "Direct commits + rollback",
      "Live worker tape",
      "Email support",
    ],
    cta: "Upgrade to Starter",
    paid: true,
  },
  {
    id: "pro",
    name: "Pro",
    price: "$19",
    period: "/ month USD",
    icon: Sparkles,
    tagline: "Most popular",
    highlight: true,
    features: [
      "Unlimited tasks",
      "Maxx mode — 100 Claude tasks/mo",
      "Project Brain memory",
      "Parallel agents",
      "VS Code extension",
      "Priority support",
    ],
    cta: "Upgrade to Pro",
    paid: true,
  },
  {
    id: "team",
    name: "Team",
    price: "$49",
    period: "/ month USD",
    icon: Users,
    tagline: "Ship as a squad",
    features: [
      "Everything in Pro",
      "Unlimited Maxx mode",
      "Admin dashboard + roles",
      "Priority queue",
      "Shared project brain",
      "Slack / DM support",
    ],
    cta: "Upgrade to Team",
    paid: true,
  },
];

export default function PricingCards({ currentTier = "free", compact = false }) {
  const [busy, setBusy] = useState(null);
  const [err, setErr]   = useState("");
  // Iter 101 — monthly/annual toggle. Default monthly. When annual,
  // we POST `plan=${tierId}_annual` to Stripe checkout which routes
  // to the new $86/$182/$470 annual price IDs.
  const [billing, setBilling] = useState("monthly");
  const isAnnual = billing === "annual";

  async function upgrade(tierId) {
    setErr("");
    setBusy(tierId);
    try {
      const planParam = isAnnual && tierId !== "free"
        ? `${tierId}_annual` : tierId;
      const r = await api.post("/payments/checkout", {
        plan: planParam,
        origin_url: window.location.origin,
      });
      const url = r.data?.checkout_url || r.data?.url;
      if (url) {
        window.location.href = url;
      } else {
        setErr("Could not start checkout — please retry.");
      }
    } catch (e) {
      setErr(
        e?.response?.data?.detail
        || e?.message
        || "Checkout failed — please retry.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function openPortal() {
    setErr("");
    try {
      const r = await api.post("/payments/portal");
      const url = r.data?.portal_url || r.data?.url;
      if (url) window.location.href = url;
    } catch (e) {
      setErr(
        e?.response?.data?.detail
        || e?.message
        || "Could not open billing portal.",
      );
    }
  }

  return (
    <div>
      {/* Iter 101 — Monthly/Annual billing toggle */}
      <div data-testid="billing-toggle" style={{
        display: "flex", justifyContent: "center", alignItems: "center",
        gap: 4, marginBottom: 20,
        padding: 4,
        background: "var(--panel, #0f1219)",
        border: "1px solid var(--border, rgba(255,200,120,0.16))",
        borderRadius: 999,
        width: "fit-content", margin: "0 auto 20px",
      }}>
        <button
          data-testid="billing-monthly"
          onClick={() => setBilling("monthly")}
          style={{
            padding: "8px 18px", fontSize: 12, fontWeight: 600,
            background: !isAnnual ? "var(--accent, #ff8a2a)" : "transparent",
            color: !isAnnual ? "var(--bg, #0a0c10)" : "var(--text-dim)",
            border: "none", borderRadius: 999, cursor: "pointer",
            transition: "all 0.15s",
          }}
        >Monthly</button>
        <button
          data-testid="billing-annual"
          onClick={() => setBilling("annual")}
          style={{
            padding: "8px 18px", fontSize: 12, fontWeight: 600,
            background: isAnnual ? "var(--accent, #ff8a2a)" : "transparent",
            color: isAnnual ? "var(--bg, #0a0c10)" : "var(--text-dim)",
            border: "none", borderRadius: 999, cursor: "pointer",
            display: "flex", alignItems: "center", gap: 6,
          }}
        >
          Annual
          <span data-testid="annual-save-badge" style={{
            fontSize: 9, fontWeight: 700,
            padding: "2px 6px", borderRadius: 3,
            background: isAnnual ? "rgba(0,0,0,0.18)" : "rgba(109,212,161,0.18)",
            color: isAnnual ? "var(--bg, #0a0c10)" : "#6dd4a1",
          }}>SAVE 20%</span>
        </button>
      </div>

      <div data-testid="pricing-cards" style={{
        display: "grid",
        gap: compact ? 12 : 16,
        gridTemplateColumns:
          "repeat(auto-fit, minmax(220px, 1fr))",
      }}>
      {PRICING_TIERS.map((t) => {
        const isCurrent = (t.id === currentTier)
          || (currentTier === "founder" && t.id === "pro");
        const Icon = t.icon;
        return (
          <div
            key={t.id}
            data-testid={`pricing-card-${t.id}`}
            data-current={isCurrent ? "true" : "false"}
            style={{
              position: "relative",
              padding: compact ? "16px 16px" : "20px 18px",
              background: t.highlight
                ? "linear-gradient(180deg, rgba(255,138,42,0.06) 0%, var(--panel, #0f1219) 80%)"
                : "var(--panel, #0f1219)",
              border: t.highlight
                ? "1px solid rgba(255,138,42,0.45)"
                : "1px solid var(--border, rgba(255,200,120,0.16))",
              borderRadius: 10,
              boxShadow: t.highlight
                ? "0 16px 36px -18px rgba(255,138,42,0.35)"
                : "none",
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            {t.highlight && (
              <div style={{
                position: "absolute", top: -10, left: 16,
                background: "var(--accent, #ff8a2a)",
                color: "var(--bg, #0a0c10)",
                fontSize: 9, fontWeight: 700, letterSpacing: ".08em",
                textTransform: "uppercase",
                padding: "3px 8px", borderRadius: 4,
              }}>Most popular</div>
            )}
            {isCurrent && (
              <div data-testid={`pricing-current-${t.id}`} style={{
                position: "absolute", top: 12, right: 12,
                background: "rgba(109,212,161,0.16)",
                color: "var(--ok, #6dd4a1)",
                fontSize: 9, fontWeight: 700, letterSpacing: ".08em",
                textTransform: "uppercase",
                padding: "3px 8px", borderRadius: 4,
              }}>Current</div>
            )}

            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              color: t.highlight
                ? "var(--accent, #ff8a2a)"
                : "var(--text-dim, #a39d8a)",
            }}>
              <Icon size={14} />
              <span style={{
                fontSize: 11, fontWeight: 600, letterSpacing: ".08em",
                textTransform: "uppercase",
              }}>{t.name}</span>
            </div>

            <div>
              <div style={{
                fontSize: 30, fontWeight: 500, color: "var(--text)",
                letterSpacing: "-0.02em", lineHeight: 1,
              }}>
                {(() => {
                  // Iter 101 — annual mode shows /yr price with strike-through
                  // on the would-be 12× monthly for visual anchor.
                  if (!t.paid || !isAnnual) return t.price;
                  const annualMap = { starter: "$86", pro: "$182", team: "$470" };
                  return annualMap[t.id] || t.price;
                })()}
                <span style={{
                  fontSize: 12, color: "var(--text-faint)",
                  fontWeight: 400, marginLeft: 4,
                }}>{isAnnual && t.paid ? "/ year USD" : t.period}</span>
              </div>
              {isAnnual && t.paid && (
                <div data-testid={`annual-save-${t.id}`} style={{
                  fontSize: 10, color: "#6dd4a1", marginTop: 2,
                  fontWeight: 600, letterSpacing: ".04em",
                }}>
                  Save ${ {starter: 22, pro: 46, team: 118}[t.id] || 0 } vs monthly
                </div>
              )}
              <div style={{
                fontSize: 11, color: "var(--text-faint)", marginTop: 4,
              }}>{t.tagline}</div>
            </div>

            <ul style={{
              listStyle: "none", margin: 0, padding: 0,
              display: "flex", flexDirection: "column", gap: 6,
            }}>
              {t.features.map((f) => (
                <li key={f} style={{
                  display: "flex", gap: 7, alignItems: "flex-start",
                  fontSize: 12, color: "var(--text-dim)",
                  lineHeight: 1.45,
                }}>
                  <Check size={12} style={{
                    color: t.highlight
                      ? "var(--accent, #ff8a2a)"
                      : "var(--ok, #6dd4a1)",
                    flexShrink: 0, marginTop: 2,
                  }}/>
                  <span>{f}</span>
                </li>
              ))}
            </ul>

            <button
              data-testid={`pricing-cta-${t.id}`}
              disabled={isCurrent || !t.paid || busy === t.id}
              onClick={() => {
                if (isCurrent && t.paid) openPortal();
                else if (t.paid) upgrade(t.id);
              }}
              style={{
                marginTop: "auto",
                padding: "10px 14px",
                fontSize: 12, fontWeight: 600,
                letterSpacing: ".04em",
                borderRadius: 5,
                border: t.highlight ? "none" : "1px solid var(--border, rgba(255,200,120,0.32))",
                background: isCurrent
                  ? "transparent"
                  : t.highlight
                    ? "var(--accent, #ff8a2a)"
                    : "var(--bg-elev, #0a0c10)",
                color: isCurrent
                  ? "var(--text-faint)"
                  : t.highlight
                    ? "var(--bg, #0a0c10)"
                    : "var(--text)",
                cursor: (isCurrent && !t.paid) || busy === t.id ? "default" : "pointer",
                opacity: ((isCurrent && !t.paid) || busy === t.id) ? 0.65 : 1,
              }}
            >
              {busy === t.id ? "Opening checkout…"
                : isCurrent && t.paid ? "Manage billing"
                : isCurrent ? "Current plan"
                : t.cta}
            </button>
          </div>
        );
      })}
      {err && (
        <div data-testid="pricing-error" style={{
          gridColumn: "1 / -1",
          fontSize: 12, color: "var(--danger, #ff6b6b)",
          background: "rgba(255,107,107,0.06)",
          border: "1px solid rgba(255,107,107,0.2)",
          padding: "8px 12px", borderRadius: 5,
        }}>{err}</div>
      )}
      </div>
    </div>
  );
}
