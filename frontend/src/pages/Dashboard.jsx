/**
 * Dashboard.jsx — Authenticated home, hosts the AUREM CTO chat panel.
 * Top: Emergent-style tab bar (Home + project tabs). The active project's
 * context flows into ChatPanel so the user works on one repo at a time.
 */
import React from "react";
import { FolderGit2, ExternalLink, Send } from "lucide-react";
import { useNavigate } from "react-router-dom";
import Shell, { useChatSession } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";
import TabBar, { useActiveProject } from "../components/TabBar";

export default function Dashboard() {
  return (
    <Shell requireAuth>
      <DashboardBody />
    </Shell>
  );
}

function DashboardBody() {
  const { sessionId, refreshSessions } = useChatSession();
  const project = useActiveProject();
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <TabBar />
      {project && <ActiveProjectBar project={project} />}
      <div style={{ flex: 1, minHeight: 0 }}>
        <ChatPanel
          sessionId={sessionId}
          onTurnSaved={refreshSessions}
          activeProject={project}
        />
      </div>
    </div>
  );
}

function ActiveProjectBar({ project }) {
  const navigate = useNavigate();
  return (
    <div
      data-testid="active-project-bar"
      style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "8px 18px",
        background: "var(--accent-soft)",
        borderBottom: "1px solid var(--accent)",
        fontSize: 12,
      }}
    >
      <FolderGit2 size={13} style={{ color: "var(--accent-2)" }} />
      <span style={{ color: "var(--accent-2)", fontWeight: 600 }}>{project.name}</span>
      <a
        href={project.github_url} target="_blank" rel="noreferrer"
        style={{
          fontFamily: "'JetBrains Mono', monospace",
          color: "var(--text-dim)", display: "inline-flex",
          alignItems: "center", gap: 4,
        }}
      >
        {project.github_owner}/{project.github_repo}@{project.branch}
        <ExternalLink size={10} />
      </a>
      <span style={{ flex: 1 }} />
      <button
        data-testid="active-project-open"
        onClick={() => navigate("/projects")}
        style={{
          background: "transparent", border: "1px solid var(--border-strong)",
          color: "var(--accent-2)", borderRadius: 4,
          padding: "4px 10px", fontSize: 11, cursor: "pointer",
          display: "inline-flex", alignItems: "center", gap: 4,
        }}
      >
        <Send size={10} /> Run task on this repo
      </button>
    </div>
  );
}
