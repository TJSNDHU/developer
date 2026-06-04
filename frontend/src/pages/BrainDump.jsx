/**
 * BrainDump.jsx — Founder-only diagnostic page.
 *
 * "ORA gave a wrong answer" — what context did it actually see?
 * This page shows the exact string injected into the system prompt for
 * a given project, plus the raw brain document so the founder can
 * inline-delete bad decisions or preferences.
 */
import React, { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Trash2 } from "lucide-react";
import { api, getToken } from "../lib/api";

const AUTH = () => ({ headers: { Authorization: `Bearer ${getToken()}` } });

export default function BrainDump() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [commits, setCommits] = useState([]);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.get(`/admin/brain/${projectId}/dump`, AUTH());
      setData(r.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  }

  async function loadCommits() {
    try {
      const r = await api.get(
        `/admin/brain/${projectId}/recent-commits`, AUTH(),
      );
      setCommits(r.data?.commits || []);
    } catch {
      // non-fatal; just hide the section
      setCommits([]);
    }
  }

  useEffect(() => {
    load();
    loadCommits();
    /* eslint-disable-next-line */
  }, [projectId]);

  function askOraAboutCommit(sha, description) {
    const message = (
      `What pattern was used in commit ${sha}? Call get_commit_diff with ` +
      `sha="${sha}" and explain the approach. Context: "${description}".`
    );
    window.dispatchEvent(new CustomEvent("ora:prefill", { detail: { message } }));
    navigate("/dashboard");
  }

  async function deleteDecision(title) {
    if (!window.confirm(`Delete decision "${title}"?`)) return;
    await api.delete(
      `/admin/project-brain/${projectId}/decision`,
      { ...AUTH(), params: { title } },
    );
    await load();
  }

  async function deletePreference(preference) {
    if (!window.confirm(`Delete preference "${preference}"?`)) return;
    await api.delete(
      `/admin/project-brain/${projectId}/preference`,
      { ...AUTH(), params: { preference } },
    );
    await load();
  }

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1024, margin: "0 auto",
                  minHeight: "100vh", overflow: "auto" }}>
      <button
        data-testid="brain-back"
        onClick={() => navigate("/admin")}
        className="btn-ghost"
        style={{ marginBottom: 16, padding: "5px 10px", fontSize: 11 }}
      >
        <ArrowLeft size={11} /> back to admin
      </button>

      <h1 style={{ fontSize: 22, fontWeight: 500, color: "var(--text)",
                    margin: "0 0 6px" }}>
        Brain dump
      </h1>
      <div style={{ fontSize: 12, color: "var(--text-dim)",
                     fontFamily: "'JetBrains Mono', monospace", marginBottom: 22 }}>
        {projectId}{data?.repo ? ` · ${data.repo}` : ""}
      </div>

      {error && (
        <div data-testid="brain-error" style={{
          padding: "10px 12px", marginBottom: 16,
          background: "var(--danger-soft)", border: "1px solid var(--danger)",
          borderRadius: 6, color: "var(--danger)", fontSize: 12,
        }}>
          {error}
        </div>
      )}

      {busy && !data && (
        <div style={{ fontSize: 12, color: "var(--text-faint)",
                       fontStyle: "italic" }}>Loading…</div>
      )}

      {data && (
        <>
          {/* What ORA sees */}
          <Section title="What ORA sees (assembled context)">
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap",
                           fontSize: 10, color: "var(--text-faint)",
                           marginBottom: 8,
                           fontFamily: "'JetBrains Mono', monospace" }}>
              <span>{data.context_length_chars} chars</span>
              {data.has_aurem_commits && <span>✓ AUREM commits</span>}
              {data.has_github_commits && <span>✓ GitHub commits</span>}
              {!data.has_github_commits && data.had_pat === false && (
                <span style={{ color: "var(--warn)" }}>⚠ no PAT — remote commits skipped</span>
              )}
              {data.has_decisions && <span>✓ decisions</span>}
              {data.has_preferences && <span>✓ preferences</span>}
            </div>
            <pre data-testid="brain-assembled" style={{
              padding: "14px 16px",
              background: "var(--bg-elev)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              fontSize: 12, color: "var(--text-dim)",
              fontFamily: "'JetBrains Mono', monospace",
              maxHeight: 400, overflow: "auto",
              whiteSpace: "pre-wrap", wordBreak: "break-word",
              margin: 0,
            }}>
              {data.assembled_context || "(empty — no brain context for this project)"}
            </pre>
          </Section>

          {/* Recent commits with "Show diff →" — pre-fills ORA with get_commit_diff. */}
          {commits.length > 0 && (
            <Section title={`Recent commits (${commits.length})`}>
              {commits.map((c, i) => (
                <Row key={c.sha || i} testid={`brain-commit-${i}`}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      display: "flex", alignItems: "center", gap: 8,
                      fontSize: 12, color: "var(--text)", fontWeight: 500,
                      overflowWrap: "anywhere",
                    }}>
                      {c.short_sha && (
                        <code data-testid={`brain-commit-sha-${i}`} style={{
                          fontFamily: "'JetBrains Mono', monospace",
                          fontSize: 11,
                          color: "var(--accent-2, #ffb347)",
                          background: "var(--bg-elev)",
                          padding: "1px 6px", borderRadius: 3,
                          flexShrink: 0,
                        }}>{c.short_sha}</code>
                      )}
                      <span style={{ flex: 1, overflowWrap: "anywhere" }}>
                        {c.description || "(no description)"}
                      </span>
                    </div>
                    {c.files?.length > 0 && (
                      <div style={{
                        fontSize: 10.5, color: "var(--text-faint)",
                        marginTop: 3,
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>
                        {c.files.slice(0, 4).map(f => `· ${f}`).join("  ")}
                        {c.files.length > 4 ? ` +${c.files.length - 4} more` : ""}
                      </div>
                    )}
                  </div>
                  {c.sha && (
                    <button
                      data-testid={`brain-commit-show-diff-${i}`}
                      onClick={() => askOraAboutCommit(c.sha, c.description || "")}
                      style={{
                        fontSize: 10,
                        padding: "3px 9px",
                        background: "rgba(255,138,42,0.08)",
                        border: "1px solid rgba(255,200,120,0.32)",
                        borderRadius: 5,
                        color: "var(--accent-2, #ffb347)",
                        cursor: "pointer",
                        marginLeft: 8,
                        whiteSpace: "nowrap",
                        flexShrink: 0,
                      }}
                      title="Pre-fill chat with get_commit_diff(sha)"
                    >
                      Show diff →
                    </button>
                  )}
                </Row>
              ))}
            </Section>
          )}

          {/* Decisions list with inline delete */}
          {Array.isArray(data.raw_brain?.decisions) && data.raw_brain.decisions.length > 0 && (
            <Section title={`Decisions (${data.raw_brain.decisions.length})`}>
              {data.raw_brain.decisions.map((d, i) => (
                <Row key={i} testid={`brain-decision-${i}`}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, color: "var(--text)", fontWeight: 500,
                                   overflowWrap: "anywhere" }}>
                      {d.title || d.decision || "(untitled)"}
                    </div>
                    {(d.reason || d.why) && (
                      <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 3 }}>
                        {d.reason || d.why}
                      </div>
                    )}
                  </div>
                  <button
                    data-testid={`brain-decision-delete-${i}`}
                    onClick={() => deleteDecision(d.title || d.decision)}
                    className="btn-ghost"
                    style={{ padding: "4px 8px", fontSize: 11, color: "var(--danger)" }}
                    title="Delete decision"
                  >
                    <Trash2 size={11} />
                  </button>
                </Row>
              ))}
            </Section>
          )}

          {/* Team preferences list */}
          {(() => {
            const prefs = data.raw_brain?.team_preferences || data.raw_brain?.preferences || [];
            if (!prefs.length) return null;
            return (
              <Section title={`Team preferences (${prefs.length})`}>
                {prefs.map((p, i) => {
                  const text = typeof p === "string" ? p : (p.preference || p.text || JSON.stringify(p));
                  return (
                    <Row key={i} testid={`brain-pref-${i}`}>
                      <div style={{ flex: 1, minWidth: 0, fontSize: 12, color: "var(--text)",
                                     overflowWrap: "anywhere" }}>
                        {text}
                      </div>
                      <button
                        data-testid={`brain-pref-delete-${i}`}
                        onClick={() => deletePreference(text)}
                        className="btn-ghost"
                        style={{ padding: "4px 8px", fontSize: 11, color: "var(--danger)" }}
                      >
                        <Trash2 size={11} />
                      </button>
                    </Row>
                  );
                })}
              </Section>
            );
          })()}

          {/* Tech stack badge strip */}
          {Array.isArray(data.raw_brain?.tech_stack) && data.raw_brain.tech_stack.length > 0 && (
            <Section title="Tech stack">
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {data.raw_brain.tech_stack.map((t, i) => (
                  <span key={i} style={{
                    padding: "3px 9px", fontSize: 11,
                    background: "var(--panel-2)", border: "1px solid var(--border)",
                    borderRadius: 999, color: "var(--text-dim)",
                  }}>{t}</span>
                ))}
              </div>
            </Section>
          )}

          {/* Brain replay — sandbox "what would ORA say?" tester.
              Pure read-only: no MongoDB writes, no commit, no Vanguard. */}
          <BrainReplay projectId={projectId} />
        </>
      )}
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section style={{ marginBottom: 26 }}>
      <h3 style={{ fontSize: 11, letterSpacing: "0.1em",
                     textTransform: "uppercase",
                     color: "var(--text-faint)", margin: "0 0 10px" }}>
        {title}
      </h3>
      {children}
    </section>
  );
}

function Row({ children, testid }) {
  return (
    <div data-testid={testid} style={{
      display: "flex", alignItems: "flex-start", gap: 12,
      padding: "8px 12px", marginBottom: 4,
      background: "var(--panel-2)", border: "1px solid var(--border)",
      borderRadius: 4,
    }}>
      {children}
    </div>
  );
}


function BrainReplay({ projectId }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer]     = useState(null);
  const [busy, setBusy]         = useState(false);
  const [err, setErr]           = useState(null);

  async function ask(e) {
    e?.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setErr(null);
    setAnswer(null);
    try {
      const r = await api.post(
        `/admin/brain/${projectId}/replay`,
        { question: q },
        AUTH(),
      );
      setAnswer(r.data);
    } catch (e2) {
      setErr(e2?.response?.data?.detail || e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Brain replay — sandbox tester (read-only)">
      <div data-testid="brain-replay" style={{
        padding: "12px 14px",
        background: "var(--panel-2)",
        border: "1px solid var(--border)",
        borderRadius: 6,
      }}>
        <div style={{ fontSize: 11, color: "var(--text-faint)",
                       marginBottom: 10, fontStyle: "italic" }}>
          Ask ORA a question using ONLY this project's brain context.
          No commits, no writes, no Vanguard scan — purely diagnostic.
        </div>
        <form onSubmit={ask} style={{ display: "flex", gap: 8,
                                       flexWrap: "wrap" }}>
          <input
            data-testid="brain-replay-input"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. what tech stack does this project use?"
            className="input"
            style={{ flex: 1, minWidth: 220 }}
            maxLength={2000}
          />
          <button
            type="submit"
            data-testid="brain-replay-ask"
            disabled={busy || !question.trim()}
            className="btn-primary"
            style={{ padding: "8px 16px", fontSize: 12 }}
          >
            {busy ? "Asking…" : "Ask ORA"}
          </button>
        </form>
        {err && (
          <div data-testid="brain-replay-error" style={{
            marginTop: 10, padding: "8px 10px", fontSize: 11,
            background: "var(--danger-soft)", color: "var(--danger)",
            border: "1px solid var(--danger)", borderRadius: 4,
          }}>{err}</div>
        )}
        {answer && (
          <div data-testid="brain-replay-answer" style={{
            marginTop: 12, padding: "10px 12px",
            background: "var(--bg-elev)",
            border: "1px solid var(--border)",
            borderRadius: 4, fontSize: 12, color: "var(--text)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
            lineHeight: 1.55, overflowWrap: "anywhere",
          }}>
            {answer.answer}
            <div style={{ marginTop: 8, fontSize: 10,
                           color: "var(--text-faint)",
                           fontFamily: "'JetBrains Mono', monospace" }}>
              {answer.brain_chars} chars of brain context used
            </div>
          </div>
        )}
      </div>
    </Section>
  );
}
