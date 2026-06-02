/**
 * pages/ShipWall.jsx — Public "Ship Wall"
 * Every task AUREM ships becomes a public card. No login needed.
 * Route: /wall
 */
import React, { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import usePageMeta from "../lib/usePageMeta";

const APP_URL = window.location.origin;

export default function ShipWall() {
  usePageMeta({
    title: "Ship Wall · AUREM — Real code shipped by real developers",
    description: "Every task AUREM CTO ships appears here in real time.",
    canonical: `${APP_URL}/wall`,
  });

  const [ships, setShips]     = useState([]);
  const [stats, setStats]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [sharing, setSharing] = useState(null);

  const load = useCallback(async () => {
    try {
      const [feedRes, statsRes] = await Promise.all([
        api.get("/wall/feed?limit=50"),
        api.get("/wall/stats"),
      ]);
      setShips(feedRes.data.ships || []);
      setStats(statsRes.data);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)", paddingBottom: 60 }}>
      <div style={{
        borderBottom: "1px solid var(--border)",
        padding: "20px 24px 18px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexWrap: "wrap", gap: 12,
      }}>
        <div>
          <Link to="/" style={{ textDecoration: "none" }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-dim)" }}>AUREM CTO</span>
          </Link>
          <h1 style={{ fontSize: 22, fontWeight: 500, marginTop: 4, color: "var(--text)" }}>Ship Wall</h1>
          <p style={{ fontSize: 13, color: "var(--text-dim)", marginTop: 2 }}>
            Real code. Real commits. Shipped by real developers with ORA.
          </p>
        </div>
        {stats && (
          <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
            <StatNum label="tasks shipped" value={stats.total_ships} />
            <StatNum label="developers"    value={stats.total_devs}  />
            <StatNum label="repos"         value={stats.total_repos} />
          </div>
        )}
      </div>

      <div style={{ maxWidth: 680, margin: "0 auto", padding: "24px 16px 0" }}>
        {loading && (
          <div style={{ textAlign: "center", color: "var(--text-dim)", padding: 40 }}>
            Loading ships…
          </div>
        )}
        {!loading && ships.length === 0 && (
          <div style={{ textAlign: "center", padding: "60px 20px", color: "var(--text-dim)", fontSize: 14 }}>
            No ships yet. Be the first.{" "}
            <Link to="/signup" style={{ color: "var(--accent)" }}>Start shipping →</Link>
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {ships.map((ship) => (
            <ShipCard
              key={ship.task_id}
              ship={ship}
              sharing={sharing === ship.task_id}
              onShare={() => setSharing(sharing === ship.task_id ? null : ship.task_id)}
            />
          ))}
        </div>
        {ships.length > 0 && (
          <div style={{ textAlign: "center", marginTop: 32, fontSize: 13, color: "var(--text-dim)" }}>
            Showing {ships.length} most recent ships.{" "}
            <Link to="/signup" style={{ color: "var(--accent)" }}>Join to appear here →</Link>
          </div>
        )}
      </div>
    </div>
  );
}

function StatNum({ label, value }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 22, fontWeight: 500, color: "var(--text)" }}>
        {(value || 0).toLocaleString()}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>{label}</div>
    </div>
  );
}

function ShipCard({ ship, sharing, onShare }) {
  const timeAgo = useTimeAgo(ship.shipped_at);
  const dev = ship.developer || {};
  const tweetText = encodeURIComponent(
    `Just shipped to ${ship.repo} with @AUREMcto\n\n${ship.summary}\n\n${ship.commit_url || ""}\n\n#AUREM #ShipWithAI`
  );
  const tweetUrl = `https://twitter.com/intent/tweet?text=${tweetText}`;

  return (
    <div style={{
      background: "rgba(20,20,28,0.55)",
      backdropFilter: "blur(10px)",
      border: "1px solid rgba(255,255,255,0.07)",
      borderRadius: 10, padding: "14px 16px",
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <Avatar name={dev.name} avatar={dev.avatar} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text)" }}>{dev.name}</span>
            {dev.handle && <span style={{ fontSize: 11, color: "var(--text-dim)" }}>@{dev.handle}</span>}
            {ship.maxx_mode && (
              <span style={{
                fontSize: 10, fontWeight: 500, padding: "2px 8px", borderRadius: 10,
                background: "rgba(127,119,221,0.18)", color: "#a59ff0",
              }}>Maxx</span>
            )}
            <span style={{ fontSize: 11, color: "var(--text-dim)", marginLeft: "auto" }}>{timeAgo}</span>
          </div>
          <p style={{
            fontSize: 13, color: "var(--text)", margin: "6px 0 0", lineHeight: 1.4,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>
            {ship.summary || "Shipped a task"}
          </p>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <span style={{
          fontSize: 11, color: "var(--text-dim)",
          background: "rgba(255,255,255,0.05)", padding: "3px 8px",
          borderRadius: 6, fontFamily: "monospace",
        }}>
          {ship.repo}
        </span>
        {ship.commit_sha && (
          <a href={ship.commit_url} target="_blank" rel="noopener noreferrer"
            style={{ fontSize: 11, color: "#7F77DD", fontFamily: "monospace", textDecoration: "none" }}>
            {ship.commit_sha}
          </a>
        )}
        <div style={{ marginLeft: "auto" }}>
          <button onClick={onShare} style={{
            fontSize: 11, padding: "3px 10px", cursor: "pointer",
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: 6, color: "var(--text-dim)",
          }}>
            Share
          </button>
        </div>
      </div>

      {sharing && (
        <div style={{
          marginTop: 10, padding: "10px 12px", borderRadius: 8,
          background: "rgba(255,255,255,0.04)",
          border: "1px solid rgba(255,255,255,0.08)",
        }}>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 8 }}>Share this ship</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a href={tweetUrl} target="_blank" rel="noopener noreferrer"
              style={{ fontSize: 11, padding: "5px 14px", background: "#000", color: "#fff", borderRadius: 6, textDecoration: "none" }}>
              Post on X
            </a>
            <button onClick={() => navigator.clipboard.writeText(ship.share_url)}
              style={{
                fontSize: 11, padding: "5px 14px", cursor: "pointer", borderRadius: 6,
                background: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.12)",
                color: "var(--text-dim)",
              }}>
              Copy link
            </button>
          </div>
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 4 }}>Add to your README:</div>
            <code style={{
              display: "block", fontSize: 10, wordBreak: "break-all",
              background: "rgba(0,0,0,0.3)", padding: "6px 8px", borderRadius: 5, color: "#a59ff0",
            }}>
              {`[![Built with AUREM](${APP_URL}/api/aurem-dev/wall/badge/${dev.handle})](${ship.share_url})`}
            </code>
          </div>
        </div>
      )}
    </div>
  );
}

function Avatar({ name, avatar }) {
  const initials = (name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  if (avatar) return <img src={avatar} alt={name} style={{ width: 32, height: 32, borderRadius: "50%", flexShrink: 0 }} />;
  return (
    <div style={{
      width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
      background: "rgba(127,119,221,0.2)", color: "#a59ff0",
      fontSize: 11, fontWeight: 500, display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      {initials}
    </div>
  );
}

function useTimeAgo(ts) {
  const [label, setLabel] = useState("");
  useEffect(() => {
    function compute() {
      if (!ts) return setLabel("just now");
      const diff = Math.floor(Date.now() / 1000 - ts);
      if (diff < 60)    return setLabel(`${diff}s ago`);
      if (diff < 3600)  return setLabel(`${Math.floor(diff / 60)}m ago`);
      if (diff < 86400) return setLabel(`${Math.floor(diff / 3600)}h ago`);
      return setLabel(`${Math.floor(diff / 86400)}d ago`);
    }
    compute();
    const t = setInterval(compute, 30_000);
    return () => clearInterval(t);
  }, [ts]);
  return label;
}
