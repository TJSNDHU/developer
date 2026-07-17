/**
 * pages/admin/OraChat.jsx — Iter 212m-238
 *
 * Deep-link route (/admin/ora-chat) that renders the ORA Chat as a
 * full-page surface instead of the floating drawer. Backend is
 * identical — same session/message endpoints.
 *
 * MVP scope: reuse OraChatDrawer inside a full-page shell so we don't
 * duplicate stream-parsing logic. Phase 2 can add conversation search
 * + history sidebar without changing the shared component.
 */
import React from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import OraChatDrawer from "../../components/OraChatDrawer";

export default function OraChat() {
  // Simulate the drawer being permanently open by rendering it with
  // its own open-state — cleanest way to reuse without a prop rewrite.
  return (
    <div
      data-testid="admin-ora-chat-page"
      style={{
        minHeight: "100vh",
        background: "#0a0a0a",
        color: "#e8e3d3",
        padding: "32px 40px",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
      }}
    >
      <div style={{ maxWidth: 720, margin: "0 auto" }}>
        <Link
          to="/admin"
          data-testid="ora-chat-back"
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: 12, color: "#a39d8a",
            textDecoration: "none", marginBottom: 24,
          }}
        >
          <ArrowLeft size={13} /> back to admin
        </Link>
        <h1 style={{
          fontFamily: "ui-monospace, monospace",
          fontSize: 24, margin: "0 0 8px",
        }}>
          ORA Chat
        </h1>
        <p style={{ fontSize: 13, color: "#a39d8a", marginBottom: 24 }}>
          AUREM&apos;s context-aware assistant. Slash-commands for deterministic DB
          reads. Everything else routes to cheap OpenRouter models.
        </p>
        <div style={{
          fontSize: 12, color: "#7a7466", lineHeight: 1.6,
        }}>
          Click the chat bubble at the bottom-right to open the drawer.
          The drawer is available on every <code>/admin/*</code> page.
        </div>
      </div>
      <OraChatDrawer />
    </div>
  );
}
