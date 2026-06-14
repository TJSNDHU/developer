/**
 * HostedDeployWidget.jsx — Vercel / Netlify deploy-hook bridge.
 *
 * Iter 147 — extracted out of Projects.jsx so it can be rendered on the
 * Deploy page (where it belongs alongside the SSH-based deploy form)
 * AND remain reusable inline on a project card if we ever want it
 * back.
 *
 * Props
 *   project { project_id, name }   the project this widget targets.
 */
import React, { useState, useEffect, useCallback } from "react";
import { Send } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";

export default function HostedDeployWidget({ project }) {
  const [info, setInfo]       = useState(null);
  const [busy, setBusy]       = useState(false);
  const [showCfg, setShowCfg] = useState(false);
  const [provider, setProvider] = useState("vercel");
  const [hookUrl, setHookUrl] = useState("");

  const load = useCallback(async () => {
    if (!project?.project_id) return;
    try {
      const r = await api.get(`/hosted-deploy/status/${project.project_id}`);
      setInfo(r.data);
    } catch {/* silent — first-time setup */}
  }, [project?.project_id]);

  useEffect(() => { load(); }, [load]);

  async function ship() {
    setBusy(true);
    try {
      const r = await api.post("/hosted-deploy/ship", {
        project_id: project.project_id,
      });
      toast({ message: `🚀 Deploy queued (${r.data.provider}) — check your dashboard.`, kind: "success" });
      load();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || e?.message || "Deploy failed", kind: "error" });
    } finally { setBusy(false); }
  }

  async function connect() {
    if (!hookUrl.trim()) return;
    setBusy(true);
    try {
      await api.post("/hosted-deploy/connect", {
        project_id: project.project_id,
        provider, hook_url: hookUrl.trim(),
      });
      toast({ message: `Connected to ${provider}.`, kind: "success" });
      setShowCfg(false); setHookUrl("");
      load();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Couldn't connect", kind: "error" });
    } finally { setBusy(false); }
  }

  async function disconnect() {
    if (!confirm("Remove the deploy hook for this project?")) return;
    setBusy(true);
    try {
      await api.delete(`/hosted-deploy/disconnect/${project.project_id}`);
      toast({ message: "Disconnected.", kind: "info" });
      load();
    } catch {/* silent */}
    setBusy(false);
  }

  const connected = !!info?.connected;
  const lastLabel = info?.last_deploy
    ? `Last deploy: ${new Date(info.last_deploy * 1000).toLocaleString()} · ${info.last_status || "?"}`
    : "Never deployed";

  return (
    <div className="card" data-testid="hosted-deploy-widget" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div>
          <span className="eyebrow">hosted deploy</span>
          <div style={{ fontSize: 13, marginTop: 4 }}>
            {connected ? (
              <>Connected to <strong style={{ color: "var(--accent-2)" }}>{info.provider}</strong>
                <span style={{ color: "var(--text-faint)", marginLeft: 8, fontSize: 11 }}>{lastLabel}</span>
              </>
            ) : (
              <span style={{ color: "var(--text-faint)" }}>
                Connect a Vercel or Netlify deploy hook to ship to a live URL with one click.
              </span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {connected ? (
            <>
              <button data-testid="hosted-deploy-ship" onClick={ship} disabled={busy} className="btn-primary"
                      style={{ padding: "6px 12px", fontSize: 12 }}>
                <Send size={12} /> {busy ? "Shipping…" : "Ship to Live"}
              </button>
              <button data-testid="hosted-deploy-disconnect" onClick={disconnect} disabled={busy} className="btn-ghost"
                      style={{ padding: "6px 10px", fontSize: 11 }}>
                Disconnect
              </button>
            </>
          ) : (
            <button data-testid="hosted-deploy-connect" onClick={() => setShowCfg(true)} className="btn-primary"
                    style={{ padding: "6px 12px", fontSize: 12 }}>
              Connect deploy
            </button>
          )}
        </div>
      </div>

      {showCfg && (
        <div data-testid="hosted-deploy-config" style={{ marginTop: 12, padding: 12, border: "1px solid var(--border)", borderRadius: 8 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <label style={{ fontSize: 11 }}>
              <input type="radio" name="prov" checked={provider === "vercel"} onChange={() => setProvider("vercel")} /> Vercel
            </label>
            <label style={{ fontSize: 11 }}>
              <input type="radio" name="prov" checked={provider === "netlify"} onChange={() => setProvider("netlify")} /> Netlify
            </label>
          </div>
          <input
            data-testid="hosted-deploy-hook-input"
            className="input"
            value={hookUrl}
            onChange={(e) => setHookUrl(e.target.value)}
            placeholder={provider === "vercel"
              ? "https://api.vercel.com/v1/integrations/deploy/prj_.../..."
              : "https://api.netlify.com/build_hooks/..."}
            style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}
          />
          <p style={{ fontSize: 10, color: "var(--text-faint)", margin: "6px 0 8px" }}>
            {provider === "vercel"
              ? "Vercel → Project → Settings → Git → Deploy Hooks → Create"
              : "Netlify → Site → Build & deploy → Build hooks → Add build hook"}
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button data-testid="hosted-deploy-connect-save" onClick={connect} disabled={busy || !hookUrl.trim()}
                    className="btn-primary" style={{ padding: "5px 12px", fontSize: 11 }}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button onClick={() => { setShowCfg(false); setHookUrl(""); }} className="btn-ghost"
                    style={{ padding: "5px 10px", fontSize: 11 }}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
