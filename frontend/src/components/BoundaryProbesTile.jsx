/**
 * components/BoundaryProbesTile.jsx — Iter 212m-171
 *
 * Small overview tile showing how many ORA boundary violations
 * (execute_bash refusals for `/app`, `/tmp`, `AUREM_MASTER_KEY`, …)
 * have fired today.  A non-zero count means someone (attacker, curious
 * user, or hallucinating LLM) is probing the ORA system boundary.
 *
 * Data: /admin/boundary-probes (24h window by default).
 */
import React, { useEffect, useState, useCallback } from "react";
import { api } from "../lib/api";
import { ShieldAlert } from "lucide-react";

export function BoundaryProbesTile() {
  const [d, setD] = useState(null);
  const load = useCallback(async () => {
    try {
      const r = await api.get("/admin/boundary-probes", { params: { window_hours: 24 } });
      setD(r.data);
    } catch { setD({ ok: false, count_today: 0 }); }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const count = d?.count_today ?? 0;
  const alert = count > 0;

  return (
    <div data-testid="boundary-probes-tile" style={{
      display: "flex", alignItems: "center", gap: 12,
      background: alert ? "#dc262615" : "var(--panel-2)",
      border: `1px solid ${alert ? "#dc262640" : "var(--border)"}`,
      borderRadius: 4, padding: 12, marginBottom: 16,
    }}>
      <ShieldAlert size={18}
                   color={alert ? "#f87171" : "var(--text-faint)"} />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 10, letterSpacing: "0.08em",
                      textTransform: "uppercase",
                      color: alert ? "#f87171" : "var(--text-faint)" }}>
          Boundary Probes · today
        </div>
        <div style={{ fontSize: 22, fontWeight: 600,
                      color: alert ? "#f87171" : "var(--text)",
                      marginTop: 2 }}
             data-testid="boundary-probes-count">
          {count}
        </div>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-faint)",
                    maxWidth: 240, textAlign: "right" }}>
        Non-zero = someone tried to inspect `/app/*`, `AUREM_MASTER_KEY`,
        or another ORA-internal path via execute_bash.
      </div>
    </div>
  );
}
