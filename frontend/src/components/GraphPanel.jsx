/**
 * components/GraphPanel.jsx — Iter 165
 *
 * Sliding right-drawer that renders the project's hybrid knowledge
 * graph (regex symbols + AI descriptions). Layers grouped as tiles,
 * search across paths & descriptions, expandable file detail with an
 * "Ask ORA about this" hook that dispatches a `ora-inject` event
 * picked up by ChatPanel.
 */
import React, { useEffect, useState, useRef } from "react";
import { X, Search, GitBranch, RefreshCw } from "lucide-react";
import { api } from "../lib/api";

const LAYER_COLORS = {
  API:     "#f59e0b",
  Service: "#6366f1",
  Data:    "#10b981",
  UI:      "#3b82f6",
  Hook:    "#8b5cf6",
  Util:    "#6b7280",
  Config:  "#ef4444",
  Test:    "#374151",
  Other:   "#4b5563",
};

const LAYER_ICONS = {
  API: "🔌", Service: "⚙️", Data: "🗄️",
  UI: "🎨", Hook: "🪝", Util: "🔧",
  Config: "⚙️", Test: "🧪", Other: "📄",
};

export default function GraphPanel({ projectId, open, onClose }) {
  const [graph, setGraph] = useState(null);
  const [status, setStatus] = useState("idle");
  const [search, setSearch] = useState("");
  const [activeLayer, setActiveLayer] = useState(null);
  const [activeFile, setActiveFile] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!open || !projectId) return undefined;
    loadGraph();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId]);

  async function loadGraph() {
    setStatus("loading");
    try {
      const r = await api.get(
        `/cto/projects/${projectId}/graph?full=true`
      );
      if (r.data?.status === "ready") {
        setGraph(r.data.graph);
        setStatus("ready");
      } else {
        setStatus("building");
        triggerBuild();
      }
    } catch {
      setStatus("error");
    }
  }

  async function triggerBuild() {
    try {
      await api.post(`/cto/projects/${projectId}/build-graph`);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const r = await api.get(
            `/cto/projects/${projectId}/graph?full=true`
          );
          if (r.data?.status === "ready") {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setGraph(r.data.graph);
            setStatus("ready");
          }
        } catch {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }, 4000);
      setTimeout(() => {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setStatus((s) => (s === "building" ? "timeout" : s));
        }
      }, 300000);
    } catch {
      setStatus("error");
    }
  }

  if (!open) return null;

  const layers = graph?.layers || {};
  const nodes = graph?.nodes || {};

  const displayFiles = search
    ? Object.keys(nodes).filter(
        (f) =>
          f.toLowerCase().includes(search.toLowerCase()) ||
          (nodes[f]?.description || "")
            .toLowerCase()
            .includes(search.toLowerCase())
      )
    : activeLayer
    ? layers[activeLayer] || []
    : [];

  const priorityLayers = [
    "API", "Service", "UI", "Data", "Hook", "Util", "Config", "Other",
  ].filter((l) => (layers[l] || []).length > 0);

  return (
    <>
      <div
        onClick={onClose}
        data-testid="graph-panel-backdrop"
        style={{
          position: "fixed", inset: 0,
          background: "rgba(0,0,0,0.4)", zIndex: 8500,
        }}
      />
      <div
        data-testid="graph-panel"
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: "min(460px, 100vw)",
          background: "var(--panel)",
          borderLeft: "1px solid var(--border-strong)",
          zIndex: 8501,
          display: "flex", flexDirection: "column",
          animation: "slide-in-right 0.2s ease-out",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex", alignItems: "center",
            justifyContent: "space-between",
            padding: "14px 18px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <GitBranch size={15} style={{ color: "#f59e0b" }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>
              Codebase Graph
            </span>
            {graph && (
              <span
                style={{
                  fontSize: 10, color: "var(--text-faint)",
                  fontFamily: "monospace",
                  background: "var(--bg-elev)",
                  padding: "2px 6px", borderRadius: 4,
                }}
              >
                {graph.file_count} files · {graph.llm_files || 0} described
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            {status === "ready" && (
              <button
                onClick={() => { setStatus("building"); triggerBuild(); }}
                title="Refresh graph"
                data-testid="graph-refresh-btn"
                style={{
                  background: "none", border: "none",
                  color: "var(--text-faint)",
                  cursor: "pointer", padding: 4,
                }}
              >
                <RefreshCw size={13} />
              </button>
            )}
            <button
              onClick={onClose}
              data-testid="graph-close-btn"
              style={{
                background: "none", border: "none",
                color: "var(--text-faint)",
                cursor: "pointer", padding: 4,
              }}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 14 }}>
          {status === "loading" && (
            <div
              style={{
                textAlign: "center", padding: 40,
                color: "var(--text-faint)", fontSize: 13,
              }}
            >
              Loading…
            </div>
          )}

          {status === "building" && (
            <div style={{ padding: 24, textAlign: "center" }}>
              <div
                style={{
                  fontSize: 28, marginBottom: 12,
                  animation: "spin 2s linear infinite",
                  display: "inline-block",
                }}
              >
                🔍
              </div>
              <div
                style={{
                  fontSize: 13, fontWeight: 600,
                  color: "#f59e0b", marginBottom: 6,
                }}
              >
                Scanning your codebase…
              </div>
              <div
                style={{
                  fontSize: 11, color: "var(--text-faint)",
                  lineHeight: 1.6,
                }}
              >
                Building knowledge graph with AI descriptions.
                <br />
                Takes 1–3 min the first time. You can close and keep working.
              </div>
              <div
                style={{
                  marginTop: 16, height: 3,
                  background: "var(--border)",
                  borderRadius: 2, overflow: "hidden",
                }}
              >
                <div
                  style={{
                    height: "100%", background: "#f59e0b",
                    borderRadius: 2,
                    animation: "progress-pulse 2s ease-in-out infinite",
                  }}
                />
              </div>
              <style>{`
                @keyframes progress-pulse {
                  0%   { width: 10%; }
                  50%  { width: 80%; }
                  100% { width: 10%; }
                }
                @keyframes spin {
                  from { transform: rotate(0deg); }
                  to   { transform: rotate(360deg); }
                }
              `}</style>
            </div>
          )}

          {status === "timeout" && (
            <div
              style={{
                textAlign: "center", padding: 24,
                color: "var(--text-faint)", fontSize: 12,
              }}
            >
              Graph build is taking longer than expected. Close and reopen
              to retry, or click refresh.
            </div>
          )}

          {status === "ready" && graph && (
            <>
              <div
                style={{
                  display: "flex", alignItems: "center", gap: 8,
                  background: "var(--bg-elev)",
                  border: "1px solid var(--border)",
                  borderRadius: 8, padding: "7px 11px",
                  marginBottom: 14,
                }}
              >
                <Search size={12} style={{ color: "var(--text-faint)" }} />
                <input
                  data-testid="graph-search-input"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setActiveLayer(null);
                    setActiveFile(null);
                  }}
                  placeholder="Search files or descriptions…"
                  style={{
                    background: "none", border: "none",
                    color: "var(--text)", fontSize: 12,
                    outline: "none", flex: 1,
                  }}
                />
                {search && (
                  <button
                    onClick={() => setSearch("")}
                    style={{
                      background: "none", border: "none",
                      color: "var(--text-faint)",
                      cursor: "pointer", fontSize: 14,
                    }}
                  >
                    ×
                  </button>
                )}
              </div>

              {!search && (
                <div
                  data-testid="graph-layer-tiles"
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 8, marginBottom: 14,
                  }}
                >
                  {priorityLayers.map((layer) => {
                    const files = layers[layer] || [];
                    const color = LAYER_COLORS[layer];
                    const icon = LAYER_ICONS[layer];
                    const active = activeLayer === layer;
                    return (
                      <button
                        key={layer}
                        data-testid={`graph-layer-${layer.toLowerCase()}`}
                        onClick={() => {
                          setActiveLayer(active ? null : layer);
                          setActiveFile(null);
                        }}
                        style={{
                          padding: "12px 14px", borderRadius: 8,
                          border: active
                            ? `2px solid ${color}`
                            : "1px solid var(--border)",
                          background: active
                            ? `${color}18`
                            : "var(--bg-elev)",
                          cursor: "pointer", textAlign: "left",
                          transition: "all 0.15s",
                        }}
                      >
                        <div style={{ fontSize: 16, marginBottom: 4 }}>
                          {icon}
                        </div>
                        <div
                          style={{
                            fontSize: 12, fontWeight: 600,
                            color: active ? color : "var(--text)",
                          }}
                        >
                          {layer}
                        </div>
                        <div
                          style={{
                            fontSize: 10, color: "var(--text-faint)",
                            marginTop: 2,
                          }}
                        >
                          {files.length} file{files.length !== 1 ? "s" : ""}
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}

              {(search || activeLayer) && (
                <div
                  style={{
                    display: "flex", flexDirection: "column", gap: 6,
                  }}
                >
                  {displayFiles.length === 0 && (
                    <div
                      style={{
                        textAlign: "center", padding: 20,
                        fontSize: 12, color: "var(--text-faint)",
                      }}
                    >
                      No files found
                    </div>
                  )}
                  {displayFiles.map((file) => {
                    const node = nodes[file] || {};
                    const color = LAYER_COLORS[node.layer] || "#6b7280";
                    const name = file.split("/").pop();
                    const isActive = activeFile === file;
                    return (
                      <div key={file}>
                        <button
                          onClick={() =>
                            setActiveFile(isActive ? null : file)
                          }
                          style={{
                            width: "100%", textAlign: "left",
                            padding: "9px 12px", borderRadius: 7,
                            border: `1px solid ${
                              isActive ? color : "var(--border)"
                            }`,
                            background: isActive
                              ? `${color}12`
                              : "var(--bg-elev)",
                            cursor: "pointer",
                          }}
                        >
                          <div
                            style={{
                              display: "flex", alignItems: "center", gap: 8,
                            }}
                          >
                            <div
                              style={{
                                width: 7, height: 7, borderRadius: "50%",
                                background: color, flexShrink: 0,
                              }}
                            />
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div
                                style={{
                                  fontSize: 12, fontWeight: 600,
                                  color: "var(--text)",
                                  overflow: "hidden",
                                  textOverflow: "ellipsis",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {name}
                              </div>
                              <div
                                style={{
                                  fontSize: 10,
                                  color: node.description
                                    ? "var(--text-dim)"
                                    : "var(--text-faint)",
                                  marginTop: 2,
                                  fontFamily: node.description
                                    ? undefined
                                    : "monospace",
                                  overflow: "hidden",
                                  textOverflow: "ellipsis",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {node.description || file}
                              </div>
                            </div>
                          </div>
                        </button>

                        {isActive && (
                          <div
                            style={{
                              margin: "4px 0 4px 20px",
                              padding: "10px 12px",
                              background: "var(--bg-elev)",
                              borderLeft: `2px solid ${color}`,
                              borderRadius: "0 6px 6px 0",
                            }}
                          >
                            <div
                              style={{
                                fontSize: 9,
                                fontFamily: "monospace",
                                color: "var(--text-faint)",
                                marginBottom: 6,
                              }}
                            >
                              {file}
                            </div>

                            {(node.symbols || []).length > 0 && (
                              <>
                                <div
                                  style={{
                                    fontSize: 10,
                                    color: "var(--text-faint)",
                                    marginBottom: 4,
                                  }}
                                >
                                  Functions:
                                </div>
                                <div
                                  style={{
                                    display: "flex",
                                    flexWrap: "wrap",
                                    gap: 4,
                                    marginBottom: 8,
                                  }}
                                >
                                  {node.symbols.slice(0, 8).map((s) => (
                                    <span
                                      key={s}
                                      style={{
                                        fontSize: 10,
                                        fontFamily: "monospace",
                                        padding: "1px 6px",
                                        borderRadius: 4,
                                        background: `${color}20`,
                                        color,
                                      }}
                                    >
                                      {s}
                                    </span>
                                  ))}
                                </div>
                              </>
                            )}

                            <button
                              data-testid="graph-ask-ora-btn"
                              onClick={() => {
                                window.dispatchEvent(
                                  new CustomEvent("ora-inject", {
                                    detail: {
                                      text: `Tell me about ${file}${
                                        node.description
                                          ? ` (${node.description})`
                                          : ""
                                      } — how does it work and what calls it?`,
                                    },
                                  })
                                );
                                onClose();
                              }}
                              style={{
                                fontSize: 10, fontWeight: 600,
                                padding: "4px 12px", borderRadius: 6,
                                border: `1px solid ${color}`,
                                background: "transparent",
                                color, cursor: "pointer",
                                width: "100%",
                              }}
                            >
                              Ask ORA about this file →
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {!search && !activeLayer && (
                <div
                  style={{
                    textAlign: "center", padding: 24,
                    color: "var(--text-faint)",
                  }}
                >
                  <div style={{ fontSize: 24, marginBottom: 8 }}>🗺️</div>
                  <div style={{ fontSize: 12 }}>
                    Click a layer to explore files
                  </div>
                  <div
                    style={{
                      fontSize: 10, marginTop: 4,
                      color: "var(--text-faint)",
                    }}
                  >
                    or search by file name or description
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {status === "ready" && graph && (
          <div
            style={{
              padding: "8px 16px",
              borderTop: "1px solid var(--border)",
              fontSize: 10,
              color: "var(--text-faint)",
              display: "flex", justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>
              {graph.llm_files || 0} files described by AI ·{" "}
              {(graph.edges || []).length} connections
            </span>
            <span>
              Built{" "}
              {Math.max(
                0,
                Math.round(
                  (Date.now() / 1000 - (graph.built_at || 0)) / 60
                )
              )}
              m ago
            </span>
          </div>
        )}
      </div>
    </>
  );
}
