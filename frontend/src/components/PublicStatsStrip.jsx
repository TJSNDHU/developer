/**
 * components/PublicStatsStrip.jsx — Iter 47
 *
 * Renders a live trust strip on the landing page:
 *   "29 developers · 152 tasks shipped · 84.2% Claude-corrected · 7 lint blocks"
 *
 * Polls /api/aurem-dev/usage/public/stats (no auth) every 60s.
 * Quietly disappears if the endpoint is unreachable.
 */
import { useEffect, useState } from "react";
import axios from "axios";

const API = process.env.REACT_APP_BACKEND_URL || "";
const POLL_MS = 60_000;

export default function PublicStatsStrip() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let alive = true;
    async function fetchOnce() {
      try {
        const r = await axios.get(`${API}/api/aurem-dev/usage/public/stats`, { timeout: 5000 });
        if (alive && r.data && r.data.available) setStats(r.data);
      } catch { /* silent — strip just hides */ }
    }
    fetchOnce();
    const t = setInterval(fetchOnce, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!stats) return null;

  const fmt = (n) => (n || 0).toLocaleString();
  const items = [
    { v: fmt(stats.users),               l: "developers" },
    { v: fmt(stats.tasks_shipped),       l: "tasks shipped" },
    { v: `${stats.correction_rate_pct ?? 0}%`, l: "Claude-corrected" },
    { v: fmt(stats.lint_blocks_caught),  l: "lint blocks caught" },
  ];

  return (
    <section
      data-testid="public-stats-strip"
      style={{
        marginTop: 40, padding: "18px 22px",
        borderRadius: 6,
        border: "1px solid rgba(34, 197, 94, 0.18)",
        background: "rgba(34, 197, 94, 0.04)",
        display: "flex", flexWrap: "wrap", gap: 32,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12, letterSpacing: "0.05em",
        alignItems: "center", justifyContent: "space-between",
      }}
    >
      <span style={{
        color: "#22c55e", fontSize: 10,
        fontWeight: 700, textTransform: "uppercase",
      }}>● live</span>
      {items.map((it, i) => (
        <div key={i} data-testid={`public-stat-${i}`} style={{ display: "flex", flexDirection: "column" }}>
          <span style={{
            fontSize: 18, fontWeight: 700, color: "var(--text)",
            fontFamily: "'JetBrains Mono', monospace",
          }}>{it.v}</span>
          <span style={{ fontSize: 10, color: "var(--text-faint)" }}>{it.l}</span>
        </div>
      ))}
    </section>
  );
}
