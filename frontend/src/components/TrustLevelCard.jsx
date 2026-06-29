/**
 * TrustLevelCard.jsx — Iter 212m-117
 *
 * Lets the user pick their Loop Mode trust level:
 *   L1 — Report Only       (Plan generated, no code written)
 *   L2 — Assisted (default) (Full pipeline + manual Ship gate)
 *   L3 — Unattended        (Full pipeline + auto-ship, no pause)
 *
 * Three radio cards. Persists via PUT /api/aurem-dev/me/trust-level.
 */
import React, { useEffect, useState } from "react";
import { ShieldCheck, ShieldAlert, Bot } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "sonner";

const LEVELS = [
  {
    id:      "L1",
    label:   "L1 — Report Only",
    Icon:    ShieldCheck,
    color:   "#22c55e",
    tagline: "Plan generated, never writes code.",
    detail:  "Safest. The AI plans the change and shows you the file list, but Execute / Verify / Scan / Ship are ALL skipped. No commits, no tokens spent on code generation. Good for read-only audits.",
  },
  {
    id:      "L2",
    label:   "L2 — Assisted (default)",
    Icon:    ShieldAlert,
    color:   "#FF6608",
    tagline: "Full pipeline + manual Ship gate.",
    detail:  "Recommended. AI runs the full Plan → Execute → Verify → Scan pipeline. Pauses at Ship with a 'Ship to GitHub' button — nothing lands on your repo without your click.",
  },
  {
    id:      "L3",
    label:   "L3 — Unattended",
    Icon:    Bot,
    color:   "#ef4444",
    tagline: "Auto-ship — no pause.",
    detail:  "Power-user. Same pipeline as L2, but the manual Ship gate is BYPASSED. Commits land on the aurem/fix-<rule> branch + draft PR (branch-per-fix safety still applies). Use only if you fully trust the planner + verifier.",
  },
];

export default function TrustLevelCard() {
  const [level,   setLevel]   = useState("L2");
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get("/me/trust-level");
        const data = r?.data || r;
        if (!cancelled && data?.trust_level) setLevel(data.trust_level);
      } catch (e) {
        if (!cancelled) console.debug("trust-level fetch failed:", e?.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function pick(next) {
    if (next === level || saving) return;
    setSaving(true);
    const prior = level;
    setLevel(next);  // optimistic
    try {
      await api.put("/me/trust-level", { trust_level: next });
      toast.success(`Trust level → ${next}`);
    } catch (e) {
      setLevel(prior);
      toast.error(e?.response?.data?.detail || e?.message || "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section
      className="card"
      data-testid="settings-trust-level"
      style={{ gridColumn: "1 / -1" }}
    >
      <h3 style={{
        fontSize: 14, color: "var(--text)",
        margin: 0, marginBottom: 4,
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <ShieldAlert size={14} /> Loop Trust Level
      </h3>
      <p style={{
        fontSize: 12, color: "var(--text-faint)",
        margin: "0 0 16px",
      }}>
        Controls how autonomously Loop Mode + Fix can write to your repo. You can change this any time.
      </p>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
        gap: 12,
      }}>
        {LEVELS.map((lv) => {
          const Icon = lv.Icon;
          const active = level === lv.id;
          return (
            <button
              key={lv.id}
              type="button"
              onClick={() => pick(lv.id)}
              disabled={loading || saving}
              data-testid={`trust-level-${lv.id}`}
              data-selected={active}
              style={{
                textAlign: "left",
                padding: 14,
                borderRadius: 10,
                background: active
                  ? `linear-gradient(135deg, ${lv.color}1A, ${lv.color}05)`
                  : "rgba(255,255,255,0.02)",
                border: `1px solid ${active ? lv.color : "rgba(255,255,255,0.08)"}`,
                color: "var(--text)",
                cursor: (loading || saving) ? "wait" : "pointer",
                opacity: loading ? 0.55 : 1,
                fontFamily: "inherit",
                transition: "border-color 150ms ease, background 150ms ease",
              }}
            >
              <div style={{
                display: "flex", alignItems: "center", gap: 8,
                marginBottom: 6,
              }}>
                <Icon size={14} color={lv.color} />
                <strong style={{ fontSize: 12.5, color: active ? lv.color : "var(--text)" }}>
                  {lv.label}
                </strong>
                {active && (
                  <span style={{
                    marginLeft: "auto", fontSize: 10,
                    padding: "2px 6px", borderRadius: 4,
                    background: lv.color, color: "#0a0a0a",
                    fontWeight: 700, letterSpacing: 0.3,
                  }}>ACTIVE</span>
                )}
              </div>
              <div style={{
                fontSize: 11.5, color: "var(--text-dim)",
                marginBottom: 6, fontWeight: 600,
              }}>
                {lv.tagline}
              </div>
              <div style={{
                fontSize: 11, color: "var(--text-faint)",
                lineHeight: 1.55,
              }}>
                {lv.detail}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
