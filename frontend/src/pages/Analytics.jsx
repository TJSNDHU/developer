/**
 * Analytics.jsx — Trust + uptime + deploy count.
 */
import React, { useEffect, useState } from "react";
import { BarChart3, Activity, Rocket } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import OraWrapped from "../components/OraWrapped";
import { api } from "../lib/api";

export default function Analytics() {
  const [uptime, setUptime] = useState(null);
  const [deployCount, setDeployCount] = useState(null);

  useEffect(() => {
    api.get("/trust/uptime").then((r) => setUptime(r.data)).catch(() => {});
    api.get("/trust/deploy-count").then((r) => setDeployCount(r.data)).catch(() => {});
  }, []);

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="trust metrics"
        title="Analytics"
        sub="System health, uptime, and deploy throughput."
      />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16, maxWidth: 820 }}>
        <Stat icon={Activity} label="uptime (s)"
              value={uptime?.uptime_s ?? "—"} testid="analytics-uptime" />
        <Stat icon={Rocket} label="deploys (total)"
              value={deployCount?.count ?? deployCount?.total ?? 0} testid="analytics-deploys" />
        <Stat icon={BarChart3} label="status"
              value={uptime?.ok ? "healthy" : "degraded"} testid="analytics-status"
              color={uptime?.ok ? "var(--ok)" : "var(--danger)"} />
      </div>

      {/* ORA Wrapped — personal monthly recap card with share button */}
      <div style={{ marginTop: 32, maxWidth: 820 }}>
        <OraWrapped />
      </div>
    </Shell>
  );
}

function Stat({ icon: Icon, label, value, testid, color }) {
  return (
    <div className="card" data-testid={testid}>
      <div style={{ display: "flex", alignItems: "center", gap: 8,
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 10, color: "var(--text-faint)",
                    textTransform: "uppercase", letterSpacing: "0.18em",
                    marginBottom: 14 }}>
        <Icon size={11} /> {label}
      </div>
      <div className="serif" style={{ fontSize: 28, color: color || "var(--accent-2)" }}>{value}</div>
    </div>
  );
}
