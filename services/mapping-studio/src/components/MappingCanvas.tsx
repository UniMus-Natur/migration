import { useCallback, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
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

import type { MappingEdge, OracleNodeData, SpecifyNodeData, TransformKind } from "../types";

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

const nodeTypes = { oracleNode: OracleNode, specifyNode: SpecifyNode };

// ---------------------------------------------------------------------------
// Transform label dialog
// ---------------------------------------------------------------------------
interface DialogState {
  connection: Connection;
}

const TRANSFORMS: TransformKind[] = ["direct", "concat", "lookup", "derived", "constant", "split", "custom"];

function TransformDialog({
  state,
  onConfirm,
  onCancel,
}: {
  state: DialogState;
  onConfirm: (transform: TransformKind, note: string) => void;
  onCancel: () => void;
}) {
  const [transform, setTransform] = useState<TransformKind>("direct");
  const [note, setNote] = useState("");

  return (
    <div style={ds.overlay}>
      <div style={ds.dialog}>
        <h3 style={ds.title}>Define mapping</h3>
        <p style={ds.sub}>
          <b>{state.connection.source?.replace("oracle::", "")}</b>
          <br />→ <b>{state.connection.target?.replace("specify::", "")}</b>
        </p>
        <label style={ds.label}>Transform</label>
        <select
          style={ds.select}
          value={transform}
          onChange={(e) => setTransform(e.target.value as TransformKind)}
        >
          {TRANSFORMS.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <label style={ds.label}>Note (optional)</label>
        <textarea
          style={ds.textarea}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Any migration note, rule, or transform expression…"
          rows={3}
        />
        <div style={ds.btns}>
          <button style={ds.cancel} onClick={onCancel}>Cancel</button>
          <button style={ds.confirm} onClick={() => onConfirm(transform, note)}>
            Save Mapping
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main canvas
// ---------------------------------------------------------------------------
interface Props {
  nodes: Node[];
  edges: Edge[];
  store: { edges: MappingEdge[] };
  onNodesChange: (nodes: Node[]) => void;
  onEdgesChange: (edges: Edge[]) => void;
  onMappingConfirmed: (edge: Omit<MappingEdge, "id" | "created_at">) => void;
  onRemoveEdge: (id: string) => void;
}

export default function MappingCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onMappingConfirmed,
  onRemoveEdge,
}: Props) {
  const [dialog, setDialog] = useState<DialogState | null>(null);

  const handleNodesChange: OnNodesChange = useCallback(
    (changes) => onNodesChange(applyNodeChanges(changes, nodes)),
    [nodes, onNodesChange],
  );

  const handleEdgesChange: OnEdgesChange = useCallback(
    (changes) => {
      const next = applyEdgeChanges(changes, edges);
      // Detect removed edges and notify store.
      const removedIds = new Set(edges.map((e) => e.id));
      next.forEach((e) => removedIds.delete(e.id));
      removedIds.forEach((id) => onRemoveEdge(id));
      onEdgesChange(next);
    },
    [edges, onEdgesChange, onRemoveEdge],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      // Only allow oracle → specify edges.
      if (
        !connection.source?.startsWith("oracle::") ||
        !connection.target?.startsWith("specify::")
      )
        return;
      setDialog({ connection });
    },
    [],
  );

  const confirmMapping = useCallback(
    (transform: TransformKind, note: string) => {
      if (!dialog) return;
      const { source, target } = dialog.connection;
      const oraclePath = source!.replace("oracle::", "");
      const specify = target!.replace("specify::", ""); // "table.column"
      const [specify_table, specify_column] = specify.split(".");

      // Add RF edge.
      const newEdge: Edge = {
        id: `edge-${source}-${target}`,
        source: source!,
        target: target!,
        label: transform,
        animated: transform === "direct",
        style: { stroke: transformColor(transform), strokeWidth: 2 },
        labelStyle: { fill: "#e2e8f0", fontSize: 10 },
        labelBgStyle: { fill: "#1e2530", fillOpacity: 0.9 },
      };
      onEdgesChange(addEdge(newEdge, edges));

      // Notify parent (store).
      onMappingConfirmed({
        oracle_path: oraclePath,
        specify_table,
        specify_column,
        transform,
        note,
        confirmed: true,
      });
      setDialog(null);
    },
    [dialog, edges, onEdgesChange, onMappingConfirmed],
  );

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        colorMode="dark"
        defaultEdgeOptions={{ style: { strokeWidth: 2 } }}
      >
        <Background color="#21262d" gap={16} />
        <Controls style={{ background: "#161b22", borderColor: "#30363d" }} />
        <MiniMap
          style={{ background: "#161b22", border: "1px solid #21262d" }}
          nodeColor={(n) => (n.type === "oracleNode" ? "#f59e0b" : "#3b82f6")}
        />
        <CanvasHelp nodesCount={nodes.length} />
      </ReactFlow>
      {dialog && (
        <TransformDialog
          state={dialog}
          onConfirm={confirmMapping}
          onCancel={() => setDialog(null)}
        />
      )}
    </div>
  );
}

function CanvasHelp({ nodesCount }: { nodesCount: number }) {
  if (nodesCount > 0) return null;
  return (
    <div
      style={{
        position: "absolute", top: "50%", left: "50%",
        transform: "translate(-50%,-50%)",
        color: "#4b5563", textAlign: "center", pointerEvents: "none",
        lineHeight: 1.8, fontSize: 13,
      }}
    >
      <div style={{ fontSize: 32, marginBottom: 8 }}>⬡</div>
      Click <span style={{ color: "#60a5fa" }}>+</span> on a Specify field (left)<br />
      or an Oracle path (right)<br />
      to add nodes, then draw edges to map them.
    </div>
  );
}

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
    width: 220,
    boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
  },
  specify: {
    padding: "10px 14px",
    borderRadius: 8,
    background: "var(--node-specify)",
    border: "2px solid var(--success)",
    color: "var(--text-main)",
    fontSize: 12,
    width: 220,
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
    overflow: "hidden",
    textOverflow: "ellipsis",
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

