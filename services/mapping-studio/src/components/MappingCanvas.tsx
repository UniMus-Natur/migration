import { useCallback, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
  type OnNodesChange,
  applyNodeChanges,
  applyEdgeChanges,
  type OnEdgesChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { MappingEdge, OracleNodeData, SpecifyNodeData, TransformKind, MappingStore } from "../types";

// ---------------------------------------------------------------------------
// Custom node: Oracle source
// ---------------------------------------------------------------------------
function OracleNode({ data }: NodeProps) {
  const d = data as OracleNodeData;
  return (
    <div style={ns.oracle}>
      <Handle type="source" position={Position.Right} style={ns.handle} />
      <div style={ns.label} title={d.oracle_path}>{d.label}</div>
      {d.examples.length > 0 && (
        <div style={ns.example}>{d.examples[0]}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Custom node: Specify target
// ---------------------------------------------------------------------------
function SpecifyNode({ data }: NodeProps) {
  const d = data as SpecifyNodeData;
  return (
    <div style={ns.specify}>
      <Handle type="target" position={Position.Left} style={ns.handle} />
      <div style={ns.label} title={`${d.specify_table}.${d.specify_column}`}>{d.label}</div>
      <div style={ns.meta}>{d.col_type.replace(/\(.*\)/, "")} {d.nullable ? "?" : ""}</div>
    </div>
  );
}

const nodeTypes = {
  oracleNode: OracleNode,
  specifyNode: SpecifyNode,
};

// ---------------------------------------------------------------------------
// Main canvas
// ---------------------------------------------------------------------------
interface Props {
  nodes: Node[];
  edges: Edge[];
  store: MappingStore;
  theme: "light" | "dark";
  onNodesChange: (nodes: Node[]) => void;
  onEdgesChange: (edges: Edge[]) => void;
  onMappingConfirmed: (edge: Omit<MappingEdge, "id" | "created_at">) => void;
  onRemoveEdge: (id: string) => void;
}

export default function MappingCanvas({
  nodes,
  edges,
  store,
  theme,
  onNodesChange,
  onEdgesChange,
  onMappingConfirmed,
  onRemoveEdge,
}: Props) {
  const [pendingEdge, setPendingEdge] = useState<Connection | null>(null);
  const [transform, setTransform] = useState<TransformKind>("direct");
  const [note, setNote] = useState("");

  const handleNodesChange: OnNodesChange = useCallback(
    (changes) => onNodesChange(applyNodeChanges(changes, nodes)),
    [nodes, onNodesChange],
  );

  const handleEdgesChange: OnEdgesChange = useCallback(
    (changes) => {
      const next = applyEdgeChanges(changes, edges);
      const removedIds = new Set(edges.map((e) => e.id));
      next.forEach((e) => removedIds.delete(e.id));
      removedIds.forEach((id) => onRemoveEdge(id));
      onEdgesChange(next);
    },
    [edges, onEdgesChange, onRemoveEdge],
  );

  const onConnect = useCallback((connection: Connection) => {
    setPendingEdge(connection);
    setTransform("direct");
    setNote("");
  }, []);

  const handleConfirm = () => {
    if (!pendingEdge?.source || !pendingEdge?.target) return;

    const oracle_path = pendingEdge.source.replace("oracle::", "");
    const specify_full = pendingEdge.target.replace("specify::", "");
    const [specify_table, specify_column] = specify_full.split(".");

    onMappingConfirmed({
      oracle_path,
      specify_table,
      specify_column,
      transform,
      note,
      confirmed: true,
    });
    setPendingEdge(null);
  };

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        colorMode={theme}
        fitView
      >
        <Background color="var(--border)" gap={20} />
        <Controls />
        <MiniMap 
          style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }} 
          nodeColor={(n) => n.type === "oracleNode" ? "var(--accent)" : "var(--success)"}
        />
      </ReactFlow>

      {pendingEdge && (
        <div style={ds.overlay}>
          <div style={ds.dialog}>
            <h3 style={ds.title}>Define Transform</h3>
            <p style={ds.sub}>
              {pendingEdge.source?.replace("oracle::", "")} →{" "}
              {pendingEdge.target?.replace("specify::", "")}
            </p>

            <label style={ds.label}>Transform Kind</label>
            <select
              style={ds.select}
              value={transform}
              onChange={(e) => setTransform(e.target.value as TransformKind)}
            >
              <option value="direct">Direct Copy</option>
              <option value="concat">Concatenate</option>
              <option value="lookup">Vocabulary Lookup</option>
              <option value="derived">Derived (Expression)</option>
              <option value="constant">Fixed Constant</option>
              <option value="split">Split Source</option>
              <option value="custom">Custom Logic</option>
            </select>

            <label style={ds.label}>Note / Rationale</label>
            <textarea
              style={ds.textarea}
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. why this mapping is correct..."
            />

            <div style={ds.btns}>
              <button style={ds.cancel} onClick={() => setPendingEdge(null)}>
                Cancel
              </button>
              <button style={ds.confirm} onClick={handleConfirm}>
                Confirm Mapping
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper: Transform Colors
// ---------------------------------------------------------------------------
function transformColor(t: string): string {
  const m: Record<string, string> = {
    direct: "#22c55e",
    concat: "#f59e0b",
    lookup: "#818cf8",
    derived: "#ec4899",
    constant: "#94a3b8",
    split: "#06b6d4",
    custom: "#f97316",
  };
  return m[t] ?? "#94a3b8";
}

// ---------------------------------------------------------------------------
// Styles for Nodes & Dialog
// ---------------------------------------------------------------------------
const ns = {
  oracle: {
    padding: "10px 14px",
    borderRadius: 8,
    background: "var(--node-oracle)",
    border: "2px solid var(--accent)",
    color: "var(--text-main)",
    fontSize: 12,
    minWidth: 220,
    maxWidth: 800,
    width: "max-content",
    boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
  },
  specify: {
    padding: "10px 14px",
    borderRadius: 8,
    background: "var(--node-specify)",
    border: "2px solid var(--success)",
    color: "var(--text-main)",
    fontSize: 12,
    minWidth: 220,
    maxWidth: 800,
    width: "max-content",
    boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
  },
  handle: {
    background: "var(--accent)",
    width: 8,
    height: 8,
    border: "2px solid var(--bg-root)",
  },
  label: {
    fontWeight: 700,
    whiteSpace: "nowrap" as const,
    marginBottom: 4,
  },
  example: {
    fontSize: 10,
    color: "var(--text-muted)",
    fontStyle: "italic",
    borderTop: "1px solid var(--border)",
    marginTop: 6,
    paddingTop: 4,
  },
  meta: {
    fontSize: 10,
    color: "var(--text-muted)",
    background: "var(--bg-root)",
    padding: "2px 6px",
    borderRadius: 4,
    display: "inline-block",
  },
};

const ds = {
  overlay: {
    position: "fixed" as const,
    inset: 0,
    background: "rgba(0,0,0,0.7)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 9999,
  },
  dialog: {
    background: "var(--bg-panel)",
    border: "1px solid var(--border)",
    borderRadius: 10,
    padding: 24,
    width: 400,
    maxWidth: "90vw",
    boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
  },
  title: { margin: "0 0 8px", color: "var(--accent)", fontSize: 16 },
  sub: { color: "var(--text-muted)", fontSize: 12, lineHeight: 1.7, margin: "0 0 16px", fontFamily: "monospace" },
  label: { display: "block", color: "var(--text-muted)", fontSize: 12, marginBottom: 4, marginTop: 12 },
  select: {
    width: "100%",
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    borderRadius: 4,
    color: "var(--text-main)",
    padding: "6px 8px",
    fontSize: 13,
  },
  textarea: {
    width: "100%",
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    borderRadius: 4,
    color: "var(--text-main)",
    padding: "6px 8px",
    fontSize: 12,
    resize: "vertical" as const,
    fontFamily: "system-ui, sans-serif",
  },
  btns: { display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 20 },
  cancel: {
    background: "transparent",
    border: "1px solid var(--border)",
    color: "var(--text-muted)",
    borderRadius: 5,
    padding: "7px 18px",
    cursor: "pointer",
    fontSize: 13,
  },
  confirm: {
    background: "var(--accent)",
    border: "none",
    color: "#fff",
    borderRadius: 5,
    padding: "7px 18px",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 600,
  },
};
