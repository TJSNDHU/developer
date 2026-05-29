/**
 * Domain.jsx — Custom domain configuration.
 */
import React, { useEffect, useState } from "react";
import { Globe, ShieldCheck } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api } from "../lib/api";

export default function Domain() {
  const [domain, setDomain] = useState("");
  const [registrar, setRegistrar] = useState("cloudflare");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [verify, setVerify] = useState(null);

  useEffect(() => {
    api.get("/domain/config").then((r) => {
      if (r.data?.config) {
        setDomain(r.data.config.domain || "");
        setRegistrar(r.data.config.registrar || "cloudflare");
      }
    }).catch(() => {});
  }, []);

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setStatus(null);
    try {
      await api.post("/domain/config", { domain, registrar });
      setStatus({ ok: true, msg: "Domain configuration saved." });
    } catch (err) {
      setStatus({ ok: false, msg: err?.response?.data?.detail || "Save failed" });
    } finally {
      setBusy(false);
    }
  }

  async function checkVerify() {
    if (!domain) return;
    setVerify("checking");
    try {
      const r = await api.get(`/domain/verification/${encodeURIComponent(domain)}`);
      setVerify(r.data);
    } catch (err) {
      setVerify({ ok: false, error: err?.response?.data?.detail || "Verify failed" });
    }
  }

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="custom domain"
        title="Domain"
        sub="Point a domain at your app and verify DNS records."
      />

      <form onSubmit={save} className="card" data-testid="domain-form" style={{ display: "grid", gap: 14, maxWidth: 560 }}>
        <label>
          <span className="label-mini">Domain</span>
          <input data-testid="domain-input" className="input" value={domain}
                 onChange={(e) => setDomain(e.target.value)}
                 placeholder="app.example.com" />
        </label>
        <label>
          <span className="label-mini">Registrar</span>
          <select data-testid="domain-registrar" className="input" value={registrar}
                  onChange={(e) => setRegistrar(e.target.value)}>
            <option value="cloudflare">cloudflare</option>
            <option value="namecheap">namecheap</option>
            <option value="route53">route53</option>
            <option value="other">other</option>
          </select>
        </label>
        {status && (
          <div data-testid="domain-status" style={{
            fontSize: 12, padding: "10px 12px", borderRadius: 4,
            color: status.ok ? "var(--ok)" : "var(--danger)",
            border: `1px solid ${status.ok ? "rgba(109,212,161,0.2)" : "rgba(255,107,107,0.2)"}`,
            background: status.ok ? "rgba(109,212,161,0.06)" : "rgba(255,107,107,0.06)",
          }}>{status.msg}</div>
        )}
        <div style={{ display: "flex", gap: 10 }}>
          <button type="submit" data-testid="domain-save" className="btn-primary"
                  disabled={busy}><Globe size={14} /> {busy ? "Saving…" : "Save"}</button>
          <button type="button" data-testid="domain-verify" className="btn-ghost"
                  onClick={checkVerify} disabled={!domain}>
            <ShieldCheck size={14} /> Verify DNS
          </button>
        </div>
        {verify && verify !== "checking" && (
          <pre data-testid="domain-verify-result" style={{
            fontSize: 11, color: "var(--text-dim)",
            background: "var(--bg-elev)", padding: 12, borderRadius: 4,
            overflowX: "auto",
          }}>{JSON.stringify(verify, null, 2)}</pre>
        )}
        {verify === "checking" && (
          <p data-testid="domain-verify-result" style={{ fontSize: 12, color: "var(--text-dim)" }}>Checking DNS…</p>
        )}
      </form>
    </Shell>
  );
}
