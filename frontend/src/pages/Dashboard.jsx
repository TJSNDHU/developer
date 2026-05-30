/**
 * Dashboard.jsx — Authenticated home, hosts the AUREM CTO chat panel.
 * Top: Emergent-style tab bar (Home + project tabs). The active project's
 * context flows into ChatPanel so the user works on one repo at a time.
 */
import React from "react";
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
