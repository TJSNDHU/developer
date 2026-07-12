/**
 * components/SlashCommandMenu.jsx  —  Directive Session 3 · Part C
 *
 * Composer autocomplete for the 4 scan slash-commands. Rendered as
 * an anchored popover ABOVE the input whenever the current value
 * matches `/…` at position 0.
 *
 * The commands are semantic — selecting one calls back to the parent
 * which fires the appropriate scan endpoint directly (there is no
 * backend LLM chat interception path for slash commands per user's
 * option-c decision). Rationale: existing scan endpoints already
 * exist + are auth-gated; wiring through the LLM would add a token
 * spend for what is a deterministic API call.
 */
import React, { useEffect, useState } from "react";
import { Search, ShieldCheck, Bug, Activity, Container } from "lucide-react";

export const SLASH_COMMANDS = [
  {
    id:          "scan",
    trigger:     "/scan",
    label:       "Run all scanners",
    description: "Vanguard + Bug Hunt + HTTP headers + Docker CIS",
    icon:        Search,
    categories:  ["security", "bug_hunt"],
  },
  {
    id:          "health-scan",
    trigger:     "/health-scan",
    label:       "Codebase health",
    description: "Performance, code quality, dependencies, database",
    icon:        Activity,
    categories:  ["performance", "code_quality", "dependencies", "database"],
  },
  {
    id:          "security-scan",
    trigger:     "/security-scan",
    label:       "Security only",
    description: "Vanguard 25-pattern regex catalog",
    icon:        ShieldCheck,
    categories:  ["security"],
  },
  {
    id:          "bug-hunt",
    trigger:     "/bug-hunt",
    label:       "Bug Hunt",
    description: "50+ Nuclei-inspired static rules",
    icon:        Bug,
    categories:  ["bug_hunt"],
  },
  {
    id:          "docker-scan",
    trigger:     "/docker-scan",
    label:       "Docker CIS",
    description: "Container hardening + compose file checks",
    icon:        Container,
    categories:  ["docker"],
  },
];

/**
 * `value` — current composer input.
 * Returns a list of commands whose trigger starts with the current
 * `/…` fragment (empty prefix returns all commands).
 */
export function matchSlashCommands(value) {
  if (typeof value !== "string" || !value.startsWith("/")) return [];
  const q = value.trim().toLowerCase();
  return SLASH_COMMANDS.filter((c) => c.trigger.toLowerCase().startsWith(q));
}

/**
 * Presentational popover. Parent (Composer) supplies:
 *   • matches       — result of matchSlashCommands(input)
 *   • selectedIndex — currently highlighted index (arrow-key nav)
 *   • onPick(cmd)   — user picked a command (click or Enter)
 */
export default function SlashCommandMenu({ matches, selectedIndex, onPick }) {
  const [highlighted, setHighlighted] = useState(selectedIndex ?? 0);
  useEffect(() => setHighlighted(selectedIndex ?? 0), [selectedIndex]);

  if (!matches || matches.length === 0) return null;

  return (
    <div
      data-testid="slash-command-menu"
      role="listbox"
      aria-label="Scan commands"
      className={
        "absolute bottom-full left-0 z-30 mb-2 w-80 max-w-full " +
        "rounded-lg border border-[#2a2a2a] bg-[#141414] p-1 " +
        "shadow-xl"
      }
    >
      <div className="border-b border-[#222] px-2 py-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/60">
        Scan commands
      </div>
      {matches.map((cmd, idx) => {
        const Icon = cmd.icon;
        const active = idx === highlighted;
        return (
          <button
            key={cmd.id}
            type="button"
            role="option"
            aria-selected={active}
            data-testid={`slash-cmd-${cmd.id}`}
            onMouseEnter={() => setHighlighted(idx)}
            onClick={() => onPick?.(cmd)}
            className={
              "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left " +
              (active ? "bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-white/5")
            }
          >
            <Icon className="mt-[3px] size-3.5 shrink-0" strokeWidth={2} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-[11px] text-foreground">{cmd.trigger}</span>
                <span className="truncate text-[11px]">{cmd.label}</span>
              </div>
              <div className="truncate text-[10px] text-muted-foreground/70">
                {cmd.description}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}
