/**
 * DeveloperSidebar.jsx — Iter 212m-80
 * Faithful JSX port of `components/dashboard/sidebar.tsx` from the
 * v0 design pack (developer-dashboard-design.zip).
 *
 * Pure presentational — no data fetching yet. Caller injects:
 *   • activeRepoName  (string, optional)
 *   • repos           (array of {name, branch, dotColor, active})
 *   • recent          (array of strings)
 *   • userInitials    (string)
 *   • userBadge       (string — "$9/mo" by default)
 *   • onAddRepo       (callback)
 *   • onSelectRepo    (callback(repo))
 *   • onSelectRecent  (callback(item))
 *
 * Width is fixed at 240 px (w-60) per the original design. The
 * full-height column expects a flex parent with `display:flex`.
 */
import React from "react";
import { Plus } from "lucide-react";
import { C } from "./colors";

const DEFAULT_REPOS = [
  { name: "TJSNDHU/Aurem",   branch: "main",        dotColor: C.orange, active: true },
  { name: "atlas-dashboard", branch: "feat/api",    dotColor: C.gray },
  { name: "orbit-payments",  branch: "fix/webhook", dotColor: C.red },
];

const DEFAULT_RECENT = [
  "Refactor auth middleware",
  "Stripe webhook retry logic",
  "Streaming response cleanup",
];

export default function DeveloperSidebar({
  repos        = DEFAULT_REPOS,
  recent       = DEFAULT_RECENT,
  userInitials = "TJ",
  userLabel    = "Founder",
  userBadge    = "$9/mo",
  onAddRepo,
  onSelectRepo,
  onSelectRecent,
}) {
  return (
    <aside
      data-testid="dev-sidebar"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        width: 240,
        flexShrink: 0,
        backgroundColor: C.sidebar,
        borderRight: `1px solid ${C.border}`,
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif',
      }}
    >
      {/* Logo */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "20px" }}>
        <div
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            height: 32, width: 32, flexShrink: 0,
            borderRadius: "50%", backgroundColor: C.orange,
          }}
        >
          <span style={{ fontSize: 14, fontWeight: 700, color: C.sidebar }}>O</span>
        </div>
        <div style={{ lineHeight: 1.15 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: C.white }}>ORA</div>
          <div style={{ fontSize: 11, color: C.gray }}>by Aurem CTO</div>
        </div>
      </div>

      {/* Repositories */}
      <div style={{ padding: "8px 20px 0" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span
            style={{
              fontSize: 11, fontWeight: 600, color: C.gray,
              letterSpacing: "0.18em",
            }}
          >
            REPOSITORIES
          </span>
          <button
            type="button"
            data-testid="dev-sidebar-add-repo"
            onClick={onAddRepo}
            style={{
              display: "flex", alignItems: "center", gap: 4,
              padding: "4px 8px", fontSize: 11, fontWeight: 500,
              borderRadius: 6, color: C.orange, background: "transparent",
              border: `1px solid ${C.border}`, cursor: "pointer",
            }}
          >
            <Plus size={12} /> Add
          </button>
        </div>

        <ul style={{ marginTop: 12, padding: 0, listStyle: "none",
                     display: "flex", flexDirection: "column", gap: 4 }}>
          {repos.map((repo) => (
            <li key={repo.name}>
              <button
                type="button"
                data-testid={`dev-sidebar-repo-${repo.name.replace(/[^a-z0-9]/gi, "-").toLowerCase()}`}
                onClick={() => onSelectRepo?.(repo)}
                style={{
                  display: "flex", alignItems: "center", gap: 12, width: "100%",
                  padding: "8px 12px", textAlign: "left", borderRadius: 8,
                  backgroundColor: repo.active ? C.main : "transparent",
                  border: `1px solid ${repo.active ? C.border : "transparent"}`,
                  cursor: "pointer", color: C.white,
                }}
              >
                <span
                  style={{
                    height: 8, width: 8, flexShrink: 0,
                    borderRadius: "50%", backgroundColor: repo.dotColor,
                  }}
                />
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span
                    style={{
                      display: "block", fontSize: 13, fontWeight: 500, color: C.white,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}
                  >
                    {repo.name}
                  </span>
                  <span style={{ display: "block", fontSize: 11, color: C.gray }}>
                    {repo.branch}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* Divider */}
      <div
        style={{
          margin: "16px 20px", height: 1, backgroundColor: C.border,
        }}
      />

      {/* Recent */}
      <div style={{ padding: "0 20px" }}>
        <span
          style={{
            fontSize: 11, fontWeight: 600, color: C.gray,
            letterSpacing: "0.18em",
          }}
        >
          RECENT
        </span>
        <ul style={{ marginTop: 12, padding: 0, listStyle: "none",
                     display: "flex", flexDirection: "column", gap: 4 }}>
          {recent.map((item) => (
            <li key={item}>
              <button
                type="button"
                onClick={() => onSelectRecent?.(item)}
                title={item}
                style={{
                  display: "block", width: "100%", padding: "6px 12px",
                  textAlign: "left", fontSize: 12, color: C.gray,
                  background: "transparent", border: "none",
                  borderRadius: 6, cursor: "pointer",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}
              >
                {item}
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* User card (sticks to bottom via mt:auto on the column) */}
      <div style={{ marginTop: "auto", padding: "16px 20px 20px" }}>
        <div
          data-testid="dev-sidebar-user"
          style={{
            display: "flex", alignItems: "center", gap: 12, padding: 12,
            borderRadius: 8, backgroundColor: C.main,
            border: `1px solid ${C.border}`,
          }}
        >
          <div
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              height: 36, width: 36, flexShrink: 0,
              borderRadius: "50%", backgroundColor: C.border,
              fontSize: 13, fontWeight: 600, color: C.white,
            }}
          >
            {userInitials}
          </div>
          <div style={{ minWidth: 0, flex: 1, lineHeight: 1.15 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: C.white }}>
              {userLabel}
            </div>
          </div>
          <span
            style={{
              padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 600,
              color: C.orange, border: `1px solid ${C.orange}`,
            }}
          >
            {userBadge}
          </span>
        </div>
      </div>
    </aside>
  );
}
