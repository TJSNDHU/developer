/**
 * Settings.jsx — Profile + API key vault.
 */
import React, { useEffect, useState } from "react";
import { User, KeyRound, Github } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api, getUser } from "../lib/api";

export default function Settings() {
  const [me, setMe] = useState(getUser());
  const [github, setGithub] = useState(null);
  const [audit, setAudit] = useState([]);

  useEffect(() => {
    api.get("/auth/me").then((r) => r.data?.user && setMe(r.data.user)).catch(() => {});
    api.get("/github/status").then((r) => setGithub(r.data)).catch(() => {});
    api.get("/vault/audit-log").then((r) => setAudit(r.data?.entries || r.data?.log || [])).catch(() => {});
  }, []);

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="account"
        title="Settings"
        sub="Profile, GitHub linkage, and API key vault audit trail."
      />
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 18, maxWidth: 920 }}>
        <section className="card" data-testid="settings-profile">
          <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <User size={14} /> Profile
          </h3>
          <Row k="email" v={me?.email || "—"} />
          <Row k="name" v={me?.name || "—"} />
          <Row k="user id" v={me?.user_id || "—"} />
          <Row k="tier" v={me?.tier || "free"} />
        </section>

        <section className="card" data-testid="settings-github">
          <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <Github size={14} /> GitHub integration
          </h3>
          <Row k="connected" v={github?.connected ? "yes" : "no"} />
          <Row k="org" v={github?.org || "—"} />
          <Row k="last sync" v={github?.last_sync || "—"} />
        </section>

        <section className="card" data-testid="settings-vault" style={{ gridColumn: "1 / -1" }}>
          <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <KeyRound size={14} /> Vault audit log
          </h3>
          {audit.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-faint)" }}>No key activity yet.</p>
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
    </Shell>
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
        color: "var(--text-faint)", width: 100,
      }}>{k}</span>
      <span style={{ color: "var(--text)" }}>{v}</span>
    </div>
  );
}
