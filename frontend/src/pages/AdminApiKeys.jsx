/**
 * pages/AdminApiKeys.jsx — MCP API key management UI.
 *
 * Wires to:
 *   POST   /api/aurem-dev/mcp/keys           → mint new key
 *   GET    /api/aurem-dev/mcp/keys           → list masked keys
 *   DELETE /api/aurem-dev/mcp/keys/{tail}    → revoke
 *
 * The full `sk-aurem-…` secret is returned exactly once by POST /keys
 * and shown to the user with a copy button + "this is the only time
 * you'll see it" warning. We never re-display the secret after the
 * banner is dismissed — the GET list returns masked values only.
 */
import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, getToken } from "../lib/api";
import { toast } from "../components/Toast";

function timeSince(epoch) {
  if (!epoch) return "never used";
  const s = Math.max(1, Math.round(Date.now() / 1000 - epoch));
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export default function AdminApiKeys() {
  const nav = useNavigate();
  const [keys, setKeys]         = useState([]);
  const [loading, setLoading]   = useState(true);
  const [err, setErr]           = useState("");
  const [busy, setBusy]         = useState(false);
  // The newly-minted full key — shown ONCE then cleared from state.
  const [newKey, setNewKey]     = useState(null);

  const load = useCallback(async () => {
    setErr("");
    setLoading(true);
    try {
      const r = await api.get("/mcp/keys", {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setKeys(r.data?.keys || []);
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "Failed to load keys.";
      setErr(detail);
      if (e?.response?.status === 401) {
        nav("/login?next=/admin/api-keys", { replace: true });
      }
    } finally {
      setLoading(false);
    }
  }, [nav]);

  useEffect(() => { load(); }, [load]);

  const mintKey = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.post("/mcp/keys", {}, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setNewKey(r.data?.key || "");
      await load();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "Failed to generate key.";
      setErr(detail);
      toast({ message: detail, kind: "error", duration: 3500 });
    } finally {
      setBusy(false);
    }
  };

  const revokeKey = async (masked) => {
    // Masked format from backend: "sk-aurem-XXXX…YYYY" — we use the
    // last 4 chars as the URL-safe revocation tail.
    const tail = masked.split("…").pop() || "";
    if (tail.length < 4) {
      toast({ message: "Could not parse key tail.", kind: "error" });
      return;
    }
    if (!window.confirm(`Revoke this key permanently? Tail: …${tail}`)) return;
    try {
      const r = await api.delete(`/mcp/keys/${encodeURIComponent(tail)}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      toast({
        message: `Revoked ${r.data?.revoked || 0} key(s).`,
        kind: "success", duration: 2500,
      });
      await load();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "Revoke failed.";
      toast({ message: detail, kind: "error", duration: 3500 });
    }
  };

  const copy = (text) => {
    navigator.clipboard.writeText(text);
    toast({ message: "Copied to clipboard.", kind: "success", duration: 1500 });
  };

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: "32px 24px" }}>
      <div style={{ marginBottom: 8, color: "var(--text-faint)", fontSize: 11,
                    letterSpacing: "0.18em", textTransform: "uppercase" }}>
        ADMIN · MCP
      </div>
      <h1 style={{ fontSize: 28, margin: "0 0 6px", letterSpacing: "-0.01em" }}>
        API Keys
      </h1>
      <p style={{ color: "var(--text-dim)", fontSize: 14, margin: "0 0 28px",
                  lineHeight: 1.6, maxWidth: 680 }}>
        Long-lived <code style={codeChip}>sk-aurem-…</code> keys for MCP
        clients (Claude Desktop, Cursor, Cline). Each key inherits your
        account&apos;s permissions and can be revoked anytime. Treat them
        like passwords — never commit them to git.
      </p>

      {/* Mint button */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 24 }}>
        <button
          data-testid="mint-mcp-key"
          onClick={mintKey}
          disabled={busy}
          style={btnPrimary(busy)}
        >
          {busy ? "Generating…" : "+ Generate new key"}
        </button>
        <button
          data-testid="refresh-mcp-keys"
          onClick={load}
          disabled={loading}
          style={btnGhost}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {err && (
        <div style={errBanner} data-testid="mcp-keys-err">{err}</div>
      )}

      {/* New-key reveal banner — shown EXACTLY once */}
      {newKey && (
        <div data-testid="new-mcp-key-banner" style={revealBanner}>
          <div style={{ fontSize: 11, color: "#ffb454",
                        letterSpacing: "0.12em", textTransform: "uppercase",
                        marginBottom: 8 }}>
            ⚠ Copy this now — it won&apos;t be shown again
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center",
                        flexWrap: "wrap" }}>
            <code data-testid="new-mcp-key-value" style={keyDisplay}>
              {newKey}
            </code>
            <button
              data-testid="copy-mcp-key"
              onClick={() => copy(newKey)}
              style={btnGhost}
            >
              Copy
            </button>
            <button
              data-testid="dismiss-mcp-key"
              onClick={() => setNewKey(null)}
              style={btnGhost}
            >
              I&apos;ve saved it
            </button>
          </div>
          <div style={{ fontSize: 12, color: "var(--text-dim)",
                        marginTop: 12, lineHeight: 1.6 }}>
            Use as <code style={codeChip}>Bearer {newKey.slice(0, 14)}…</code>{" "}
            in your MCP client config. Endpoint:{" "}
            <code style={codeChip}>https://auremcto.com/api/aurem-dev/mcp</code>
          </div>
        </div>
      )}

      {/* Existing keys table */}
      <h3 style={sectionLabel}>Active keys ({keys.length})</h3>
      {loading && keys.length === 0 ? (
        <div style={emptyState}>Loading…</div>
      ) : keys.length === 0 ? (
        <div data-testid="mcp-keys-empty" style={emptyState}>
          No API keys yet. Click &quot;Generate new key&quot; above to create one.
        </div>
      ) : (
        <div style={tableWrap}>
          <table style={{ width: "100%", borderCollapse: "collapse",
                          fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)",
                           color: "var(--text-faint)",
                           textTransform: "uppercase",
                           fontSize: 10, letterSpacing: "0.1em" }}>
                <th style={th}>Key</th>
                <th style={th}>Created</th>
                <th style={th}>Last used</th>
                <th style={th}>Status</th>
                <th style={{ ...th, textAlign: "right" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k, i) => (
                <tr key={i} data-testid={`mcp-key-row-${i}`}
                    style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={td}>
                    <code style={codeChip}>{k.key_masked || "—"}</code>
                    {k.label && (
                      <span style={{ marginLeft: 8, fontSize: 11,
                                     color: "var(--text-faint)" }}>
                        {k.label}
                      </span>
                    )}
                  </td>
                  <td style={td}>{timeSince(k.created_at)}</td>
                  <td style={td}>{timeSince(k.last_used_at)}</td>
                  <td style={td}>
                    <span style={k.active ? badgeOk : badgeRevoked}>
                      {k.active ? "Active" : "Revoked"}
                    </span>
                  </td>
                  <td style={{ ...td, textAlign: "right" }}>
                    {k.active && (
                      <button
                        data-testid={`revoke-mcp-key-${i}`}
                        onClick={() => revokeKey(k.key_masked)}
                        style={btnDanger}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Claude Desktop config hint */}
      <div style={hintBox}>
        <div style={{ fontSize: 11, color: "var(--text-faint)",
                      letterSpacing: "0.12em", textTransform: "uppercase",
                      marginBottom: 10 }}>
          Claude Desktop · config snippet
        </div>
        <pre style={preBlock}>{`{
  "mcpServers": {
    "aurem-cto": {
      "url": "https://auremcto.com/api/aurem-dev/mcp",
      "headers": {
        "Authorization": "Bearer sk-aurem-YOUR_KEY_HERE"
      }
    }
  }
}`}</pre>
        <div style={{ fontSize: 11, color: "var(--text-dim)",
                      marginTop: 8, lineHeight: 1.6 }}>
          Paste into{" "}
          <code style={codeChip}>~/Library/Application Support/Claude/claude_desktop_config.json</code>
          {" "}(macOS) or{" "}
          <code style={codeChip}>%APPDATA%\Claude\claude_desktop_config.json</code>
          {" "}(Windows). Restart Claude Desktop after editing.
        </div>
      </div>
    </div>
  );
}

// ── Styles ──────────────────────────────────────────────────────────
const codeChip = {
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 11.5,
  background: "var(--card-2)",
  padding: "2px 6px",
  borderRadius: 4,
  border: "1px solid var(--border)",
  color: "var(--text)",
};
const btnPrimary = (disabled) => ({
  padding: "10px 18px", fontSize: 13, fontWeight: 600,
  background: disabled ? "var(--card-2)" : "var(--accent)",
  color: disabled ? "var(--text-faint)" : "#0a0a0a",
  border: "none", borderRadius: 6,
  cursor: disabled ? "not-allowed" : "pointer",
  fontFamily: "inherit",
});
const btnGhost = {
  padding: "8px 14px", fontSize: 12,
  background: "transparent",
  color: "var(--text-dim)",
  border: "1px solid var(--border)", borderRadius: 6,
  cursor: "pointer", fontFamily: "inherit",
};
const btnDanger = {
  padding: "6px 12px", fontSize: 11,
  background: "transparent",
  color: "#ff6b6b",
  border: "1px solid rgba(255,107,107,0.3)",
  borderRadius: 4, cursor: "pointer", fontFamily: "inherit",
};
const errBanner = {
  padding: 12, marginBottom: 16,
  background: "rgba(255,107,107,0.08)",
  border: "1px solid rgba(255,107,107,0.3)",
  borderRadius: 6, color: "#ff6b6b", fontSize: 13,
};
const revealBanner = {
  padding: 18, marginBottom: 24,
  background: "rgba(255,180,84,0.06)",
  border: "1px solid rgba(255,180,84,0.3)",
  borderRadius: 8,
};
const keyDisplay = {
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 12, padding: "8px 12px",
  background: "var(--bg)", border: "1px solid var(--border)",
  borderRadius: 4, color: "var(--accent)",
  wordBreak: "break-all", flex: 1, minWidth: 0,
};
const sectionLabel = {
  fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase",
  color: "var(--text-faint)", margin: "0 0 12px",
};
const emptyState = {
  padding: 32, textAlign: "center",
  color: "var(--text-faint)", fontSize: 13,
  background: "var(--card)",
  border: "1px dashed var(--border)", borderRadius: 8,
};
const tableWrap = {
  border: "1px solid var(--border)", borderRadius: 8,
  overflow: "hidden", background: "var(--card)",
};
const th = { padding: "12px 14px", textAlign: "left", fontWeight: 500 };
const td = { padding: "12px 14px", color: "var(--text)" };
const badgeOk = {
  display: "inline-block", padding: "2px 8px", borderRadius: 10,
  fontSize: 11, color: "#6dd4a1",
  background: "rgba(109,212,161,0.10)",
  border: "1px solid rgba(109,212,161,0.3)",
};
const badgeRevoked = {
  display: "inline-block", padding: "2px 8px", borderRadius: 10,
  fontSize: 11, color: "var(--text-faint)",
  background: "rgba(136,141,153,0.10)",
  border: "1px solid var(--border)",
};
const hintBox = {
  marginTop: 32, padding: 18,
  background: "var(--card)", border: "1px solid var(--border)",
  borderRadius: 8,
};
const preBlock = {
  margin: 0, padding: 14, fontSize: 11.5, lineHeight: 1.5,
  fontFamily: "'JetBrains Mono', monospace",
  background: "var(--bg)", border: "1px solid var(--border)",
  borderRadius: 6, color: "var(--text-dim)",
  overflow: "auto",
};
