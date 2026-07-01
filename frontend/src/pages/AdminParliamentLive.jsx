/**
 * pages/AdminParliamentLive.jsx — Iter 212m-171
 *
 * Per-council live status card + full page.  Reads /admin/parliament/live
 * every 30 s and shows Council A/B/C + CEO Judge state (model, calls,
 * rescues, LongCat live flag).
 */
import React, { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import { RefreshCw, Loader2 } from "lucide-react";

const WINDOWS = [
  { id: 1,   label: "1h"  },
  { id: 24,  label: "24h" },
  { id: 168, label: "7d"  },
];

export function ParliamentLivePanel() {
  const [win, setWin] = useState(24);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.get("/admin/parliament/live",
                              { params: { window_hours: win } });
      setData(r.data);
    } catch { setData({ ok: false }); }
    finally { setBusy(false); }
  }, [win]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30 * 1000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div data-testid="parliament-live-panel" style={{
      background: "var(--panel-2)", border: "1px solid var(--border)",
      borderRadius: 4, padding: 16, marginBottom: 16,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12,
                     marginBottom: 12 }}>
        <div style={{ fontSize: 11, color: "var(--text-faint)",
                       textTransform: "uppercase", letterSpacing: "0.08em" }}>
          Parliament Live Status
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {WINDOWS.map((w) => (
            <button key={w.id}
                    data-testid={`parliament-window-${w.id}`}
                    onClick={() => setWin(w.id)}
                    style={{
                      padding: "3px 8px", fontSize: 10, borderRadius: 3,
                      background: win === w.id ? "var(--accent, #ff8a2a)" : "transparent",
                      color: win === w.id ? "#0a0c10" : "var(--text-dim)",
                      border: "1px solid var(--border)",
                      cursor: "pointer",
                    }}>
              {w.label}
            </button>
          ))}
        </div>
        <button onClick={load} disabled={busy}
                data-testid="parliament-refresh"
                style={{ background: "transparent",
                         border: "1px solid var(--border)",
                         color: "var(--text-dim)", padding: "3px 8px",
                         borderRadius: 3, cursor: "pointer", fontSize: 10,
                         display: "flex", alignItems: "center", gap: 4 }}>
          {busy ? <Loader2 size={10} className="spin" /> : <RefreshCw size={10} />}
        </button>
        {data && (
          <div style={{ marginLeft: "auto", fontSize: 10,
                        color: data.longcat_live ? "#4ade80" : "#fbbf24" }}>
            LongCat: {data.longcat_live ? "LIVE ✓" : "FALLBACK ⚠"}
          </div>
        )}
      </div>

      {!data ? (
        <div style={{ fontSize: 11, color: "var(--text-faint)" }}>Loading…</div>
      ) : (
        <div style={{ display: "grid",
                      gridTemplateColumns: "80px 1fr 220px 220px 90px",
                      rowGap: 6, columnGap: 12,
                      fontSize: 11 }}>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         textTransform: "uppercase" }}>Council</div>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         textTransform: "uppercase" }}>Role</div>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         textTransform: "uppercase" }}>Primary</div>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         textTransform: "uppercase" }}>Fallback</div>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         textTransform: "uppercase" }}>Calls</div>
          {(data.councils || []).map((c) => (
            <React.Fragment key={c.id}>
              <div style={{ color: "var(--accent-2, #4ade80)",
                            fontFamily: "'JetBrains Mono', monospace" }}
                   data-testid={`council-${c.id}-row`}>
                {c.id}
              </div>
              <div style={{ color: "var(--text)" }}>{c.label}</div>
              <div style={{ color: "var(--text-dim)",
                            fontFamily: "'JetBrains Mono', monospace" }}>
                {c.model_primary}
              </div>
              <div style={{ color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace" }}>
                {c.model_fallback}
              </div>
              <div style={{ color: "var(--text-dim)" }}>
                {c.calls}{c.rescues !== undefined
                  ? <span style={{ color: "#fbbf24", marginLeft: 6 }}>
                      ({c.rescues} rescue{c.rescues !== 1 ? "s" : ""})
                    </span>
                  : null}
              </div>
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

export default function AdminParliamentLive() {
  return (
    <div style={{ padding: "24px 20px", maxWidth: 1100 }}>
      <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0,
                   color: "var(--text)", marginBottom: 16 }}>
        Parliament Live
      </h1>
      <ParliamentLivePanel />
    </div>
  );
}
