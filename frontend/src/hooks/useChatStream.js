/**
 * useChatStream — SSE streaming orchestration + abort handle.
 *
 * Iter 140 — wraps lib/api.js `streamChat` so the component just
 * cares about callbacks, not the AbortController plumbing. abort()
 * cancels the in-flight stream; the caller is responsible for
 * cleaning up its own message-state via useChatMessages.stopStreaming.
 */
import { useRef, useCallback } from "react";
import { streamChat } from "../lib/api";

export function useChatStream() {
  const abortRef = useRef(null);

  const stream = useCallback(async (opts) => {
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat({ ...opts, signal: controller.signal });
    } finally {
      // Clear the ref only if we're still the active controller —
      // a fresh stream() call may have replaced us mid-await.
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }, []);

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, []);

  return { stream, abort, abortRef };
}
