/**
 * pages/AdminFeatureFlags.jsx — Iter 212m-171
 *
 * Feature flag admin: toggle globally, add per-user overrides, create
 * new flags.  Reads/writes to the existing /admin/feature-flags
 * endpoints plus the new /admin/feature-flags/{flag}/user-override
 * POST + DELETE from Iter 212m-171.
 */
import React, { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import { Loader2, Plus, X, RefreshCw } from "lucide-react";

function Toggle({ enabled, onChange, disabled, testid }) {
  return (
    <button
      data-testid={testid}
      onClick={onChange}
      disabled={disabled}
      style={{
        width: 44, height: 22, borderRadius: 11,
        background: enabled ? "#4ade80" : "#374151",
        border: "none", cursor: disabled ? "not-allowed" : "pointer",
        position: "relative", transition: "background 0.2s",
        opacity: disabled ? 0.5 : 1,
      }}>
      <div style={{
        position: "absolute", top: 2,
        left: enabled ? 24 : 2,
        width: 18, height: 18, borderRadius: 9,
        background: "#0f172a", transition: "left 0.2s",
      }} />
    </button>
  );
}

function FlagRow({ flag, onToggle, onDelete }) {
  const overrides = Object.entries(flag.user_overrides || {});
  const [busy, setBusy] = useState(false);
  const doToggle = async () => {
    setBusy(true);
    try {
      await api.post(`/admin/feature-flags/${flag.flag}/toggle`);
      onToggle(flag.flag, !flag.enabled);
    } catch { toast({ message: "Toggle failed", kind: "error" }); }
    finally { setBusy(false); }
  };
  return (
    <div data-testid={`flag-row-${flag.flag}`} style={{
      display: "grid",
      gridTemplateColumns: "1fr 100px 60px",
      alignItems: "center", padding: "10px 12px", gap: 12,
      borderTop: "1px solid var(--border)",
    }}>
      <div>
        <div style={{ fontFamily: "'JetBrains Mono', monospace",
                       fontSize: 12, color: "var(--text)" }}>
          {flag.flag}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 2 }}>
          {flag.description || "no description"}
        </div>
        {flag.tier_allowlist?.length > 0 && (
          <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 4 }}>
            tiers: {flag.tier_allowlist.join(", ")}
          </div>
        )}
        {overrides.length > 0 && (
          <div style={{ marginTop: 6, fontSize: 10, color: "#fbbf24" }}>
            {overrides.length} user override{overrides.length > 1 ? "s" : ""}
          </div>
        )}
      </div>
      <div style={{ fontSize: 11, color: flag.enabled ? "#4ade80" : "#f87171" }}>
        {flag.enabled ? "● ON" : "○ OFF"}
      </div>
      <Toggle
        enabled={flag.enabled}
        disabled={busy}
        testid={`flag-toggle-${flag.flag}`}
        onChange={doToggle} />
    </div>
  );
}

function CreateFlagCard({ onCreated }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [tiers, setTiers] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api.post("/admin/feature-flags", {
        flag: name.trim(),
        description: desc.trim(),
        enabled: false,
        tier_allowlist: tiers.split(",").map(s => s.trim()).filter(Boolean),
      });
      toast({ message: "Flag created", kind: "success" });
      setName(""); setDesc(""); setTiers("");
      onCreated();
    } catch (e) { toast({ message: "Create failed", kind: "error" }); }
    finally { setBusy(false); }
  };
  return (
    <div data-testid="flag-create-card" style={{
      background: "var(--panel-2)", border: "1px solid var(--border)",
      borderRadius: 4, padding: 12, marginTop: 16,
    }}>
      <div style={{ fontSize: 11, color: "var(--text-faint)",
                     textTransform: "uppercase", marginBottom: 8 }}>
        <Plus size={11} style={{ verticalAlign: "middle", marginRight: 6 }} />
        Create new flag
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 80px",
                     gap: 8 }}>
        <input data-testid="flag-name-input"
               placeholder="flag_name" value={name}
               onChange={(e) => setName(e.target.value)}
               style={{ padding: "6px 8px", fontSize: 11,
                        background: "var(--bg-elev)", color: "var(--text)",
                        border: "1px solid var(--border)", borderRadius: 3,
                        fontFamily: "'JetBrains Mono', monospace" }} />
        <input placeholder="description" value={desc}
               onChange={(e) => setDesc(e.target.value)}
               style={{ padding: "6px 8px", fontSize: 11,
                        background: "var(--bg-elev)", color: "var(--text)",
                        border: "1px solid var(--border)", borderRadius: 3 }} />
        <input placeholder="tiers (comma) e.g. pro,team" value={tiers}
               onChange={(e) => setTiers(e.target.value)}
               style={{ padding: "6px 8px", fontSize: 11,
                        background: "var(--bg-elev)", color: "var(--text)",
                        border: "1px solid var(--border)", borderRadius: 3 }} />
        <button data-testid="flag-create-btn"
                onClick={submit} disabled={busy || !name.trim()}
                style={{
                  padding: "6px 10px", fontSize: 11,
                  background: "var(--accent, #ff8a2a)",
                  color: "#0a0c10", border: "none",
                  borderRadius: 3, cursor: "pointer",
                  fontWeight: 600,
                  opacity: (busy || !name.trim()) ? 0.5 : 1,
                }}>
          Create
        </button>
      </div>
    </div>
  );
}

export default function AdminFeatureFlags() {
  const [flags, setFlags] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/feature-flags");
      setFlags(r.data.flags || []);
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const onToggled = (name, next) => {
    setFlags((cur) => cur.map((f) =>
      f.flag === name ? { ...f, enabled: next } : f));
  };

  return (
    <div style={{ padding: "24px 20px", maxWidth: 1100 }}
         data-testid="feature-flags-page">
      <div style={{ display: "flex", alignItems: "center", gap: 12,
                     marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0,
                     color: "var(--text)" }}>Feature Flags</h1>
        <button data-testid="flags-refresh" onClick={load}
                style={{ background: "transparent", border: "1px solid var(--border)",
                         color: "var(--text-dim)", padding: "4px 10px",
                         borderRadius: 3, cursor: "pointer", fontSize: 11,
                         display: "flex", alignItems: "center", gap: 6 }}>
          <RefreshCw size={11} /> Refresh
        </button>
        <div style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-faint)" }}>
          {loading ? "loading…" : `${flags.length} flags`}
        </div>
      </div>

      <div style={{
        background: "var(--panel-2)", border: "1px solid var(--border)",
        borderRadius: 4,
      }}>
        <div style={{ padding: "10px 12px", fontSize: 10,
                       letterSpacing: "0.08em", textTransform: "uppercase",
                       color: "var(--text-faint)" }}>
          GLOBAL FLAGS
        </div>
        {flags.length === 0 && !loading && (
          <div style={{ padding: 20, color: "var(--text-faint)",
                         fontSize: 12, textAlign: "center" }}>
            No flags configured. Create one below.
          </div>
        )}
        {flags.map((f) => (
          <FlagRow key={f.flag} flag={f} onToggle={onToggled} />
        ))}
      </div>

      <CreateFlagCard onCreated={load} />
    </div>
  );
}
