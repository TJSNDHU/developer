/**
 * useTextToVoice.js — thin wrapper around the Web Speech API.
 * Strips common markdown noise so the spoken reply sounds natural.
 */
import { useState, useCallback, useRef } from "react";

export function useTextToVoice() {
  const [speaking, setSpeaking] = useState(false);
  const [supported] = useState(() =>
    typeof window !== "undefined" && "speechSynthesis" in window
  );
  const utterRef = useRef(null);

  const speak = useCallback((text) => {
    if (!supported || !text?.trim()) return;

    window.speechSynthesis.cancel();

    const clean = text
      .replace(/```[\s\S]*?```/g, " code block ")
      .replace(/`[^`]+`/g, "")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\*([^*]+)\*/g, "$1")
      .replace(/#{1,6}\s/g, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .trim();

    if (!clean) return;

    const utterance = new SpeechSynthesisUtterance(clean);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;

    const voices = window.speechSynthesis.getVoices();
    const preferred = voices.find((v) =>
      v.name.includes("Google") ||
      v.name.includes("Samantha") ||
      v.name.includes("Alex") ||
      v.lang.startsWith("en")
    );
    if (preferred) utterance.voice = preferred;

    utterance.onstart = () => setSpeaking(true);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);

    utterRef.current = utterance;
    window.speechSynthesis.speak(utterance);
  }, [supported]);

  const stop = useCallback(() => {
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    setSpeaking(false);
  }, []);

  return { speak, stop, speaking, supported };
}
