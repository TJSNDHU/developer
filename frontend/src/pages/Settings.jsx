/**
 * Settings.jsx — Iter 212m-180: standalone settings WINDOW.
 *
 * The old page rendered inside the legacy <Shell> (old sidebar chrome).
 * Founder wants the avatar-menu entries to open a clean popup-style
 * window: no sidebar, v2 (ds2) design language, tabbed sections, and a
 * back button that returns to wherever the user came from.
 *
 * Entry points: sidebar avatar dropdown (Edit Profile / Settings),
 * Stripe redirect (/settings?session_id=cs_xxx → Plans tab), old
 * #pricing anchors, direct URL.
 */
import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  ArrowLeft, User, KeyRound, Receipt, Plug, ShieldCheck, Coins,
} from "lucide-react";
import { api, getUser } from "../lib/api";
import { trackPurchase } from "../lib/analytics";
import GitHubCard from "../components/GitHubCard";
import VercelCard from "../components/VercelCard";
import PricingCards from "../components/PricingCards";
import OraWrapped from "../components/OraWrapped";
import ReferralShare from "../components/ReferralShare";
import TrustLevelCard from "../components/TrustLevelCard";

const TABS = [
  { id: "profile",      label: "Profile",       icon: User },
  { id: "plans",        label: "Plans & Usage", icon: Receipt },
  { id: "integrations", label: "Integrations",  icon: Plug },
  { id: "vault",        label: "Vault",         icon: KeyRound },
];

export default function Settings() {
  const location = useLocation();
  const navigate = useNavigate();
  const [me, setMe]         = useState(getUser());
  const [usage, setUsage]   = useState(null);
  const [audit, setAudit]   = useState([]);
  const [billingMsg, setBillingMsg] = useState("");

  const params = new URLSearchParams(location.search);
  const hasStripeSession = !!params.get("session_id");
  const initialTab =
    hasStripeSession || location.hash === "#pricing"
      ? "plans"
      : (TABS.some((t) => t.id === params.get("tab"))
          ? params.get("tab") : "profile");
  const [tab, setTab] = useState(initialTab);

  useEffect(() => {
    if (!getUser()) { navigate("/login", { replace: true }); return; }
    api.get("/auth/me").then((r) => r.data?.user && setMe(r.data.user)).catch(() => {});
    api.get("/usage/me").then((r) => setUsage(r.data)).catch(() => {});
    api.get("/vault/audit-log").then((r) => setAudit(r.data?.entries || r.data?.log || [])).catch(() => {});
    // eslint-disable-next-line
  }, []);

  // Stripe redirects to /settings?session_id=cs_xxx — poll until the
  // tier flip lands, then refresh the user.
  useEffect(() => {
    const sid = params.get("session_id");
    if (!sid) return;
    let cancelled = false;
    setBillingMsg("Confirming payment…");
    (async () => {
      for (let i = 0; i < 12 && !cancelled; i++) {
        try {
          const r = await api.get(`/payments/status/${sid}`);
          if (r.data?.payment_status === "paid") {
            setBillingMsg(`Upgraded to ${r.data.tier?.toUpperCase() || "PAID"} — enjoy.`);
            const tier = (r.data.tier || "").toLowerCase();
            const PLAN_VALUE_USD = { starter: 9, pro: 19, team: 49 };
            if (tier && PLAN_VALUE_USD[tier]) {
              trackPurchase(PLAN_VALUE_USD[tier], "USD", sid);
            } else {
              trackPurchase();
            }
            const me2 = await api.get("/auth/me");
            if (me2.data?.user) setMe(me2.data.user);
            return;
          }
        } catch { /* keep trying */ }
        await new Promise((r) => setTimeout(r, 2000));
      }
      if (!cancelled) setBillingMsg("Payment is still processing. Refresh in a minute.");
    })();
    // Strip session_id from the URL so a reload doesn't re-poll.
    navigate("/settings?tab=plans", { replace: true });
    return () => { cancelled = true; };
    // eslint-disable-next-line
  }, []);

  // Back → origin. Direct entries (Stripe redirect / fresh tab) have no
  // in-app history, so fall back to the dashboard.
  const goBack = () => {
    if (location.key && location.key !== "default") navigate(-1);
    else navigate("/dashboard");
  };

  const switchTab = (id) => {
    setTab(id);
    navigate(`/settings?tab=${id}`, { replace: true });
  };

  const initial = (me?.name || me?.email || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="ds2-root" data-theme="dark" data-testid="settings-window"
      style={{ minHeight: "100vh", background: "var(--ds2-bg, #0A0A0A)" }}>
      <div style={{
        position: "fixed", inset: 0, pointerEvents: "none",
        background: "radial-gradient(900px 400px at 80% -10%, rgba(255,102,8,0.07), transparent 60%)",
      }} />

      <div style={{
        maxWidth: 880, margin: "0 auto", padding: "28px 20px 60px",
        position: "relative", animation: "ds2SettingsIn .28s ease both",
      }}>
        {/* ── Window header ── */}
        <div style={{
          display: "flex", alignItems: "center", gap: 14, marginBottom: 22,
        }}>
          <button
            data-testid="settings-back-btn"
            onClick={goBack}
            style={{
              display: "inline-flex", alignItems: "center", gap: 7,
              padding: "8px 14px", borderRadius: 999,
              background: "var(--panel)", border: "1px solid var(--border)",
              color: "var(--text-dim)", fontSize: 12.5, cursor: "pointer",
              fontFamily: "inherit",
              transition: "border-color .15s ease, color .15s ease",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "var(--text)"; e.currentTarget.style.borderColor = "var(--accent)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-dim)"; e.currentTarget.style.borderColor = "var(--border)"; }}
          >
            <ArrowLeft size={14} /> Back
          </button>

          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0, fontSize: 20, color: "var(--text)", letterSpacing: "-0.01em" }}>
              Settings
            </h1>
            <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--text-faint)" }}>
              Your account, plans, integrations &amp; security.
            </p>
          </div>

          <div data-testid="settings-user-chip" style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "6px 12px 6px 6px", borderRadius: 999,
            background: "var(--panel)", border: "1px solid var(--border)",
          }}>
            <span style={{
              width: 28, height: 28, borderRadius: "50%",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              background: "var(--accent-soft)", color: "var(--accent)",
              fontSize: 13, fontWeight: 700,
            }}>{initial}</span>
            <span style={{ fontSize: 12, color: "var(--text-dim)", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {me?.email || "—"}
            </span>
          </div>
        </div>

        {/* ── Tabs ── */}
        <div role="tablist" style={{
          display: "flex", gap: 6, marginBottom: 20, flexWrap: "wrap",
          padding: 5, borderRadius: 12,
          background: "var(--panel)", border: "1px solid var(--border)",
          width: "fit-content",
        }}>
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              data-testid={`settings-tab-${id}`}
              onClick={() => switchTab(id)}
              style={{
                display: "inline-flex", alignItems: "center", gap: 7,
                padding: "8px 15px", borderRadius: 8, cursor: "pointer",
                fontSize: 12.5, fontFamily: "inherit", border: "none",
                background: tab === id ? "var(--accent-soft)" : "transparent",
                color: tab === id ? "var(--accent)" : "var(--text-dim)",
                fontWeight: tab === id ? 600 : 400,
                transition: "background .15s ease, color .15s ease",
              }}
            >
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>

        {billingMsg && (
          <div data-testid="billing-banner" style={{
            padding: "10px 14px", marginBottom: 16, borderRadius: 8,
            background: "rgba(255,138,42,0.08)",
            border: "1px solid rgba(255,138,42,0.32)",
            color: "var(--accent-2, #ffb347)", fontSize: 12,
          }}>{billingMsg}</div>
        )}

        {/* ── Tab content ── */}
        {tab === "profile" && (
          <div key="profile" style={{ display: "grid", gap: 18, animation: "ds2SettingsIn .22s ease both" }}>
            {/* Iter 212m-221 — Universal Key + quota card. Prominent
                at top of Profile tab so users don't need the Plans
                tab to see their key balance / tier / remaining quota. */}
            <section className="card" data-testid="settings-universal-key" style={{
              display: "grid", gap: 14,
              padding: "20px 24px",
              background: "linear-gradient(135deg, rgba(255,138,42,0.06), rgba(255,138,42,0.02))",
              border: "1px solid rgba(255,138,42,0.24)",
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span style={{
                    width: 40, height: 40, borderRadius: 10,
                    display: "inline-flex", alignItems: "center", justifyContent: "center",
                    background: "var(--accent-soft, rgba(255,138,42,0.16))", color: "var(--accent, #ffb347)",
                  }}><KeyRound size={18} /></span>
                  <div>
                    <div className="label-mini" style={{ marginBottom: 2 }}>Universal LLM Key</div>
                    <div style={{ fontSize: 15, color: "var(--text)", fontWeight: 600 }}>
                      Emergent — one key, all models
                    </div>
                  </div>
                </div>
                <div
                  data-testid="tier-badge"
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11, textTransform: "uppercase",
                    letterSpacing: "1.5px", padding: "6px 12px",
                    borderRadius: 999,
                    background: (me?.tier || usage?.tier || "free").toLowerCase() === "founder"
                      ? "rgba(109,212,161,0.14)"
                      : "rgba(255,138,42,0.14)",
                    color: (me?.tier || usage?.tier || "free").toLowerCase() === "founder"
                      ? "var(--ok, #6dd4a1)" : "var(--accent, #ffb347)",
                    border: "1px solid currentColor",
                    fontWeight: 700,
                  }}
                >
                  {(me?.tier || usage?.tier || "free").toUpperCase()}
                </div>
              </div>

              {/* Balance grid */}
              <div style={{
                display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                gap: 12, marginTop: 4,
              }}>
                <div data-testid="key-balance-tokens" style={{
                  padding: "12px 14px", borderRadius: 8,
                  background: "rgba(0,0,0,0.24)", border: "1px solid var(--border-strong, rgba(255,255,255,0.08))",
                }}>
                  <div className="label-mini" style={{ marginBottom: 6 }}>tokens remaining</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text)", fontFamily: "'JetBrains Mono', monospace" }}>
                    {usage?.is_unlimited || me?.is_unlimited
                      ? "∞"
                      : (usage?.remaining != null
                          ? Number(usage.remaining).toLocaleString()
                          : "—")}
                  </div>
                </div>
                <div data-testid="key-balance-used" style={{
                  padding: "12px 14px", borderRadius: 8,
                  background: "rgba(0,0,0,0.24)", border: "1px solid var(--border-strong, rgba(255,255,255,0.08))",
                }}>
                  <div className="label-mini" style={{ marginBottom: 6 }}>tokens used</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text)", fontFamily: "'JetBrains Mono', monospace" }}>
                    {usage?.used != null
                      ? Number(usage.used).toLocaleString()
                      : "—"}
                  </div>
                </div>
                <div data-testid="key-tasks-this-month" style={{
                  padding: "12px 14px", borderRadius: 8,
                  background: "rgba(0,0,0,0.24)", border: "1px solid var(--border-strong, rgba(255,255,255,0.08))",
                }}>
                  <div className="label-mini" style={{ marginBottom: 6 }}>tasks this month</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text)", fontFamily: "'JetBrains Mono', monospace" }}>
                    {usage?.tasks_this_month ?? 0}
                    {usage?.monthly_task_cap != null && (
                      <span style={{ color: "var(--text-faint)", fontSize: 14, fontWeight: 400 }}>
                        {" / "}{usage.monthly_task_cap}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Quota bar for non-unlimited users */}
              {!(usage?.is_unlimited || me?.is_unlimited) && usage?.pct_used != null && (
                <div data-testid="quota-bar" style={{ marginTop: 4 }}>
                  <div style={{
                    height: 6, borderRadius: 999,
                    background: "rgba(255,255,255,0.06)",
                    overflow: "hidden",
                  }}>
                    <div style={{
                      height: "100%",
                      width: `${Math.min(100, Math.max(0, usage.pct_used))}%`,
                      background: usage.pct_used > 85
                        ? "var(--danger, #ff6b6b)"
                        : usage.pct_used > 60
                          ? "var(--accent-2, #ffb347)"
                          : "var(--accent, #ffb347)",
                      transition: "width 0.4s ease",
                    }} />
                  </div>
                  <div style={{
                    fontSize: 11, color: "var(--text-faint)",
                    marginTop: 6, fontFamily: "'JetBrains Mono', monospace",
                  }}>
                    {usage.pct_used}% used
                    {usage.is_exhausted && (
                      <span style={{ color: "var(--danger)", marginLeft: 8 }}>
                        · quota exhausted — <span
                          onClick={() => setTab("plans")}
                          style={{ textDecoration: "underline", cursor: "pointer" }}
                          data-testid="quota-upgrade-link"
                        >upgrade →</span>
                      </span>
                    )}
                  </div>
                </div>
              )}
            </section>

            <section className="card" data-testid="settings-profile">
              <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
                <User size={14} /> Profile
              </h3>
              <Row k="email" v={me?.email || "—"} />
              <Row k="name" v={me?.name || "—"} />
              <Row k="user id" v={me?.user_id || "—"} />
              <Row k="tier" v={me?.tier || usage?.tier || "free"} />
              <Row k="track" v={me?.track || "developer"} />
              {usage && usage.monthly_task_cap != null && (
                <Row k="tasks this month"
                     v={`${usage.tasks_this_month} / ${usage.monthly_task_cap}`} />
              )}
              {usage && usage.monthly_task_cap == null && (
                <Row k="tasks this month" v={`${usage.tasks_this_month} / unlimited`} />
              )}
            </section>

            {/* Iter 212m-235 — Switch between Personal and Developer Track.  */}
            <TrackSwitcher currentTrack={me?.track || "developer"} onSwitched={(t) => setMe((m) => m ? { ...m, track: t } : m)} />

            <TrustLevelCard />
            <ReferralShare />
          </div>
        )}

        {tab === "plans" && (
          <div key="plans" style={{ display: "grid", gap: 18, animation: "ds2SettingsIn .22s ease both" }}>
            <section className="card" data-testid="settings-wallet" style={{
              display: "flex", alignItems: "center", gap: 14,
              padding: "16px 24px",
            }}>
              <span style={{
                width: 34, height: 34, borderRadius: 9,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                background: "var(--accent-soft)", color: "var(--accent)",
              }}><Coins size={16} /></span>
              <div>
                <div className="label-mini" style={{ marginBottom: 2 }}>token wallet</div>
                <div style={{ fontSize: 18, color: "var(--text)", fontWeight: 600 }}>
                  {me?.is_unlimited ? "∞ Unlimited" : (me?.tokens_remaining ?? "—")}
                </div>
              </div>
            </section>

            <section className="card" data-testid="settings-pricing">
              <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                <Receipt size={14} /> Plans
              </h3>
              <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 16px" }}>
                Flat fee. No token surprises. Cancel any time.
              </p>
              <PricingCards currentTier={me?.tier || usage?.tier || "free"} />
            </section>

            <section className="card" data-testid="settings-wrapped">
              <OraWrapped defaultPeriod="this_month" />
            </section>
          </div>
        )}

        {tab === "integrations" && (
          <div key="integrations" style={{ display: "grid", gap: 18, animation: "ds2SettingsIn .22s ease both" }}>
            <GitHubCard />
            <VercelCard />
          </div>
        )}

        {tab === "vault" && (
          <div key="vault" style={{ display: "grid", gap: 18, animation: "ds2SettingsIn .22s ease both" }}>
            <section className="card" data-testid="settings-vault">
              <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                <ShieldCheck size={14} /> Vault audit log
              </h3>
              <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 14px" }}>
                Every read / write against your encrypted key vault.
              </p>
              {audit.length === 0 ? (
                <p style={{ fontSize: 13, color: "var(--text-faint)", margin: 0 }}>No key activity yet.</p>
              ) : (
                <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8 }}>
                  {audit.slice(0, 10).map((e, i) => (
                    <li key={i} style={{
                      fontSize: 12, color: "var(--text-dim)",
                      fontFamily: "'JetBrains Mono', monospace",
                      padding: "6px 0", borderBottom: "1px solid var(--border)",
                    }}>
                      [{e.ts || e.created_at || "?"}] {e.action || e.event || "?"} · {e.key_name || e.key || "—"}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </div>

      <style>{`
        @keyframes ds2SettingsIn {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div style={{
      display: "flex", gap: 14, alignItems: "baseline",
      padding: "8px 0", borderBottom: "1px solid var(--border)",
      fontSize: 13,
    }}>
      <span style={{
        fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
        textTransform: "uppercase", letterSpacing: "0.15em",
        color: "var(--text-faint)", width: 130,
      }}>{k}</span>
      <span style={{ color: "var(--text)" }}>{v}</span>
    </div>
  );
}


// Iter 212m-235 — Switch Personal ↔ Developer Track from Settings.
function TrackSwitcher({ currentTrack, onSwitched }) {
  const [showConfirm, setShowConfirm] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const nav = useNavigate();
  const target = currentTrack === "personal" ? "developer" : "personal";
  const targetLabel = target === "personal" ? "Personal Track" : "Developer Track";
  const currentLabel = currentTrack === "personal" ? "Personal Track" : "Developer Track";

  async function apply() {
    setBusy(true);
    try {
      await api.post("/auth/set-track", { track: target });
      onSwitched?.(target);
      // Route to the destination track's home surface so the change
      // is immediately visible without a manual refresh.
      nav(target === "personal" ? "/build" : "/dashboard", { replace: true });
    } catch (e) {
      setBusy(false);
      setShowConfirm(false);
      // eslint-disable-next-line no-alert
      alert("Couldn't switch modes just now. Please try again.");
    }
  }

  return (
    <section className="card" data-testid="settings-track-switcher">
      <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14 }}>
        Workspace mode
      </h3>
      <p style={{ fontSize: 13, color: "var(--text-dim)", margin: "0 0 14px", lineHeight: 1.6 }}>
        You&apos;re on <strong style={{ color: "var(--text)" }}>{currentLabel}</strong>.
        {target === "personal"
          ? " Personal Track is the no-code, chat-based way to build apps from an idea."
          : " Developer Track is the full-control workspace — connect your own repos, deploy, and manage everything."}
      </p>
      <button
        className="btn-ghost"
        data-testid="settings-switch-mode-button"
        onClick={() => setShowConfirm(true)}
        style={{ fontSize: 13 }}
      >
        Switch to {targetLabel} →
      </button>

      {showConfirm && (
        <div
          data-testid="track-switch-confirm-modal"
          style={{
            position: "fixed", inset: 0, zIndex: 60,
            background: "rgba(0,0,0,0.55)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 24,
          }}
          onClick={() => !busy && setShowConfirm(false)}
        >
          <div
            role="dialog" aria-modal="true"
            onClick={(e) => e.stopPropagation()}
            style={{
              maxWidth: 440, width: "100%",
              background: "var(--bg, #0a0e1a)",
              border: "1px solid var(--border)",
              borderRadius: 12, padding: 24,
            }}
          >
            <h3 style={{ fontSize: 18, margin: "0 0 8px", color: "var(--text)" }}>
              Switch to {targetLabel}?
            </h3>
            <p style={{ fontSize: 14, color: "var(--text-dim)", lineHeight: 1.6, margin: "0 0 20px" }}>
              Your projects and settings stay where they are — only the workspace
              you see will change. You can switch back anytime.
            </p>
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button
                className="btn-ghost"
                data-testid="track-switch-cancel"
                disabled={busy}
                onClick={() => setShowConfirm(false)}
                style={{ fontSize: 13 }}
              >Cancel</button>
              <button
                data-testid="track-switch-confirm"
                disabled={busy}
                onClick={apply}
                style={{
                  fontSize: 13, padding: "8px 18px",
                  borderRadius: 6, border: "1px solid var(--accent, #ff8a2a)",
                  background: "var(--accent, #ff8a2a)", color: "#0a0e1a",
                  fontWeight: 600, cursor: busy ? "wait" : "pointer",
                }}
              >{busy ? "Switching…" : `Yes, switch`}</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
