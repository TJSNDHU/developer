/**
 * Dashboard.jsx — Authenticated home, hosts the AUREM CTO chat panel.
 * Header removed per request — chat panel fills the viewport top to bottom.
 */
import React from "react";
import Shell, { useChatSession } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";

export default function Dashboard() {
  return (
    <Shell requireAuth>
      <DashboardBody />
    </Shell>
  );
}

function DashboardBody() {
  const { sessionId, refreshSessions } = useChatSession();
  return <ChatPanel sessionId={sessionId} onTurnSaved={refreshSessions} />;
}
