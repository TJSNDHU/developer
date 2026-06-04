/**
 * Dashboard.jsx — Authenticated home, hosts the AUREM CTO chat panel.
 * Top: Emergent-style tab bar (Home + project tabs). The active project's
 * context flows into ChatPanel so the user works on one repo at a time.
 *
 * Iter 73 Task 3: Renders <NewUserWizard /> over the dashboard when the
 * authenticated user has 0 connected projects AND hasn't dismissed it.
 */
import React, { useEffect, useState } from "react";
import Shell, { useChatSession } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";
import TabBar, { useActiveProject } from "../components/TabBar";
import NewUserWizard, { isWizardDismissed } from "../components/NewUserWizard";
import { api } from "../lib/api";

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
  const [showWizard, setShowWizard] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (isWizardDismissed()) return;
    api.get("/cto/projects/list")
      .then((r) => {
        if (cancelled) return;
        const count = (r.data?.projects || []).length;
        if (count === 0) setShowWizard(true);
      })
      .catch(() => { /* ignore — auth/network noise */ });
    return () => { cancelled = true; };
  }, []);

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
      {showWizard && (
        <NewUserWizard onComplete={() => setShowWizard(false)} />
      )}
    </div>
  );
}
