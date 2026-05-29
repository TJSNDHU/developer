/**
 * TemperatureBadge.jsx — Show the LLM temperature/mode used for a reply.
 * Blue dot for deterministic (≤0.2), amber for warm (>0.2).
 */
import React from "react";
import { Thermometer } from "lucide-react";

export default function TemperatureBadge({ temperature, mode }) {
  if (typeof temperature !== "number") return null;
  const cold = temperature <= 0.2;
  const color = cold ? "#60a5fa" : "var(--accent-2)";
  return (
    <span
      data-testid="temperature-badge"
      title={`mode: ${mode || "?"} · temperature ${temperature}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10,
        color,
        padding: "2px 6px",
        border: `1px solid ${cold ? "rgba(96,165,250,0.3)" : "rgba(255,197,96,0.35)"}`,
        borderRadius: 999,
        letterSpacing: "0.05em",
        background: cold ? "rgba(96,165,250,0.08)" : "rgba(255,197,96,0.08)",
      }}
    >
      <Thermometer size={9} />
      {temperature.toFixed(1)}
      {mode ? <span style={{ opacity: 0.65, marginLeft: 2 }}>· {mode}</span> : null}
    </span>
  );
}
