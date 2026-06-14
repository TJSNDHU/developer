/**
 * FloatingORAButton — Iter 152.
 * The actual launch button now lives in Dashboard.jsx's top-right bar
 * (next to the Preview toggle) so users find it alongside the other
 * page-level controls instead of floating over the composer.
 *
 * This component is the global owner of the panel state — it stays
 * mounted from Shell.jsx and listens for the `aurem:ora-open` window
 * event dispatched by the Dashboard launch button. That keeps the
 * ORA panel available across every authenticated route while letting
 * each page place its own trigger wherever it makes sense.
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

  // Listen for the launch event from page-level triggers.
  useEffect(() => {
    const onOpen = () => openPanel();
    window.addEventListener("aurem:ora-open", onOpen);
    return () => window.removeEventListener("aurem:ora-open", onOpen);
  }, [openPanel]);

  // ORA is desktop-only; on mobile we don't render the panel at all.
  if (!isDesktop) return null;

  return (
    <ORASidePanel
      open={open}
      messages={messages}
      busy={busy}
      projectId={projectId}
      onClose={closePanel}
      onSend={sendMessage}
      onStop={stopStream}
    />
  );
}
