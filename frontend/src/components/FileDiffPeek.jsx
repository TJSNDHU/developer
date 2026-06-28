/**
 * FileDiffPeek.jsx — Iter 212m-91
 *
 * Cursor-like inline file-diff peek. Renders as a small orange chip
 * `📄 path/to/file.py` underneath an ORA reply. Hovering the chip
 * fetches the CURRENT file content from the connected GitHub repo and
 * shows a side-by-side mini diff vs the proposed code block in the
 * reply, inside a dark floating tooltip.
 *
 * Data:
 *   • Current content   ←  GET /cto/projects/{project_id}/file?path=…
 *   • Proposed content  ←  passed in via `proposedCode` prop
 *
 * The diff is a naive line-by-line equality check — same renderer
 * pattern as ShipConfirmModal's +/− counts but here we show the
 * actual changed lines in red/green columns.
 *
 * Tooltip auto-positions left when the chip is in the right half of
 * the bubble (avoids spilling off-screen on narrow viewports).
 */
import React, { useMemo, useRef, useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import { api } from "../lib/api";

const FETCH_DELAY = 350;       // wait a bit before firing the GitHub call

function naiveDiff(oldText, newText) {
  // O(n) line walk — fast enough for files under ~2k lines. We emit
  // an array of {kind:"same"|"add"|"del", text} entries. No LCS — we
  // mark blocks that don't match line-for-line as del/add pairs. Good
  // enough for the peek UX; real review goes to the full PR diff.
  const a = (oldText || "").split("\n");
  const b = (newText || "").split("\n");
  const out = [];
  const max = Math.max(a.length, b.length);
  for (let i = 0; i < max; i++) {
    const oa = a[i];
    const ob = b[i];
    if (oa === undefined) { out.push({ kind: "add", text: ob }); }
    else if (ob === undefined) { out.push({ kind: "del", text: oa }); }
    else if (oa === ob) { out.push({ kind: "same", text: oa }); }
    else { out.push({ kind: "del", text: oa }); out.push({ kind: "add", text: ob }); }
  }
  return out;
}

export default function FileDiffPeek({ path, projectId, proposedCode }) {
  const [open, setOpen]    = useState(false);
  const [busy, setBusy]    = useState(false);
  const [err, setErr]      = useState("");
  const [current, setCur]  = useState(null);
  const timerRef           = useRef(null);
  const chipRef            = useRef(null);

  const filename = path.split("/").pop();

  async function load() {
    if (current !== null || busy || !projectId) return;
    setBusy(true); setErr("");
    try {
      const r = await api.get(
        `/cto/projects/${projectId}/file?path=${encodeURIComponent(path)}`,
      );
      setCur(r?.data?.content ?? "");
    } catch (e) {
      const msg = e?.response?.status === 404
        ? "(new file — no current version on GitHub)"
        : e?.response?.data?.detail || "Failed to load current file";
      // 404 isn't an error for new files; treat as empty current
      if (e?.response?.status === 404) setCur("");
      else setErr(msg);
    } finally { setBusy(false); }
  }

  function onEnter() {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setOpen(true); load();
    }, FETCH_DELAY);
  }
  function onLeave() {
    if (timerRef.current) clearTimeout(timerRef.current);
    setOpen(false);
  }

  const diff = useMemo(() => {
    if (current === null) return null;
    return naiveDiff(current, proposedCode || "").slice(0, 60); // cap render
  }, [current, proposedCode]);

  const addedCount = diff?.filter((d) => d.kind === "add").length ?? 0;
  const removedCount = diff?.filter((d) => d.kind === "del").length ?? 0;

  return (
    <span
      ref={chipRef}
      data-testid={`file-diff-peek-${path.replace(/[^a-z0-9]/gi, "-")}`}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      style={{ position: "relative", display: "inline-flex" }}
    >
      <span style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        padding: "3px 9px", marginRight: 6, marginTop: 6,
        borderRadius: 6, fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
        background: "rgba(255,102,8,0.10)",
        color: "#FF6608", border: "1px solid rgba(255,102,8,0.32)",
        cursor: "default",
      }}>
        <FileText size={10} />
        <span>{filename}</span>
      </span>

      {open && (
        <div
          data-testid={`file-diff-tooltip-${path.replace(/[^a-z0-9]/gi, "-")}`}
          style={{
            position: "absolute", top: "100%", left: 0, zIndex: 50,
            marginTop: 4, width: "min(640px, 80vw)",
            background: "#0A0A0A", border: "1px solid #FF6608",
            borderRadius: 10, padding: 12,
            boxShadow: "0 18px 48px rgba(0,0,0,0.72)",
            fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
            color: "#F5F5F5",
          }}
        >
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            paddingBottom: 8, marginBottom: 8,
            borderBottom: "1px solid #222",
          }}>
            <FileText size={11} style={{ color: "#FF6608" }} />
            <span style={{ flex: 1, color: "#F5F5F5" }}>{path}</span>
            {diff && (
              <span style={{ fontSize: 10 }}>
                <span style={{ color: "#22C55E" }}>+{addedCount}</span>
                {" / "}
                <span style={{ color: "#EF4444" }}>−{removedCount}</span>
              </span>
            )}
          </div>

          {busy && (
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              color: "#8A8A8A", padding: 12,
            }}>
              <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
              Loading current file from GitHub…
            </div>
          )}
          {err && !busy && (
            <div style={{ color: "#EF4444", padding: 8, fontSize: 11 }}>
              ⚠ {err}
            </div>
          )}
          {!busy && !err && diff && (
            <div style={{
              maxHeight: 320, overflowY: "auto", overflowX: "auto",
              borderRadius: 6, background: "#0A0A0A",
              border: "1px solid #161616",
            }}>
              {diff.length === 0 ? (
                <div style={{ padding: 10, color: "#666", fontStyle: "italic" }}>
                  (no diff — files match)
                </div>
              ) : (
                diff.map((d, i) => (
                  <div key={i} style={{
                    display: "flex", padding: "1px 8px",
                    background: d.kind === "add"
                      ? "rgba(34,197,94,0.07)"
                      : d.kind === "del"
                        ? "rgba(239,68,68,0.07)"
                        : "transparent",
                    color: d.kind === "add"
                      ? "#22C55E"
                      : d.kind === "del"
                        ? "#EF4444"
                        : "#8A8A8A",
                    whiteSpace: "pre", lineHeight: 1.5,
                  }}>
                    <span style={{ width: 14, flexShrink: 0, opacity: 0.7 }}>
                      {d.kind === "add" ? "+" : d.kind === "del" ? "−" : " "}
                    </span>
                    <span style={{ flex: 1 }}>{d.text || " "}</span>
                  </div>
                ))
              )}
            </div>
          )}
          <div style={{
            marginTop: 8, fontSize: 9, color: "#666",
            letterSpacing: "0.1em", textTransform: "uppercase",
          }}>
            Hover diff · proposed vs current branch
          </div>
        </div>
      )}
    </span>
  );
}
