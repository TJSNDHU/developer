/**
 * pages/personal/DraftReview.jsx — Iter 212m-235 — Phase 6
 *
 * Split screen after a draft is scaffolded. Left: non-technical file
 * tree (Frontend / Database / Logic) with lucide icons. Right:
 * README preview. Bottom: sticky glass action bar (Regenerate + Ship).
 */
import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  Palette, Database, Settings2, FileText, RotateCcw, Rocket,
  Eye, Code2,
} from "lucide-react";
import { toast } from "sonner";
import { PersonalShell, PrimaryButton, SecondaryButton } from "./_shell";
import PreviewPanel from "./PreviewPanel";
import { api } from "../../lib/api";

/** Rule-based classification of a scaffolded path → a friendly bucket. */
function bucketFor(path) {
  const p = (path || "").toLowerCase();
  if (p.startsWith("ui/") || p.endsWith(".jsx") || p.endsWith(".tsx") ||
      p.endsWith(".html") || p.endsWith(".vue") || p.endsWith(".css"))
    return "frontend";
  if (p.includes("db") || p.includes("schema") || p.includes(".sql") ||
      p.includes("model") || p.includes("aurem_db_client"))
    return "database";
  if (p === "readme.md") return "readme";
  return "logic";
}

const BUCKETS = [
  { key: "frontend", label: "Frontend",       Icon: Palette   },
  { key: "database", label: "Database",       Icon: Database  },
  { key: "logic",    label: "Logic & Config", Icon: Settings2 },
  { key: "readme",   label: "Overview",       Icon: FileText  },
];

export default function DraftReview() {
  const { draftId } = useParams();
  const nav = useNavigate();
  const [draft, setDraft] = useState(null);
  const [err, setErr]     = useState("");
  const [busy, setBusy]   = useState(false);
  const [selected, setSelected] = useState(null);
  const [view, setView] = useState("code");   // "code" | "preview"

  useEffect(() => {
    (async () => {
      try {
        const r = await api.get(`/scaffold/${draftId}`);
        setDraft(r.data);
      } catch (e) {
        setErr("Couldn't load your draft. It may have expired.");
      }
    })();
  }, [draftId]);

  const grouped = useMemo(() => {
    const out = { frontend: [], database: [], logic: [], readme: [] };
    (draft?.files || []).forEach((f) => {
      out[bucketFor(f.path)].push(f);
    });
    return out;
  }, [draft]);

  const readme = grouped.readme[0]?.content || "";
  const previewText = selected
    ? draft.files.find((f) => f.path === selected)?.content || ""
    : readme;

  async function regenerate() {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api.post(`/scaffold/${draftId}/regenerate`, {});
      setDraft((d) => ({ ...d, files: r.data.files }));
      toast.success("Fresh take — let's see what's changed.");
    } catch (e) {
      toast.error("Regeneration didn't go through. Try again in a moment.");
    } finally { setBusy(false); }
  }

  function ship() { nav(`/build/${draftId}/ship`); }

  if (err) {
    return (
      <PersonalShell>
        <div style={{ maxWidth: 640, margin: "80px auto", textAlign: "center", padding: 24 }}>
          <h2 style={{ fontFamily: "'Cabinet Grotesk', sans-serif" }}>{err}</h2>
          <p style={{ color: "#6B6B63" }}>
            Drafts are kept for 48 hours. Start a fresh one below.
          </p>
          <PrimaryButton onClick={() => nav("/build")} data-testid="draft-start-over">
            Start a new draft
          </PrimaryButton>
        </div>
      </PersonalShell>
    );
  }

  if (!draft) {
    return (
      <PersonalShell>
        <div style={{ textAlign: "center", padding: 100, color: "#6B6B63" }}>
          Loading your draft…
        </div>
      </PersonalShell>
    );
  }

  return (
    <PersonalShell>
      <div
        data-testid="draft-review-page"
        style={{
          maxWidth: 1200, margin: "0 auto",
          padding: "32px 24px 140px",  // bottom padding leaves room for sticky bar
        }}
      >
        <div style={{ marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
          <div>
            <p style={{
              fontSize: 12, color: "#8B8B7D",
              textTransform: "uppercase", letterSpacing: "0.08em",
              margin: "0 0 4px",
            }}>Your draft</p>
            <h1 style={{
              fontFamily: "'Cabinet Grotesk', 'Manrope', sans-serif",
              fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em",
              margin: 0,
            }} data-testid="draft-brief-title">
              {(draft.brief || "").slice(0, 100)}{draft.brief?.length > 100 ? "…" : ""}
            </h1>
          </div>
          {/* Iter 212m-239 — Code / Preview toggle */}
          <div data-testid="draft-view-toggle" style={{
            display: "inline-flex", padding: 4, borderRadius: 999,
            background: "#F4F3EE", border: "1px solid #E5E5DF",
          }}>
            <button
              data-testid="draft-view-code"
              onClick={() => setView("code")}
              style={toggleBtn(view === "code")}
            ><Code2 size={13} /> Code</button>
            <button
              data-testid="draft-view-preview"
              onClick={() => setView("preview")}
              style={toggleBtn(view === "preview")}
            ><Eye size={13} /> Preview</button>
          </div>
        </div>

        {view === "preview" ? (
          <div data-testid="draft-preview-tab">
            <PreviewPanel draft={draft} />
          </div>
        ) : (
        <div style={{
          display: "grid",
          gridTemplateColumns: "minmax(240px, 320px) 1fr",
          gap: 32,
          alignItems: "start",
        }}>
          {/* ── Left: non-technical file tree ── */}
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            style={{
              background: "#FFFFFF",
              borderRadius: 16,
              border: "1px solid #E5E5DF",
              padding: 16, position: "sticky", top: 24,
            }}
          >
            {BUCKETS.map(({ key, label, Icon }) => {
              const files = grouped[key] || [];
              if (files.length === 0) return null;
              return (
                <div key={key} style={{ marginBottom: 16 }}>
                  <div style={{
                    display: "flex", alignItems: "center", gap: 8,
                    fontSize: 13, fontWeight: 600, color: "#1C1C19",
                    marginBottom: 6, padding: "4px 8px",
                  }}>
                    <Icon size={15} color="#E07A5F" strokeWidth={1.8} />
                    {label}
                    <span style={{
                      marginLeft: "auto", fontSize: 11, fontWeight: 500,
                      color: "#8B8B7D",
                    }}>{files.length}</span>
                  </div>
                  {files.map((f, i) => (
                    <motion.button
                      key={f.path}
                      data-testid="draft-file-tree-node"
                      initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: 0.05 * i }}
                      onClick={() => setSelected(f.path === selected ? null : f.path)}
                      style={{
                        display: "block", width: "100%", textAlign: "left",
                        padding: "6px 8px 6px 30px",
                        background: selected === f.path ? "rgba(224,122,95,0.10)" : "transparent",
                        border: "none", borderRadius: 6,
                        color: selected === f.path ? "#1C1C19" : "#6B6B63",
                        fontSize: 12, fontFamily: "ui-monospace, monospace",
                        cursor: "pointer",
                        transition: "background 150ms ease",
                      }}
                      onMouseEnter={(e) => {
                        if (selected !== f.path)
                          e.currentTarget.style.background = "#F4F3EE";
                      }}
                      onMouseLeave={(e) => {
                        if (selected !== f.path)
                          e.currentTarget.style.background = "transparent";
                      }}
                    >
                      {f.path.split("/").pop()}
                    </motion.button>
                  ))}
                </div>
              );
            })}
          </motion.div>

          {/* ── Right: preview panel ── */}
          <div
            data-testid="draft-preview-panel"
            style={{
              background: "#FFFFFF",
              borderRadius: 16,
              border: "1px solid #E5E5DF",
              padding: "32px 36px",
              minHeight: 400,
              fontSize: 14, lineHeight: 1.7,
              color: "#1C1C19",
              whiteSpace: "pre-wrap", fontFamily: "'Manrope', sans-serif",
            }}
          >
            <AnimatePresence mode="wait">
              <motion.div
                key={selected || "readme"}
                initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
              >
                {selected && (
                  <p style={{
                    fontSize: 12, color: "#8B8B7D",
                    fontFamily: "ui-monospace, monospace",
                    marginTop: 0, marginBottom: 16,
                  }}>{selected}</p>
                )}
                {selected ? (
                  <pre style={{
                    background: "#F4F3EE",
                    padding: 16, borderRadius: 8,
                    fontSize: 12, lineHeight: 1.6,
                    overflow: "auto", whiteSpace: "pre-wrap",
                    fontFamily: "ui-monospace, monospace",
                    color: "#1C1C19", margin: 0,
                  }}>{previewText}</pre>
                ) : (
                  <div style={{ whiteSpace: "pre-wrap" }}>{previewText}</div>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
        )}
      </div>

      {/* ── Sticky glass action bar ── */}
      <div
        data-testid="draft-action-bar"
        style={{
          position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 40,
          padding: "16px 24px",
          background: "rgba(253,253,249,0.85)",
          backdropFilter: "blur(16px)",
          borderTop: "1px solid #E5E5DF",
        }}
      >
        <div style={{
          maxWidth: 1200, margin: "0 auto",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          gap: 12,
        }}>
          <SecondaryButton
            data-testid="draft-regenerate-button"
            onClick={regenerate}
            disabled={busy}
          >
            <RotateCcw size={15} /> {busy ? "Regenerating…" : "Regenerate"}
          </SecondaryButton>
          <PrimaryButton
            data-testid="draft-ship-button"
            onClick={ship}
            disabled={busy}
            style={{ padding: "14px 28px", fontSize: 15 }}
          >
            Ship it <Rocket size={16} />
          </PrimaryButton>
        </div>
      </div>
    </PersonalShell>
  );
}


function toggleBtn(active) {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 14px", borderRadius: 999,
    background: active ? "#FFFFFF" : "transparent",
    color: active ? "#1C1C19" : "#6B6B63",
    border: "none", cursor: "pointer",
    fontSize: 13, fontWeight: 600, fontFamily: "inherit",
    boxShadow: active ? "0 1px 3px rgba(28,28,25,0.10)" : "none",
    transition: "background 200ms ease, color 200ms ease",
  };
}
