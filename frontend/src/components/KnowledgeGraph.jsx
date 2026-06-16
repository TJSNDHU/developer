/**
 * components/KnowledgeGraph.jsx — Iter 167
 *
 * Interactive React Flow graph for the project codebase graph.
 *  • Nodes = files, colored by detected layer (API/Service/UI/Data/...)
 *  • Edges = import dependencies
 *  • Live signal: nodes glow + edges animate when ORA is editing them
 *    (driven by the `liveFiles` prop owned by GraphPanel)
 *  • Layer filter pills, search dim-out, pan, zoom, minimap
 */
import React, { useEffect, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
  Panel,
  getBezierPath,
  BaseEdge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

// Bright vivid colors — wow factor
const LAYER_COLORS = {
  API:     { bg: "#f59e0b", glow: "#f59e0b66", text: "#000" },
  Service: { bg: "#818cf8", glow: "#818cf866", text: "#fff" },
  Data:    { bg: "#34d399", glow: "#34d39966", text: "#000" },
  UI:      { bg: "#60a5fa", glow: "#60a5fa66", text: "#000" },
  Hook:    { bg: "#c084fc", glow: "#c084fc66", text: "#fff" },
  Util:    { bg: "#94a3b8", glow: "#94a3b866", text: "#000" },
  Config:  { bg: "#fb7185", glow: "#fb718566", text: "#fff" },
  Test:    { bg: "#475569", glow: "#47556966", text: "#fff" },
  Other:   { bg: "#64748b", glow: "#64748b66", text: "#fff" },
};

// Custom node renderer
function FileNode({ data, selected }) {
  const c = LAYER_COLORS[data.layer] || LAYER_COLORS.Other;
  const isLive = data.isLive;
  return (
    <div
      data-testid={`node-${data.layer}`}
      style={{
        padding: "8px 14px",
        borderRadius: 10,
        border: `2px solid ${selected ? "#fff" : c.bg}`,
        background: isLive ? c.bg : `${c.bg}22`,
        color: isLive ? c.text : "#f1f5f9",
        minWidth: 130,
        maxWidth: 190,
        boxShadow: isLive
          ? `0 0 20px ${c.glow}, 0 0 40px ${c.glow}`
          : selected
            ? `0 0 16px ${c.glow}`
            : "0 2px 8px rgba(0,0,0,0.4)",
        transition: "all 0.2s ease",
        position: "relative",
        backdropFilter: "blur(4px)",
      }}
    >
      {isLive && (
        <div
          style={{
            position: "absolute",
            top: -4, right: -4,
            width: 10, height: 10,
            borderRadius: "50%",
            background: "#22c55e",
            boxShadow: "0 0 8px #22c55e",
            animation: "live-pulse 1s infinite",
          }}
        />
      )}
      <div
        style={{
          fontSize: 9,
          color: c.bg,
          fontWeight: 700,
          letterSpacing: "0.08em",
          marginBottom: 3,
          textTransform: "uppercase",
          fontFamily: "monospace",
        }}
      >
        {data.layer}
      </div>
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          color: isLive ? c.text : "#f8fafc",
        }}
      >
        {data.label}
      </div>
      {data.description && (
        <div
          style={{
            fontSize: 9,
            color: isLive ? `${c.text}cc` : "#94a3b8",
            marginTop: 3,
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            lineHeight: 1.3,
          }}
        >
          {data.description}
        </div>
      )}
      {data.symbolCount > 0 && (
        <div
          style={{
            fontSize: 9,
            color: c.bg,
            marginTop: 4,
            fontFamily: "monospace",
          }}
        >
          ƒ {data.symbolCount} functions
        </div>
      )}
    </div>
  );
}

// Animated edge with a particle dot tracing the path
function AnimatedEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, data, style, markerEnd,
}) {
  const [edgePath] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });
  return (
    <>
      <BaseEdge id={id} path={edgePath} style={style} markerEnd={markerEnd} />
      {data?.animated && (
        <circle r="4" fill={data.color || "#22c55e"}>
          <animateMotion dur="2s" repeatCount="indefinite">
            <mpath href={`#${id}`} />
          </animateMotion>
        </circle>
      )}
    </>
  );
}

const nodeTypes = { file: FileNode };
const edgeTypes = { animated: AnimatedEdge };

// Layout: group files by layer in columns. Pure deterministic.
function layoutNodes(graphNodes, graphLayers) {
  const COL_W = 230;
  const ROW_H = 110;
  const COLS = 3;
  const layerOrder = [
    "Config", "API", "Service", "Data",
    "UI", "Hook", "Util", "Test", "Other",
  ];
  const positions = {};
  let colOffset = 0;
  for (const layer of layerOrder) {
    const files = (graphLayers[layer] || []).filter((f) => graphNodes[f]);
    if (!files.length) continue;
    files.forEach((path, idx) => {
      positions[path] = {
        x: (colOffset + (idx % COLS)) * COL_W,
        y: Math.floor(idx / COLS) * ROW_H,
      };
    });
    colOffset += COLS + 1; // gap between layer columns
  }
  return positions;
}

export default function KnowledgeGraph({
  graph,
  onNodeClick,
  searchQuery = "",
  liveFiles = [],
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [activeLayer, setActiveLayer] = useState(null);

  useEffect(() => {
    if (!graph?.nodes) return;

    const positions = layoutNodes(graph.nodes, graph.layers || {});
    const search = (searchQuery || "").toLowerCase();
    const liveSet = new Set(liveFiles || []);

    const rfNodes = Object.entries(graph.nodes).map(([path, node]) => {
      const name = path.split("/").pop();
      const pos = positions[path] || { x: 0, y: 0 };
      const isLive = liveSet.has(path);
      const matchSearch = search
        ? name.toLowerCase().includes(search) ||
          (node.description || "").toLowerCase().includes(search)
        : true;
      const matchLayer = activeLayer ? node.layer === activeLayer : true;
      return {
        id: path,
        type: "file",
        position: pos,
        data: {
          label: name,
          layer: node.layer,
          description: node.description || "",
          symbols: node.symbols || [],
          symbolCount: (node.symbols || []).length,
          path,
          isLive,
        },
        style: {
          opacity: (matchSearch && matchLayer) ? 1 : 0.08,
          zIndex: isLive ? 10 : 1,
        },
      };
    });

    const rfEdges = (graph.edges || []).slice(0, 300).map((e, i) => {
      const fromNode = graph.nodes[e.from];
      const c = LAYER_COLORS[fromNode?.layer || "Other"];
      const isLiveEdge = liveSet.has(e.from) || liveSet.has(e.to);
      const matchLayer = activeLayer
        ? fromNode?.layer === activeLayer
        : true;
      return {
        id: `e${i}`,
        source: e.from,
        target: e.to,
        type: isLiveEdge ? "animated" : "default",
        data: { animated: isLiveEdge, color: c?.bg },
        style: {
          stroke: isLiveEdge ? "#22c55e" : c?.bg || "#6b7280",
          strokeWidth: isLiveEdge ? 2 : 1,
          opacity: matchLayer ? (isLiveEdge ? 1 : 0.35) : 0.04,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: isLiveEdge ? "#22c55e" : c?.bg || "#6b7280",
          width: isLiveEdge ? 12 : 8,
          height: isLiveEdge ? 12 : 8,
        },
      };
    });

    setNodes(rfNodes);
    setEdges(rfEdges);
  }, [graph, searchQuery, activeLayer, liveFiles, setNodes, setEdges]);

  const layers = Object.keys(graph?.layers || {}).filter(
    (l) => (graph.layers[l] || []).length > 0
  );

  return (
    <div
      data-testid="knowledge-graph"
      style={{ width: "100%", height: "100%", position: "relative" }}
    >
      {/* Layer filter pills */}
      <div
        data-testid="knowledge-graph-layer-filter"
        style={{
          position: "absolute",
          top: 8, left: 8,
          zIndex: 10,
          display: "flex",
          flexWrap: "wrap",
          gap: 4,
          maxWidth: "70%",
        }}
      >
        <button
          data-testid="knowledge-graph-layer-all"
          onClick={() => setActiveLayer(null)}
          style={{
            padding: "3px 10px",
            borderRadius: 12,
            border: "1px solid #ffffff22",
            background: !activeLayer ? "rgba(255,255,255,0.12)" : "transparent",
            color: !activeLayer ? "#fff" : "#94a3b8",
            cursor: "pointer",
            fontSize: 10,
            fontWeight: !activeLayer ? 700 : 400,
          }}
        >
          All
        </button>
        {layers.map((layer) => {
          const c = LAYER_COLORS[layer]?.bg || "#6b7280";
          const active = activeLayer === layer;
          return (
            <button
              key={layer}
              data-testid={`knowledge-graph-layer-${layer.toLowerCase()}`}
              onClick={() => setActiveLayer(active ? null : layer)}
              style={{
                padding: "3px 10px",
                borderRadius: 12,
                border: `1px solid ${c}66`,
                background: active ? `${c}33` : "transparent",
                color: active ? c : "#94a3b8",
                cursor: "pointer",
                fontSize: 10,
                fontWeight: active ? 700 : 400,
                boxShadow: active ? `0 0 8px ${c}44` : "none",
              }}
            >
              {layer}
              <span style={{ marginLeft: 4, fontSize: 9, opacity: 0.7 }}>
                {(graph.layers[layer] || []).length}
              </span>
            </button>
          );
        })}
      </div>

      {/* Live indicator */}
      {liveFiles.length > 0 && (
        <div
          data-testid="knowledge-graph-live-indicator"
          style={{
            position: "absolute",
            top: 8, right: 60,
            zIndex: 10,
            display: "flex",
            alignItems: "center",
            gap: 6,
            background: "rgba(34,197,94,0.12)",
            border: "1px solid #22c55e44",
            borderRadius: 12,
            padding: "4px 10px",
          }}
        >
          <div
            style={{
              width: 7, height: 7,
              borderRadius: "50%",
              background: "#22c55e",
              boxShadow: "0 0 8px #22c55e",
              animation: "live-pulse 1s infinite",
            }}
          />
          <span style={{ fontSize: 10, color: "#22c55e", fontWeight: 600 }}>
            ORA editing {liveFiles.length} file{liveFiles.length > 1 ? "s" : ""}
          </span>
        </div>
      )}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={(_, n) => onNodeClick?.(n.data)}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.04}
        maxZoom={2.5}
        style={{ background: "#080c14" }}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant="dots" color="#ffffff08" gap={24} size={1} />
        <Controls
          style={{
            background: "#0f172a",
            border: "1px solid #1e293b",
            borderRadius: 8,
          }}
        />
        <MiniMap
          style={{ background: "#0f172a", border: "1px solid #1e293b" }}
          nodeColor={(n) =>
            LAYER_COLORS[n.data?.layer]?.bg || "#6b7280"
          }
          maskColor="rgba(0,0,0,0.7)"
          zoomable
          pannable
        />
        <Panel position="bottom-center">
          <div
            style={{
              fontSize: 10,
              color: "#64748b",
              background: "#0f172a",
              padding: "4px 12px",
              borderRadius: 12,
              border: "1px solid #1e293b",
            }}
          >
            {Object.keys(graph?.nodes || {}).length} files ·{" "}
            {(graph?.edges || []).length} connections · scroll to zoom · drag to pan · click to explore
          </div>
        </Panel>
      </ReactFlow>

      <style>{`
        @keyframes live-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%      { opacity: 0.5; transform: scale(1.4); }
        }
      `}</style>
    </div>
  );
}
