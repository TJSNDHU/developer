/**
 * SidebarPreview.jsx — Iter 212m-80 preview route at /sidebar-preview
 *
 * Purpose: show the v0 dashboard sidebar in isolation so the user
 * can approve the look before we wire it into the real workspace
 * (no data hooked up, no navigation captured).
 */
import React from "react";
import DeveloperSidebar from "../components/dashboard/DeveloperSidebar";
import { C } from "../components/dashboard/colors";

export default function SidebarPreview() {
  return (
    <div
      data-testid="sidebar-preview-page"
      style={{
        display: "flex", height: "100vh", width: "100vw",
        backgroundColor: C.main, color: C.white,
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif',
      }}
    >
      <DeveloperSidebar />

      {/* Main canvas — placeholder so the sidebar reads in context. */}
      <main
        style={{
          flex: 1, padding: 40, overflow: "auto",
          backgroundColor: C.main, borderLeft: `1px solid ${C.border}`,
        }}
      >
        <div style={{
          maxWidth: 880, margin: "0 auto", display: "flex",
          flexDirection: "column", gap: 18,
        }}>
          <div
            style={{
              fontSize: 11, color: C.gray, letterSpacing: "0.2em",
              fontWeight: 600,
            }}
          >
            PREVIEW — DEVELOPER DASHBOARD SIDEBAR
          </div>
          <h1 style={{
            fontSize: 28, fontWeight: 700, color: C.white, margin: 0,
            letterSpacing: -0.5,
          }}>
            New sidebar component
          </h1>
          <p style={{ fontSize: 14, color: C.gray, margin: 0, lineHeight: 1.6 }}>
            This is the v0 design ported into <code style={{ color: C.orange }}>
            DeveloperSidebar.jsx</code>. The repositories list, recent
            queries and user badge use placeholder data so you can review
            the visual language before we wire it up to the real
            <code style={{ color: C.orange, marginLeft: 4 }}>
            /cto/projects/list</code> + chat sessions.
          </p>

          <div
            style={{
              marginTop: 16, padding: 20, borderRadius: 12,
              border: `1px solid ${C.border}`, backgroundColor: C.panel,
            }}
          >
            <div style={{ fontSize: 12, color: C.gray, marginBottom: 8 }}>
              Palette (strict, from <code>colors.js</code>):
            </div>
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
              gap: 10,
            }}>
              {Object.entries(C).map(([name, hex]) => (
                <div
                  key={name}
                  style={{
                    display: "flex", alignItems: "center", gap: 10,
                    padding: 10, borderRadius: 8,
                    border: `1px solid ${C.border}`, backgroundColor: C.sidebar,
                  }}
                >
                  <div style={{
                    width: 20, height: 20, borderRadius: 4,
                    backgroundColor: hex, border: `1px solid ${C.border}`,
                  }} />
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    <span style={{
                      fontSize: 12, fontWeight: 500, color: C.white,
                    }}>{name}</span>
                    <span style={{
                      fontSize: 11, color: C.gray,
                      fontFamily: "ui-monospace, JetBrains Mono, monospace",
                    }}>{hex}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{
            display: "flex", gap: 10, alignItems: "center", fontSize: 12,
            color: C.gray,
          }}>
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "4px 10px", borderRadius: 999,
              border: `1px solid ${C.orange}`, color: C.orange, fontWeight: 600,
            }}>
              📚 next step
            </span>
            Approve the look and I&apos;ll wire it into the existing
            workspace (replace the current sidebar in <code>ChatPanel.jsx</code>),
            connect live <code>/cto/projects/list</code>, and route
            repo clicks through the existing project switcher.
          </div>
        </div>
      </main>
    </div>
  );
}
