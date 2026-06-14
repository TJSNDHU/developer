/**
 * FloatingORAButton — fixed bottom-right floating button that opens
 * the ORASidePanel.
 *
 * Iter 150: desktop-only. The button (and therefore the panel) does
 * not mount on viewports ≤ 900px to keep the mobile experience focused
 * on the primary chat composer.
 */
import React, { useEffect, useState } from "react";
import { useORAPanel } from "../hooks/useORAPanel";
import ORASidePanel from "./ORASidePanel";

function useIsDesktop() {
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.matchMedia("(min-width: 901px)").matches;
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(min-width: 901px)");
    const onChange = (e) => setIsDesktop(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return isDesktop;
}

export default function FloatingORAButton() {
  const isDesktop = useIsDesktop();
  const {
    open, messages, busy, projectId,
    openPanel, closePanel, sendMessage, stopStream,
  } = useORAPanel();

  // Mobile users get the standard chat composer — no ORA FAB needed.
  if (!isDesktop) return null;

  return (
    <>
      {!open && (
        <button
          data-testid="floating-ora-btn"
          onClick={openPanel}
          title="Ask ORA"
          style={{
            position: "fixed",
            // Sits well above the composer's Send button on /dashboard.
            bottom: 92,
            right: 24,
            width: 52, height: 52,
            borderRadius: "50%",
            background:
              "radial-gradient(circle at 30% 30%, var(--accent-2), var(--accent))",
            border: "1px solid var(--accent)",
            cursor: "pointer",
            zIndex: 7999,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow:
              "0 6px 22px rgba(255, 138, 42, 0.42), 0 0 0 1px rgba(255,200,120,0.18) inset",
            fontSize: 18,
            fontWeight: 800,
            color: "#1a0f00",
            fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.05em",
            transition: "transform 180ms ease, box-shadow 180ms ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = "scale(1.08)";
            e.currentTarget.style.boxShadow =
              "0 8px 30px rgba(255, 138, 42, 0.58), 0 0 0 1px rgba(255,200,120,0.28) inset";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = "scale(1)";
            e.currentTarget.style.boxShadow =
              "0 6px 22px rgba(255, 138, 42, 0.42), 0 0 0 1px rgba(255,200,120,0.18) inset";
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
