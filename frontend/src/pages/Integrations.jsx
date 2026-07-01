/**
 * frontend/src/pages/Integrations.jsx  —  Iter 212m-174
 *
 * Public IDE-integration setup page.  Renders 4 tabs (Cursor, VS Code,
 * Claude Desktop, Claude Code CLI) with one-click install deep-links
 * and copy-paste configs, all pre-filled with the caller's real
 * sk-aurem- API key (minted on first visit).
 *
 * Data source: `GET /api/aurem-dev/mcp/install-links` (auto-mints an
 * API key if the user has none). Zero manual configuration.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate }             from "react-router-dom";
import { api, getToken }           from "../lib/api";
import { toast }                   from "../components/Toast";

const TABS = [
  { id: "cursor",         label: "Cursor",          icon: "🖱" },
  { id: "vscode",         label: "VS Code",         icon: "⚡" },
  { id: "claude_desktop", label: "Claude Desktop",  icon: "🧠" },
  { id: "claude_code",    label: "Claude Code CLI", icon: "⌨" },
];

export default function Integrations() {
  const navigate       = useNavigate();
  const [data,   setData]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [tab,     setTab]     = useState("cursor");
  const [testResult, setTestResult] = useState(null);

  const authed = !!getToken();

  const fetchLinks = useCallback(async () => {
    if (!authed) {
      setLoading(false);
      return;
    }
    setLoading(true); setError("");
    try {
      const r = await api.get("/mcp/install-links");
      setData(r.data);
      if (r.data?.api_key_new) {
        toast({
          message: "A new ORA API key was minted for your account.",
          kind: "success", duration: 4000,
        });
      }
    } catch (e) {
      setError(
        e?.response?.data?.detail ||
        e?.message ||
        "Could not load install links"
      );
    } finally {
      setLoading(false);
    }
  }, [authed]);

  useEffect(() => { fetchLinks(); }, [fetchLinks]);

  const copy = useCallback((text, label) => {
    try {
      navigator.clipboard.writeText(text);
      toast({ message: `${label} copied`, kind: "success", duration: 2000 });
    } catch {
      toast({ message: "Copy failed — please select manually.", kind: "error" });
    }
  }, []);

  const testConnection = useCallback(async () => {
    setTestResult({ status: "pending" });
    try {
      const r = await api.get("/mcp");
      const okShape =
        r.data?.protocolVersion &&
        Array.isArray(r.data?.tools) &&
        r.data.tools.length > 0;
      setTestResult({
        status: okShape ? "ok" : "warn",
        tools:  r.data?.tools?.length || 0,
        version: r.data?.protocolVersion,
      });
    } catch (e) {
      setTestResult({
        status: "error",
        msg:    e?.response?.data?.detail || e?.message || "unreachable",
      });
    }
  }, []);

  if (!authed) {
    return (
      <div data-testid="integrations-login-gate"
        style={styles.gate}>
        <h1 style={styles.h1}>Connect your IDE to ORA</h1>
        <p style={styles.gateP}>
          Sign in to generate your ORA API key and get one-click
          install links for Cursor, VS Code, Claude Desktop, and
          Claude Code CLI.
        </p>
        <button data-testid="integrations-login-btn"
          onClick={() => navigate("/login?next=/integrations")}
          style={styles.primaryBtn}>
          Sign in to continue
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div data-testid="integrations-loading" style={styles.center}>
        <p style={{ color: "#94a3b8" }}>Loading your integration setup…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div data-testid="integrations-error" style={styles.center}>
        <p style={{ color: "#f87171", marginBottom: 12 }}>{error}</p>
        <button onClick={fetchLinks} style={styles.primaryBtn}>Retry</button>
      </div>
    );
  }

  return (
    <div data-testid="integrations-page" style={styles.wrap}>
      <header style={styles.header}>
        <h1 style={styles.h1}>
          Connect ORA to your IDE
        </h1>
        <p style={styles.sub}>
          One-click install for Cursor, VS Code, Claude Desktop, and Claude Code CLI.
          Every request runs against your projects with strict per-user isolation
          (BINContext + ORAContext).
        </p>
      </header>

      {/* API key section */}
      <ApiKeyCard
        apiKey={data.api_key}
        isNew={data.api_key_new}
        endpoint={data.endpoint}
        onCopy={copy}
        onTestConnection={testConnection}
        testResult={testResult}
      />

      {/* Tab strip */}
      <nav data-testid="integrations-tabs" style={styles.tabRow}>
        {TABS.map(t => (
          <button
            key={t.id}
            data-testid={`integrations-tab-${t.id}`}
            onClick={() => setTab(t.id)}
            style={{
              ...styles.tabBtn,
              background: tab === t.id ? "#1e293b" : "transparent",
              color:      tab === t.id ? "#f8fafc" : "#94a3b8",
              borderColor: tab === t.id ? "#38bdf8" : "#1e293b",
            }}
          >
            <span style={{ marginRight: 8 }}>{t.icon}</span>{t.label}
          </button>
        ))}
      </nav>

      {/* Tab body */}
      <section style={styles.tabBody}>
        {tab === "cursor"         && <CursorTab       data={data} onCopy={copy} />}
        {tab === "vscode"         && <VSCodeTab       data={data} onCopy={copy} />}
        {tab === "claude_desktop" && <ClaudeDesktopTab data={data} onCopy={copy} />}
        {tab === "claude_code"    && <ClaudeCodeTab   data={data} onCopy={copy} />}
      </section>

      <footer style={styles.footer}>
        <p style={{ color: "#64748b", fontSize: 13 }}>
          <strong style={{ color: "#94a3b8" }}>What ORA can do in your IDE:</strong>{" "}
          list projects, read/write repo files, semantic search, run Vanguard
          security scans, fetch commit history, and ship code via natural language.
        </p>
      </footer>
    </div>
  );
}

/* ─── Sub-components ────────────────────────────────────────────── */

function ApiKeyCard({ apiKey, isNew, endpoint, onCopy, onTestConnection, testResult }) {
  const [reveal, setReveal] = useState(isNew);
  const masked = apiKey ? `${apiKey.slice(0, 14)}…${apiKey.slice(-4)}` : "";
  return (
    <div data-testid="api-key-card" style={styles.card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h3 style={styles.cardH}>Your ORA API Key</h3>
          <p style={styles.cardSub}>
            Used by every IDE integration below. Treat it like a password —
            you can revoke it any time from Admin → API Keys.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          <button data-testid="test-connection-btn"
            onClick={onTestConnection}
            style={styles.secondaryBtn}>
            Test Connection
          </button>
        </div>
      </div>
      <div style={styles.keyRow}>
        <code data-testid="api-key-display" style={styles.keyCode}>
          {reveal ? apiKey : masked}
        </code>
        <button onClick={() => setReveal(v => !v)}
          data-testid="api-key-reveal-btn"
          style={styles.miniBtn}>{reveal ? "Hide" : "Reveal"}</button>
        <button onClick={() => onCopy(apiKey, "API key")}
          data-testid="api-key-copy-btn"
          style={styles.miniBtnPrimary}>Copy</button>
      </div>
      <p style={styles.endpointLine}>
        Endpoint:&nbsp;
        <code style={styles.inlineCode}>{endpoint}</code>
      </p>
      {testResult && (
        <div data-testid="test-connection-result" style={{
          marginTop: 12, padding: "8px 12px", borderRadius: 6,
          background: testResult.status === "ok"    ? "#052e2b"
                    : testResult.status === "error" ? "#3f1d1d"
                    :                                 "#1e293b",
          color: testResult.status === "ok"    ? "#5eead4"
               : testResult.status === "error" ? "#fca5a5"
               :                                 "#cbd5e1",
          fontSize: 13,
        }}>
          {testResult.status === "pending" && "Pinging /mcp…"}
          {testResult.status === "ok"      && `✓ ORA reachable — ${testResult.tools} tools available (protocol ${testResult.version})`}
          {testResult.status === "warn"    && "⚠ Unexpected response shape — please retry."}
          {testResult.status === "error"   && `✗ ${testResult.msg}`}
        </div>
      )}
    </div>
  );
}

function CursorTab({ data, onCopy }) {
  return (
    <div data-testid="cursor-tab-content">
      <Step n={1} title="Install in one click">
        <p style={styles.p}>
          Cursor 0.42 or newer required. This will open Cursor with ORA
          pre-configured.
        </p>
        <a data-testid="cursor-install-link"
          href={data.cursor}
          style={styles.installBtn}>
          Install ORA in Cursor&nbsp;↗
        </a>
        <button data-testid="cursor-copy-link"
          onClick={() => onCopy(data.cursor, "Cursor install link")}
          style={styles.linkBtn}>
          copy install link
        </button>
      </Step>
      <Step n={2} title="Test it in Cursor">
        <p style={styles.p}>Open Cursor's chat and try:</p>
        <code style={styles.exampleCode}>list my ORA projects</code>
        <p style={styles.pSmall}>
          Cursor will call the <code>list_projects</code> tool on your ORA
          MCP server and show your connected repos.
        </p>
      </Step>
      <Step n={3} title="Manual fallback">
        <p style={styles.pSmall}>
          If the deep-link doesn't work, paste this into Cursor Settings
          → MCP Servers:
        </p>
        <ConfigBlock json={data.config_json} onCopy={onCopy}
          testid="cursor-config-json" />
      </Step>
    </div>
  );
}

function VSCodeTab({ data, onCopy }) {
  return (
    <div data-testid="vscode-tab-content">
      <Step n={1} title="One-click install">
        <p style={styles.p}>
          VS Code 1.100+ with MCP support required.
        </p>
        <a data-testid="vscode-install-link"
          href={data.vscode}
          style={styles.installBtn}>
          Install ORA in VS Code&nbsp;↗
        </a>
        <button data-testid="vscode-copy-link"
          onClick={() => onCopy(data.vscode, "VS Code install link")}
          style={styles.linkBtn}>
          copy install link
        </button>
      </Step>
      <Step n={2} title="Or install the AUREM CTO extension">
        <p style={styles.p}>
          The AUREM CTO VS Code extension bundles the MCP config plus a
          right-click "Ship via ORA" action.
        </p>
        <a data-testid="vscode-marketplace-link"
          href="vscode:extension/auremcto.aurem-cto"
          style={styles.linkBtn}>Open in VS Code Marketplace&nbsp;↗</a>
      </Step>
      <Step n={3} title="Manual fallback">
        <p style={styles.pSmall}>
          Paste this into <code>settings.json</code> under
          <code>&nbsp;"mcp.servers"</code>:
        </p>
        <ConfigBlock json={data.config_json} onCopy={onCopy}
          testid="vscode-config-json" />
      </Step>
    </div>
  );
}

function ClaudeDesktopTab({ data, onCopy }) {
  const configStr = useMemo(
    () => JSON.stringify(data.config_json, null, 2),
    [data.config_json]
  );
  return (
    <div data-testid="claude-desktop-tab-content">
      <Step n={1} title="Open your Claude Desktop config">
        <p style={styles.p}>Config location:</p>
        <ul style={styles.list}>
          <li><strong>macOS:</strong>&nbsp;
            <code>~/Library/Application Support/Claude/claude_desktop_config.json</code>
          </li>
          <li><strong>Windows:</strong>&nbsp;
            <code>%APPDATA%\Claude\claude_desktop_config.json</code>
          </li>
        </ul>
      </Step>
      <Step n={2} title="Paste this config">
        <ConfigBlock json={data.config_json} onCopy={onCopy}
          testid="claude-desktop-config-json" />
        <button data-testid="claude-desktop-copy-config"
          onClick={() => onCopy(configStr, "Claude Desktop config")}
          style={{ ...styles.primaryBtn, marginTop: 8 }}>
          Copy full config
        </button>
      </Step>
      <Step n={3} title="Restart Claude Desktop">
        <p style={styles.p}>
          Fully quit Claude Desktop (⌘Q on Mac) and reopen. ORA will
          appear in the MCP tools tray at the bottom of the chat window.
        </p>
      </Step>
    </div>
  );
}

function ClaudeCodeTab({ data, onCopy }) {
  return (
    <div data-testid="claude-code-tab-content">
      <Step n={1} title="Install Claude Code CLI">
        <code style={styles.exampleCode}>
          npm install -g @anthropic-ai/claude-code
        </code>
        <button
          onClick={() => onCopy("npm install -g @anthropic-ai/claude-code", "Install cmd")}
          style={styles.linkBtn}>copy</button>
      </Step>
      <Step n={2} title="Add ORA as an MCP server">
        <code data-testid="claude-code-cmd" style={styles.exampleCode}>
          {data.claude_code_cli}
        </code>
        <button data-testid="claude-code-copy-cmd"
          onClick={() => onCopy(data.claude_code_cli, "Claude Code command")}
          style={styles.linkBtn}>copy</button>
      </Step>
      <Step n={3} title="Verify">
        <p style={styles.p}>Run <code>claude mcp list</code>.
          You should see <strong>ora</strong> listed with 12 tools.</p>
      </Step>
    </div>
  );
}

function Step({ n, title, children }) {
  return (
    <div style={styles.step}>
      <div style={styles.stepNum}>{n}</div>
      <div style={{ flex: 1 }}>
        <h4 style={styles.stepTitle}>{title}</h4>
        {children}
      </div>
    </div>
  );
}

function ConfigBlock({ json, onCopy, testid }) {
  const str = JSON.stringify(json, null, 2);
  return (
    <div style={{ position: "relative" }}>
      <pre data-testid={testid} style={styles.configPre}>
        <code>{str}</code>
      </pre>
      <button
        onClick={() => onCopy(str, "Config JSON")}
        style={{ ...styles.miniBtn, position: "absolute", top: 8, right: 8 }}>
        Copy
      </button>
    </div>
  );
}

/* ─── Styles ────────────────────────────────────────────────────── */

const styles = {
  wrap: {
    maxWidth: 920, margin: "0 auto", padding: "32px 24px 80px",
    color: "#e2e8f0", fontFamily: "'Inter', -apple-system, sans-serif",
  },
  header: { marginBottom: 24 },
  h1: {
    fontSize: 32, fontWeight: 700, margin: 0, color: "#f8fafc",
    letterSpacing: "-0.02em",
  },
  sub: { marginTop: 8, color: "#94a3b8", fontSize: 15, lineHeight: 1.5 },

  card: {
    background: "#0f172a", border: "1px solid #1e293b",
    borderRadius: 10, padding: 20, marginBottom: 24,
  },
  cardH: { margin: 0, fontSize: 17, fontWeight: 600, color: "#f8fafc" },
  cardSub: { marginTop: 6, marginBottom: 0, fontSize: 13, color: "#94a3b8" },
  keyRow: {
    marginTop: 16, display: "flex", alignItems: "center", gap: 8,
    background: "#020617", padding: "10px 12px", borderRadius: 6,
    border: "1px solid #1e293b",
  },
  keyCode: {
    flex: 1, fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
    color: "#5eead4", background: "transparent",
    overflowX: "auto", whiteSpace: "nowrap",
  },
  endpointLine: { marginTop: 12, fontSize: 13, color: "#94a3b8" },
  inlineCode: {
    background: "#020617", padding: "2px 6px", borderRadius: 4,
    fontSize: 12, color: "#cbd5e1",
  },

  tabRow: {
    display: "flex", gap: 8, marginBottom: 20, borderBottom: "1px solid #1e293b",
    paddingBottom: 0, overflowX: "auto",
  },
  tabBtn: {
    padding: "10px 16px", background: "transparent", border: "1px solid transparent",
    borderBottom: "2px solid transparent", borderRadius: "6px 6px 0 0",
    cursor: "pointer", fontSize: 14, fontWeight: 500, whiteSpace: "nowrap",
    transition: "background 120ms",
  },
  tabBody: { minHeight: 400 },

  step: {
    display: "flex", gap: 16, marginBottom: 24,
    padding: 16, background: "#0f172a", borderRadius: 8,
    border: "1px solid #1e293b",
  },
  stepNum: {
    width: 32, height: 32, borderRadius: "50%",
    background: "#38bdf8", color: "#0f172a",
    display: "flex", alignItems: "center", justifyContent: "center",
    fontWeight: 700, flexShrink: 0,
  },
  stepTitle: {
    margin: "6px 0 8px", fontSize: 15, fontWeight: 600, color: "#f8fafc",
  },
  p:      { color: "#cbd5e1", fontSize: 14, lineHeight: 1.5, margin: "0 0 12px" },
  pSmall: { color: "#94a3b8", fontSize: 13, lineHeight: 1.5, margin: "0 0 8px" },
  list:   { color: "#cbd5e1", fontSize: 13, lineHeight: 1.7, paddingLeft: 20 },

  configPre: {
    background: "#020617", border: "1px solid #1e293b", borderRadius: 6,
    padding: "12px 14px", margin: 0, fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace", color: "#cbd5e1",
    overflowX: "auto", maxHeight: 300,
  },
  exampleCode: {
    display: "inline-block", background: "#020617",
    border: "1px solid #1e293b", padding: "6px 12px", borderRadius: 4,
    fontFamily: "'JetBrains Mono', monospace", fontSize: 13, color: "#5eead4",
    marginRight: 8,
  },

  installBtn: {
    display: "inline-block", background: "#38bdf8", color: "#0f172a",
    padding: "10px 20px", borderRadius: 6, textDecoration: "none",
    fontWeight: 600, fontSize: 14, marginRight: 12,
  },
  primaryBtn: {
    background: "#38bdf8", color: "#0f172a", border: "none",
    padding: "10px 20px", borderRadius: 6, fontWeight: 600,
    cursor: "pointer", fontSize: 14,
  },
  secondaryBtn: {
    background: "transparent", color: "#38bdf8",
    border: "1px solid #38bdf8", padding: "8px 14px", borderRadius: 6,
    cursor: "pointer", fontSize: 13, fontWeight: 500,
  },
  linkBtn: {
    background: "transparent", color: "#38bdf8", border: "none",
    padding: "6px 8px", cursor: "pointer", fontSize: 13, textDecoration: "underline",
  },
  miniBtn: {
    background: "#1e293b", color: "#e2e8f0", border: "none",
    padding: "6px 10px", borderRadius: 4, cursor: "pointer", fontSize: 12,
  },
  miniBtnPrimary: {
    background: "#38bdf8", color: "#0f172a", border: "none",
    padding: "6px 12px", borderRadius: 4, cursor: "pointer",
    fontSize: 12, fontWeight: 600,
  },
  center: {
    minHeight: "60vh", display: "flex", flexDirection: "column",
    alignItems: "center", justifyContent: "center",
  },
  gate: {
    maxWidth: 480, margin: "80px auto", padding: 24, textAlign: "center",
    background: "#0f172a", borderRadius: 10, border: "1px solid #1e293b",
  },
  gateP: { color: "#94a3b8", fontSize: 14, margin: "12px 0 24px", lineHeight: 1.6 },
  footer: { marginTop: 32, paddingTop: 24, borderTop: "1px solid #1e293b" },
};
