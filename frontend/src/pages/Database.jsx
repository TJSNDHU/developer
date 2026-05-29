/**
 * Database.jsx — Provision a fresh MongoDB DB for a project.
 */
import React, { useState } from "react";
import { Database as DBIcon, Copy, CheckCircle2 } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api } from "../lib/api";

export default function Database() {
  const [idea, setIdea] = useState("");
  const [stack, setStack] = useState("react-fastapi");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [copied, setCopied] = useState(false);

  async function provision(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await api.post("/projects/create", {
        idea: idea.trim() || "Untitled project",
        stack_id: stack,
        provision_db: true,
        private_repo: true,
      });
      setResult(r.data);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Provision failed");
    } finally {
      setBusy(false);
    }
  }

  function copyConn() {
    if (result?.database?.connection_string) {
      navigator.clipboard.writeText(result.database.connection_string);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    }
  }

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="storage"
        title="Database"
        sub="Spin up a dedicated MongoDB database for a new project."
      />

      <div className="card" data-testid="db-provision-card" style={{ maxWidth: 720 }}>
        <form onSubmit={provision} style={{ display: "grid", gap: 14 }}>
          <label>
            <span className="label-mini">Project idea</span>
            <input data-testid="db-idea" className="input" value={idea}
                   onChange={(e) => setIdea(e.target.value)}
                   placeholder="A booking app for solo therapists" />
          </label>
          <label>
            <span className="label-mini">Stack</span>
            <select data-testid="db-stack" className="input" value={stack}
                    onChange={(e) => setStack(e.target.value)}>
              <option value="react-fastapi">react + fastapi</option>
              <option value="nextjs-node">next.js + node</option>
              <option value="vue-express">vue + express</option>
              <option value="plain-html">plain html</option>
            </select>
          </label>
          {err && (
            <div data-testid="db-error" style={{
              fontSize: 12, color: "var(--danger)",
              border: "1px solid rgba(255,107,107,0.25)",
              background: "rgba(255,107,107,0.06)",
              padding: "10px 12px", borderRadius: 4,
            }}>{err}</div>
          )}
          <button type="submit" data-testid="db-provision-btn" className="btn-primary"
                  disabled={busy} style={{ justifyContent: "center", width: 220 }}>
            <DBIcon size={14} /> {busy ? "Provisioning…" : "Provision database"}
          </button>
        </form>

        {result && (
          <div data-testid="db-result" style={{
            marginTop: 26, paddingTop: 22,
            borderTop: "1px solid var(--border)",
          }}>
            <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 12 }}>
              ✓ Project created
            </h3>
            <Row k="project id" v={result.project_id} testid="db-project-id" />
            <Row k="stack" v={result.stack} />
            <Row k="files" v={String(result.files_count ?? 0)} />
            {result.database && (
              <>
                <Row k="db name" v={result.database.db_name || "—"} testid="db-name" />
                <div style={{ marginTop: 12 }}>
                  <span className="label-mini">Connection string</span>
                  <div style={{
                    display: "flex", gap: 8, alignItems: "center",
                    background: "var(--bg-elev)",
                    border: "1px solid var(--border)",
                    borderRadius: 4, padding: "10px 12px",
                  }}>
                    <code data-testid="db-conn" style={{
                      flex: 1, fontSize: 12, color: "var(--accent-2)",
                      overflowX: "auto", whiteSpace: "nowrap",
                    }}>
                      {result.database.connection_string || "(none returned)"}
                    </code>
                    <button onClick={copyConn} className="btn-ghost"
                            data-testid="db-copy" style={{ padding: "6px 10px", fontSize: 11 }}>
                      {copied ? <CheckCircle2 size={12} /> : <Copy size={12} />}
                    </button>
                  </div>
                </div>
              </>
            )}
            {result.github?.repo_url && (
              <Row k="github" v={
                <a href={result.github.repo_url} target="_blank" rel="noreferrer" data-testid="db-github-link">
                  {result.github.repo_url}
                </a>
              } />
            )}
          </div>
        )}
      </div>
    </Shell>
  );
}

function Row({ k, v, testid }) {
  return (
    <div style={{
      display: "flex", gap: 14, alignItems: "baseline",
      padding: "6px 0", borderBottom: "1px solid var(--border)",
      fontSize: 13,
    }}>
      <span style={{
        fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
        textTransform: "uppercase", letterSpacing: "0.15em",
        color: "var(--text-faint)", width: 110,
      }}>{k}</span>
      <span data-testid={testid} style={{ color: "var(--text)", flex: 1 }}>{v}</span>
    </div>
  );
}
