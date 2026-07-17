/**
 * pages/OraDirect.jsx — Iter 212m-241
 *
 * Public, no-login route at /ora — bookmarkable from any device.
 * Renders a 4-digit PIN pad first; on success it fetches a real
 * admin JWT from `/api/aurem-dev/ora-chat/pin-login`, stores it in
 * localStorage under the same key the rest of the app uses, and
 * then reveals the ORA Chat as a full-screen surface.
 *
 * Security posture:
 *  - Backend enforces 5 wrong attempts / hour / IP + constant-time compare
 *  - PIN itself is never logged; only { ok, ip, ts } is persisted
 *  - Successful login mints a normal 7-day admin JWT (same as password login)
 *  - Once a valid token is already in localStorage we skip the PIN pad
 *
 * Responsive: full viewport on all sizes; the OraChatDrawer mounts
 * inside a fullscreen shell so the max-width limit of the drawer
 * doesn't apply here.
 */
import React, { useEffect, useState } from "react";
import { Lock } from "lucide-react";
import { api, setToken, getToken } from "../lib/api";
import OraChatDrawer from "../components/OraChatDrawer";

const PIN_LENGTH = 4;

export default function OraDirect() {
  const [pin, setPin]           = useState("");
  const [busy, setBusy]         = useState(false);
  const [err, setErr]           = useState(null);
  const [authorized, setAuthed] = useState(!!getToken());

  // If a token already exists (recent PIN unlock or normal admin
  // login on this browser), skip straight to the chat.
  useEffect(() => {
    if (getToken()) {
      // Confirm the token still works — /auth/me is cheap.
      api.get("/auth/me").then(() => setAuthed(true))
                          .catch(() => setToken(null));
    }
  }, []);

  const submit = async (nextPin) => {
    const p = (nextPin ?? pin).trim();
    if (p.length !== PIN_LENGTH || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.post("/ora-chat/pin-login", { pin: p });
      setToken(r.data.token);
      setAuthed(true);
    } catch (e) {
      const d = e?.response?.data?.detail || e?.response?.data;
      if (d?.error === "too_many_attempts") {
        setErr(d.message || "Too many attempts. Try again in an hour.");
      } else if (d?.error === "invalid_pin") {
        setErr(`Wrong PIN. ${d.attempts_remaining} attempt(s) left.`);
      } else {
        setErr("PIN login failed. Check your connection.");
      }
      setPin("");
    } finally { setBusy(false); }
  };

  const pressDigit = (d) => {
    if (pin.length >= PIN_LENGTH || busy) return;
    const next = pin + d;
    setPin(next);
    if (next.length === PIN_LENGTH) submit(next);
  };
  const pressBack  = () => setPin(pin.slice(0, -1));
  const pressClear = () => setPin("");

  if (authorized) {
    // Full-screen ORA — reuse the drawer but drop it into a
    // fullscreen shell so it fills the viewport instead of the
    // right-side panel.
    return (
      <div
        data-testid="ora-direct-fullscreen"
        style={{
          position: "fixed", inset: 0,
          background: "#0a0a0a", color: "#e8e3d3",
          display: "flex", flexDirection: "column",
        }}
      >
        <OraChatDrawer forceOpen fullscreen />
      </div>
    );
  }

  return (
    <div
      data-testid="ora-direct-pin"
      style={{
        minHeight: "100vh",
        background: "radial-gradient(70% 60% at 50% 30%, #1a1410 0%, #0a0a0a 60%)",
        color: "#e8e3d3",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 20,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
      }}
    >
      <div style={{ maxWidth: 380, width: "100%", textAlign: "center" }}>
        <div style={{
          width: 56, height: 56, borderRadius: 14,
          background: "rgba(224,122,95,0.14)",
          border: "1px solid rgba(224,122,95,0.28)",
          margin: "0 auto 22px",
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <Lock size={22} color="#E07A5F" strokeWidth={1.8} />
        </div>
        <div style={{
          fontFamily: "ui-monospace, monospace",
          fontSize: 22, fontWeight: 700, marginBottom: 6,
          letterSpacing: -0.5,
        }}>
          ORA Chat
        </div>
        <div style={{ fontSize: 13, color: "#7a7466", marginBottom: 32 }}>
          Enter your 4-digit PIN
        </div>

        {/* Dots */}
        <div style={{
          display: "flex", justifyContent: "center", gap: 14,
          marginBottom: 34,
        }}>
          {[0, 1, 2, 3].map(i => (
            <div key={i} data-testid={`pin-dot-${i}`}
                 style={{
                   width: 16, height: 16, borderRadius: "50%",
                   background: pin.length > i ? "#E07A5F" : "transparent",
                   border: `2px solid ${pin.length > i
                     ? "#E07A5F"
                     : "rgba(255,255,255,0.16)"}`,
                   transition: "background 0.1s ease",
                 }}
            />
          ))}
        </div>

        {err && (
          <div data-testid="pin-error" style={{
            fontSize: 12, color: "#f88",
            marginBottom: 20, minHeight: 16,
          }}>{err}</div>
        )}

        {/* Number pad */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 12,
          maxWidth: 300, margin: "0 auto",
        }}>
          {["1","2","3","4","5","6","7","8","9"].map(d => (
            <PinKey key={d} label={d} testId={`pin-key-${d}`}
                    onClick={() => pressDigit(d)} disabled={busy} />
          ))}
          <PinKey label="C" testId="pin-key-clear"
                  onClick={pressClear} disabled={busy} muted />
          <PinKey label="0" testId="pin-key-0"
                  onClick={() => pressDigit("0")} disabled={busy} />
          <PinKey label="⌫" testId="pin-key-back"
                  onClick={pressBack} disabled={busy} muted />
        </div>

        <div style={{
          marginTop: 32, fontSize: 10, color: "#4d4a41",
          lineHeight: 1.6,
        }}>
          5 wrong attempts per hour · Session lasts 7 days on this device
        </div>
      </div>
    </div>
  );
}

function PinKey({ label, onClick, disabled, muted, testId }) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      style={{
        aspectRatio: "1 / 1",
        fontSize: 22,
        fontFamily: "ui-monospace, monospace",
        fontWeight: 500,
        color: muted ? "#a39d8a" : "#e8e3d3",
        background: muted
          ? "rgba(255,255,255,0.02)"
          : "rgba(255,255,255,0.04)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 12,
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "background 0.12s ease, transform 0.12s ease",
      }}
      onMouseDown={(e) => e.currentTarget.style.transform = "scale(0.96)"}
      onMouseUp={(e)   => e.currentTarget.style.transform = "scale(1)"}
      onMouseLeave={(e)=> e.currentTarget.style.transform = "scale(1)"}
    >
      {label}
    </button>
  );
}
