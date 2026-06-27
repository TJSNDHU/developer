/**
 * ThemeToggle.jsx — Iter 212m-52
 *
 * 3-way segmented control (Auto / Day / Night) with subtle amber
 * highlight on the active option. Sits in the topbar.
 *
 * Auto = follow OS prefers-color-scheme
 * Day  = light (white background)
 * Night = dark (current Aurem aesthetic)
 *
 * Persists choice in localStorage via services/theme.js.
 */
import { useEffect, useState } from "react";
import { Sun, Moon, Monitor } from "lucide-react";
import {
  getThemeMode, setThemeMode, subscribe, getResolvedTheme,
} from "../services/theme";

const OPTIONS = [
  { value: "auto",  label: "Auto",  icon: Monitor, testid: "theme-auto"  },
  { value: "light", label: "Day",   icon: Sun,     testid: "theme-day"   },
  { value: "dark",  label: "Night", icon: Moon,    testid: "theme-night" },
];

export default function ThemeToggle({ compact = false }) {
  const [mode, setMode] = useState(getThemeMode());
  const [resolved, setResolved] = useState(getResolvedTheme());

  useEffect(() => {
    const off = subscribe(() => {
      setMode(getThemeMode());
      setResolved(getResolvedTheme());
    });
    return off;
  }, []);

  const onPick = (next) => {
    setMode(next);
    setThemeMode(next);
  };

  return (
    <div
      data-testid="theme-toggle"
      role="radiogroup"
      aria-label="Theme"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 2,
        padding: 3,
        borderRadius: 999,
        border: "1px solid var(--border-strong)",
        background: "var(--panel-2)",
      }}
    >
      {OPTIONS.map((opt) => {
        const Icon = opt.icon;
        const active = mode === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            data-testid={opt.testid}
            data-active={active ? "true" : "false"}
            onClick={() => onPick(opt.value)}
            title={`${opt.label} mode${
              opt.value === "auto" ? ` (currently ${resolved})` : ""
            }`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              padding: compact ? "5px 8px" : "6px 11px",
              borderRadius: 999,
              border: "none",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.02em",
              color: active ? "#1a1410" : "var(--text-dim)",
              background: active
                ? "linear-gradient(180deg, var(--accent-2) 0%, var(--accent) 100%)"
                : "transparent",
              transition: "background 180ms ease, color 180ms ease",
            }}
          >
            <Icon size={13} strokeWidth={2.2} />
            {!compact && <span>{opt.label}</span>}
          </button>
        );
      })}
    </div>
  );
}
