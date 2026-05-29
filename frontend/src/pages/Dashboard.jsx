/**
 * Dashboard.jsx — Authenticated home, hosts the AUREM CTO chat panel.
 */
import React from "react";
import Shell, { PageHeader } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";
import { getUser } from "../lib/api";

export default function Dashboard() {
  const user = getUser();
  const name = user?.name || user?.email?.split("@")[0] || "builder";
  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="dashboard"
        title={`Welcome back, ${name}.`}
        sub="Talk to your autonomous CTO. Plan, build, debug — everything stays in this thread."
      />
      <ChatPanel />
    </Shell>
  );
}
