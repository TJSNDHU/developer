/**
 * Dashboard.jsx — Authenticated home, hosts the AUREM CTO chat panel.
 * Pulls active session_id from Shell context. Refreshes sidebar after each turn.
 */
import React from "react";
import Shell, { PageHeader, useChatSession } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";
import { getUser } from "../lib/api";

export default function Dashboard() {
  return (
    <Shell requireAuth>
      <DashboardBody />
    </Shell>
  );
}

function DashboardBody() {
  const { sessionId, refreshSessions } = useChatSession();
  const user = getUser();
  const name = user?.name || user?.email?.split("@")[0] || "builder";
  return (
    <>
      <PageHeader
        eyebrow="dashboard"
        title={`Welcome back, ${name}.`}
        sub="Talk to your autonomous CTO. Every reply is streamed and saved — your sessions live in the sidebar."
      />
      <ChatPanel sessionId={sessionId} onTurnSaved={refreshSessions} />
    </>
  );
}
