/**
 * FloatingORAButton — fixed bottom-right floating action button that
 * opens the ORASidePanel. Mounted once from Shell.jsx so the panel is
 * available across every authenticated route.
 */
import React from "react";
import { useORAPanel } from "../hooks/useORAPanel";
import ORASidePanel from "./ORASidePanel";

export default function FloatingORAButton() {
  const {
    open, messages, busy, projectId,
    openPanel, closePanel, sendMessage, stopStream,
  } = useORAPanel();

  return (
    <>
      {!open && (
        <button
          data-testid="floating-ora-btn"
          onClick={openPanel}
          title="Ask ORA"
          style={{
            position: "fixed",
            // Sits above the composer's Send button so the two don't
            // overlap visually on dashboard routes.
            bottom: 92,
            right: 24,
            width: 52,
            height: 52,
            borderRadius: "50%",
            background: "linear-gradient(135deg, #f59e0b, #d97706)",
            border: "none",
            cursor: "pointer",
            zIndex: 7999,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 4px 20px rgba(245,158,11,0.4)",
            fontSize: 20,
            fontWeight: 800,
            color: "#000",
            transition: "transform 0.2s, box-shadow 0.2s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = "scale(1.08)";
            e.currentTarget.style.boxShadow = "0 6px 28px rgba(245,158,11,0.55)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = "scale(1)";
            e.currentTarget.style.boxShadow = "0 4px 20px rgba(245,158,11,0.4)";
          }}
        >
          O
        </button>
      )}

      <ORASidePanel
        open={open}
        messages={messages}
        busy={busy}
        projectId={projectId}
        onClose={closePanel}
        onSend={sendMessage}
        onStop={stopStream}
      />
    </>
  );
}
