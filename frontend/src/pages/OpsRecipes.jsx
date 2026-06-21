/**
 * OpsRecipes.jsx — `/admin/ops` — Copy-paste runbooks for server ops
 * that AUREM can't (and shouldn't) execute remotely on user infra.
 *
 * Triggered when ORA classifies a message as "operational, not codebase"
 * (e.g. "restart supervisor", "free disk space", "check logs"). Instead
 * of dumping raw bash in chat, ORA links here.
 */
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Copy, ExternalLink, Terminal } from "lucide-react";

const RECIPES = [
  {
    id: "supervisor-restart",
    title: "Restart supervisor (services aren't running)",
    when: "Backend/frontend showing 502 · supervisor process died · after deploy",
    steps: [
      { cmd: "sudo systemctl status supervisor", note: "First check if supervisor itself is alive" },
      { cmd: "sudo systemctl restart supervisor", note: "Restart the whole daemon" },
      { cmd: "sudo supervisorctl status", note: "Confirm all services show RUNNING" },
      { cmd: "sudo supervisorctl restart backend frontend", note: "If supervisor is alive but services crashed" },
      { cmd: "sudo tail -n 100 /var/log/supervisor/supervisord.log", note: "Read here if restart fails" },
    ],
    escalate: "If `systemctl status` shows 'Failed' for 3+ consecutive restarts, email polarisbuiltinc@gmail.com with the last 100 lines of /var/log/supervisor/supervisord.log",
  },
  {
    id: "service-logs",
    title: "Read service logs (something is broken)",
    when: "API returns 500 · frontend blank · need to know WHY",
    steps: [
      { cmd: "sudo tail -n 200 /var/log/supervisor/backend.err.log", note: "Most recent backend crashes/tracebacks" },
      { cmd: "sudo tail -n 200 /var/log/supervisor/backend.out.log", note: "Backend stdout (uvicorn access log)" },
      { cmd: "sudo tail -n 100 /var/log/supervisor/frontend.err.log", note: "Vite/build errors" },
      { cmd: "sudo journalctl -u supervisor -n 100 --no-pager", note: "Systemd-level supervisor errors" },
    ],
    escalate: "Look for the FIRST traceback (not last). Search the error string on /admin/brain/<project>/replay — ORA often knows the fix.",
  },
  {
    id: "disk-full",
    title: "Disk full / 'No space left on device'",
    when: "Deploys failing · MongoDB writes erroring · Docker pull hangs",
    steps: [
      { cmd: "df -h", note: "Confirm which mount is full" },
      { cmd: "sudo du -sh /var/log/supervisor/* | sort -h | tail -10", note: "Largest log files" },
      { cmd: "sudo truncate -s 0 /var/log/supervisor/*.log", note: "⚠ Truncates ALL supervisor logs — don't run if you need them for debugging" },
      { cmd: "docker system prune -af --volumes", note: "Reclaim Docker disk (only if Docker is running)" },
      { cmd: "sudo apt clean", note: "Clear apt cache" },
    ],
    escalate: "If still >90% full after these, your VPS needs a bigger disk OR you have a runaway log writer. Contact support.",
  },
  {
    id: "mongo-connection",
    title: "MongoDB connection refused / timeout",
    when: "Backend logs show 'ServerSelectionTimeoutError' · API hangs · health endpoint reports db: false",
    steps: [
      { cmd: "sudo systemctl status mongod", note: "Is the daemon running?" },
      { cmd: "sudo systemctl restart mongod", note: "Cold restart" },
      { cmd: "mongosh --eval 'db.serverStatus().connections'", note: "Verify it accepts connections" },
      { cmd: "grep MONGO_URL /app/backend/.env", note: "Confirm backend has the right URL" },
      { cmd: "sudo tail -n 100 /var/log/mongodb/mongod.log", note: "Look for OOM / config errors" },
    ],
    escalate: "If MongoDB won't start: `sudo dmesg | tail -50` — OOM kills show here. Likely needs more RAM.",
  },
  {
    id: "deploy-stuck",
    title: "Production deploy stuck / showing old build",
    when: "Pushed code but auremcto.com still shows old UI · build hash unchanged in /admin",
    steps: [
      { cmd: "# Open /admin in browser → check 'build XXXXXXX' pill at top", note: "If hash matches latest commit → it's a browser cache issue" },
      { cmd: "# In /admin → click '🧹 Purge & hard-refresh'", note: "Iter 63 button — purges Cloudflare + Mongo + LRU + this browser" },
      { cmd: "curl -sI https://auremcto.com/assets/index-*.js | grep -i cache", note: "Check Cloudflare cache headers" },
      { cmd: "# Cloudflare dashboard → Caching → Purge Everything", note: "Nuclear option if you own the CF zone" },
    ],
    escalate: "If hash in /admin still doesn't match git HEAD after redeploy → deploy pipeline failed silently. Check Emergent dashboard deploy logs.",
  },
];


export default function OpsRecipes() {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(null);
  function copyCmd(cmd, key) {
    navigator.clipboard?.writeText(cmd);
    setCopied(key);
    setTimeout(() => setCopied(null), 1200);
  }
  return (
    <div style={{ padding: "24px 32px", maxWidth: 920, margin: "0 auto",
                  minHeight: "100vh", overflow: "auto" }}>
      <button
        data-testid="ops-back"
        onClick={() => navigate("/admin")}
        className="btn-ghost"
        style={{ marginBottom: 16, padding: "5px 10px", fontSize: 11 }}
      >
        <ArrowLeft size={11} /> back to admin
      </button>
      <h1 style={{ fontSize: 22, fontWeight: 500, color: "var(--text)",
                    margin: "0 0 6px" }}>
        Ops recipes
      </h1>
      <p style={{ fontSize: 12, color: "var(--text-dim)",
                   margin: "0 0 24px", maxWidth: 600 }}>
        Server-side operations AUREM <em>can't</em> run on your infra
        (we don't have SSH to your boxes). Copy-paste runbooks below for
        the most common issues. ORA links here when you ask things like
        "restart supervisor" or "free disk space".
      </p>
      {RECIPES.map((r) => (
        <section key={r.id} data-testid={`ops-recipe-${r.id}`}
                 style={{ marginBottom: 28, padding: "16px 18px",
                          background: "var(--panel)",
                          border: "1px solid var(--border)",
                          borderRadius: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8,
                         marginBottom: 4 }}>
            <Terminal size={13} style={{ color: "var(--accent-2)" }} />
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 500,
                          color: "var(--text)" }}>{r.title}</h3>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-faint)",
                         fontStyle: "italic", marginBottom: 12 }}>
            When: {r.when}
          </div>
          {r.steps.map((s, i) => {
            const key = `${r.id}-${i}`;
            return (
              <div key={i} style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", alignItems: "center",
                               gap: 8, background: "var(--bg-elev)",
                               border: "1px solid var(--border)",
                               borderRadius: 4, padding: "6px 10px",
                               fontFamily: "'JetBrains Mono', monospace",
                               fontSize: 12, color: "var(--text)" }}>
                  <span style={{ flex: 1, overflowWrap: "anywhere" }}>
                    {s.cmd}
                  </span>
                  <button
                    data-testid={`ops-copy-${key}`}
                    onClick={() => copyCmd(s.cmd, key)}
                    className="btn-ghost"
                    style={{ padding: "3px 7px", fontSize: 10,
                              color: copied === key
                                ? "var(--ok)" : "var(--text-faint)" }}
                    title="Copy"
                  >
                    <Copy size={10} />
                    {copied === key ? " ✓" : ""}
                  </button>
                </div>
                {s.note && (
                  <div style={{ fontSize: 10, color: "var(--text-faint)",
                                 padding: "3px 6px 0" }}>
                    {s.note}
                  </div>
                )}
              </div>
            );
          })}
          {r.escalate && (
            <div style={{ marginTop: 10, padding: "8px 10px",
                           background: "var(--accent-soft)",
                           border: "1px solid var(--border-strong)",
                           borderRadius: 4, fontSize: 11,
                           color: "var(--text-dim)" }}>
              <ExternalLink size={10} style={{ marginRight: 6,
                color: "var(--accent-2)" }} />
              <strong style={{ color: "var(--accent-2)" }}>Escalate:</strong>
              {" "}{r.escalate}
            </div>
          )}
        </section>
      ))}
      <div style={{ fontSize: 11, color: "var(--text-faint)",
                     textAlign: "center", padding: 16 }}>
        Missing a recipe? Email polarisbuiltinc@gmail.com — we'll add it.
      </div>
    </div>
  );
}
