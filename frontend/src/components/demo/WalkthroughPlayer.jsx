/**
 * components/demo/WalkthroughPlayer.jsx — Iter 212m-200
 *
 * Reusable CSS-animated walkthrough player. Renders a stack of
 * schematic mock-UI "scenes" in a browser-chrome frame and auto-
 * advances through them with a progress bar, captions, and a
 * play/pause + restart control.  Used by both the /demo route
 * (anonymous full walkthrough) and the homepage embed section.
 *
 * Props
 *   steps    Array<{ id, caption, duration, render(state) }>
 *            Each `render` returns JSX for the frame content.
 *   mode     "full" | "teaser"   (default "full")
 *   loop     boolean             (default true)
 *   compact  boolean             render smaller for landing embed
 *
 * All content is fabricated (no real PATs, no real repo access).
 */
import React, { useEffect, useRef, useState } from "react";

export default function WalkthroughPlayer({
  steps,
  mode = "full",
  loop = true,
  compact = false,
  audioEnabled = true,           // Iter 212m-231 — voiceover toggle
  audioBaseUrl = "/demo-audio",  // where step-{n}.mp3 files live
}) {
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [tick, setTick] = useState(0);   // 0..1 progress within step
  const [muted, setMuted] = useState(false);
  const rafRef = useRef(null);
  const startedAtRef = useRef(null);
  const audioRef = useRef(null);        // Iter 212m-231 — <audio> element handle

  const active = steps[idx];
  const duration = Math.max(1000, active?.duration ?? 8000);

  // ── Audio sync ────────────────────────────────────────────────
  // Iter 212m-231 — When the active step changes (or user restarts /
  // jumps), swap the <audio> src to the matching MP3 and play/pause
  // in lockstep with the visual player.  Every step is expected to
  // have `audioSrc: "step-N.mp3"` in its config (or `${audioBaseUrl}/
  // ${id}.mp3` as fallback).  If the file is missing (404), we
  // gracefully skip audio for that step — visuals never wait for it.
  useEffect(() => {
    if (!audioEnabled) return;
    const a = audioRef.current;
    if (!a) return;
    const src = active?.audioSrc
      || (active?.id ? `${audioBaseUrl}/${active.id}.mp3` : null);
    if (!src) return;
    if (a.src.endsWith(src) === false) {
      a.src = src;
      a.load();
    }
    if (playing && !muted) {
      // Autoplay may reject on first paint; that's fine — the caption
      // is visible and the visual pipeline keeps running.
      a.currentTime = 0;
      a.play().catch(() => { /* browser autoplay policy — silent skip */ });
    } else {
      a.pause();
    }
  }, [idx, playing, muted, audioEnabled, active, audioBaseUrl]);

  // ── autoplay loop ─────────────────────────────────────────────
  useEffect(() => {
    if (!playing) return;
    startedAtRef.current = performance.now();
    const step = () => {
      const elapsed = performance.now() - startedAtRef.current;
      const t = Math.min(1, elapsed / duration);
      setTick(t);
      if (t >= 1) {
        if (idx + 1 < steps.length) {
          setIdx(idx + 1);
        } else if (loop) {
          setIdx(0);
        } else {
          setPlaying(false);
        }
      } else {
        rafRef.current = requestAnimationFrame(step);
      }
    };
    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [idx, playing, duration, steps.length, loop]);

  const jump = (i) => {
    setIdx(Math.max(0, Math.min(steps.length - 1, i)));
    setTick(0);
    setPlaying(true);
  };

  const restart = () => {
    setIdx(0);
    setTick(0);
    setPlaying(true);
  };

  const framePad  = compact ? 12 : 18;
  const frameH    = compact ? 380 : 520;
  const captionSz = compact ? 12 : 14;

  return (
    <div
      data-testid={`walkthrough-player-${mode}`}
      style={{
        width: "100%",
        maxWidth: compact ? 960 : 1080,
        margin: "0 auto",
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif',
        color: "#e5e7eb",
      }}
    >
      {/* Browser chrome */}
      <div
        style={{
          background: "#0b0e17",
          border: "1px solid #1f2937",
          borderRadius: 14,
          overflow: "hidden",
          boxShadow:
            "0 30px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.02) inset",
        }}
      >
        {/* Chrome bar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 14px",
            background: "#0f1420",
            borderBottom: "1px solid #1f2937",
          }}
        >
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#ef4444" }} />
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#f59e0b" }} />
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#22c55e" }} />
          <div
            style={{
              flex: 1,
              margin: "0 12px",
              padding: "4px 12px",
              background: "#0a0e18",
              border: "1px solid #1a2130",
              borderRadius: 6,
              fontSize: 11,
              color: "#64748b",
              fontFamily: '"JetBrains Mono", monospace',
              textAlign: "center",
              letterSpacing: "0.02em",
            }}
          >
            auremcto.com{active?.urlPath || ""}
          </div>
          <span style={{ fontSize: 10, color: "#475569" }}>DEMO</span>
        </div>

        {/* Stage */}
        <div
          key={active?.id}
          style={{
            height: frameH,
            padding: framePad,
            background:
              "radial-gradient(900px 400px at 15% 0%, rgba(245,158,11,0.05), transparent 70%), #05070d",
            overflow: "hidden",
            position: "relative",
          }}
        >
          {active?.render?.({ tick, idx, playing })}
        </div>
      </div>

      {/* Caption + progress */}
      <div
        style={{
          marginTop: 18,
          display: "flex",
          alignItems: "center",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <button
          type="button"
          data-testid="walkthrough-playpause"
          onClick={() => setPlaying((p) => !p)}
          aria-label={playing ? "Pause" : "Play"}
          style={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            background: "#f59e0b",
            border: "none",
            color: "#000",
            fontWeight: 700,
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 14,
          }}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        {/* Iter 212m-231 — Mute toggle for voiceover.  Off by default
            respects browser autoplay policies; user opts IN by
            clicking. */}
        {audioEnabled && (
          <button
            type="button"
            data-testid="walkthrough-mute"
            onClick={() => setMuted((m) => !m)}
            aria-label={muted ? "Unmute narration" : "Mute narration"}
            title={muted ? "Unmute narration" : "Mute narration"}
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              background: muted ? "#1f2937" : "#334155",
              border: `1px solid ${muted ? "#374151" : "#f59e0b"}`,
              color: muted ? "#64748b" : "#f59e0b",
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 14,
            }}
          >
            {muted ? "🔇" : "🔊"}
          </button>
        )}
        {/* Hidden <audio> — driven programmatically from the effect above. */}
        {audioEnabled && (
          <audio
            ref={audioRef}
            data-testid="walkthrough-audio"
            preload="auto"
            muted={muted}
            style={{ display: "none" }}
          />
        )}
        <button
          type="button"
          data-testid="walkthrough-restart"
          onClick={restart}
          aria-label="Restart"
          style={{
            padding: "8px 14px",
            fontSize: 12,
            fontFamily: '"JetBrains Mono", monospace',
            background: "transparent",
            border: "1px solid #334155",
            borderRadius: 8,
            color: "#94a3b8",
            cursor: "pointer",
            letterSpacing: "0.06em",
          }}
        >
          ↺ RESTART
        </button>
        <div
          style={{
            flex: 1,
            minWidth: 180,
            fontSize: captionSz,
            fontFamily: '"JetBrains Mono", monospace',
            color: "#e5e7eb",
            letterSpacing: "0.01em",
          }}
        >
          <span style={{ color: "#f59e0b", fontWeight: 700 }}>
            {String(idx + 1).padStart(2, "0")}
          </span>
          <span style={{ color: "#475569", margin: "0 8px" }}>/</span>
          <span style={{ color: "#64748b" }}>
            {String(steps.length).padStart(2, "0")}
          </span>
          <span style={{ color: "#94a3b8", margin: "0 12px" }}>·</span>
          {active?.caption}
        </div>
      </div>

      {/* Step dots (also serve as scrubber) */}
      <div
        style={{
          marginTop: 12,
          display: "flex",
          gap: 6,
          alignItems: "center",
        }}
      >
        {steps.map((s, i) => (
          <button
            key={s.id}
            type="button"
            data-testid={`walkthrough-jump-${i}`}
            aria-label={`Jump to step ${i + 1}`}
            onClick={() => jump(i)}
            style={{
              flex: 1,
              height: 4,
              borderRadius: 4,
              border: "none",
              padding: 0,
              cursor: "pointer",
              background:
                i < idx
                  ? "#f59e0b"
                  : i === idx
                  ? `linear-gradient(90deg, #f59e0b ${tick * 100}%, #1f2937 ${tick * 100}%)`
                  : "#1f2937",
              transition: "background 120ms linear",
            }}
          />
        ))}
      </div>
    </div>
  );
}
