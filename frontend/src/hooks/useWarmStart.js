/**
 * hooks/useWarmStart.js — Iter 165
 *
 * Triggers the backend `warm-start` job when a project is selected and
 * polls the status endpoint until the 4 background agents report ready.
 * Frontend consumers (ChatPanel) use `{ status, progress }` to render a
 * thin status bar — the actual context payload lives server-side in
 * Mongo and is injected into the next chat turn automatically by the
 * orchestrator.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_TICKS = 40; // 60s cap — agents are 8s each, parallel

export function useWarmStart(projectId) {
  const [status, setStatus] = useState("idle");
  const [progress, setProgress] = useState(0);
  const pollRef = useRef(null);
  const jobRef = useRef(null);
  const ticksRef = useRef(0);

  useEffect(() => {
    // Reset on project change / unmount
    const cleanup = () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      ticksRef.current = 0;
      jobRef.current = null;
    };

    if (!projectId || projectId === "home") {
      cleanup();
      setStatus("idle");
      setProgress(0);
      return cleanup;
    }

    cleanup();
    setStatus("warming");
    setProgress(0);

    api
      .post(`/cto/projects/${projectId}/warm-start`)
      .then((r) => {
        const data = r?.data || {};
        // No GitHub connection → server returned no job_id; stay idle
        if (!data.job_id) {
          setStatus("idle");
          setProgress(0);
          return;
        }
        jobRef.current = data.job_id;

        pollRef.current = setInterval(async () => {
          ticksRef.current += 1;
          if (ticksRef.current > MAX_POLL_TICKS) {
            cleanup();
            setStatus("idle");
            return;
          }
          try {
            const s = await api.get(
              `/cto/projects/warm-start/${jobRef.current}/status`
            );
            const sd = s?.data || {};
            setProgress(typeof sd.progress === "number" ? sd.progress : 0);
            if (sd.ready) {
              // Iter 212m-15 — when the job finishes mark progress at
              // 100% explicitly then transition to "ready" on the next
              // tick so the bar visually fills before WarmStatusBar
              // unmounts (was previously snapping out at 80% if the
              // last agent's $addToSet hadn't been read yet).
              setProgress(1);
              if (pollRef.current) {
                clearInterval(pollRef.current);
                pollRef.current = null;
              }
              ticksRef.current = 0;
              jobRef.current = null;
              setTimeout(() => setStatus("ready"), 250);
            } else if (sd.status === "failed") {
              cleanup();
              setStatus("idle");
            }
          } catch {
            cleanup();
            setStatus("idle");
          }
        }, POLL_INTERVAL_MS);
      })
      .catch(() => {
        setStatus("idle");
        setProgress(0);
      });

    return cleanup;
  }, [projectId]);

  return { status, progress };
}
