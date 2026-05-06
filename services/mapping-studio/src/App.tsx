import { useState, useEffect, useCallback, useRef } from "react";
import type { SpecifySchema, PathOutline, MappingStore, MappingEdge, OracleNodeData, SpecifyNodeData } from "./types";
import { fetchSpecifySchema, fetchOraclePathOutline } from "./api";
import { loadStore, saveStore, emptyStore, addEdge, removeEdge, exportJSON, importJSON } from "./store";
import SchemaOutline from "./components/SchemaOutline";
import OracleExplorer from "./components/OracleExplorer";
import MappingCanvas from "./components/MappingCanvas";
import CoverageMatrix from "./components/CoverageMatrix";

import type { Node, Edge } from "@xyflow/react";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getResultIdFromUrl(): string {
  const p = new URLSearchParams(window.location.search);
  return p.get("result") ?? "";
}

type LoadState = "idle" | "loading" | "ready" | "error";

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [resultId, setResultId] = useState(getResultIdFromUrl);
  const [pendingId, setPendingId] = useState(getResultIdFromUrl);

  const [schema, setSchema] = useState<SpecifySchema | null>(null);
  const [oracleOutline, setOracleOutline] = useState<PathOutline | null>(null);
  const [schemaState, setSchemaState] = useState<LoadState>("idle");
  const [outlineState, setOutlineState] = useState<LoadState>("idle");
  const [schemaErr, setSchemaErr] = useState("");
  const [outlineErr, setOutlineErr] = useState("");

  const [store, setStore] = useState<MappingStore>(() =>
    emptyStore(getResultIdFromUrl(), ""),
  );

  // React Flow node/edge state lifted here so canvas + panels share it.
  const [rfNodes, setRfNodes] = useState<Node[]>([]);
  const [rfEdges, setRfEdges] = useState<Edge[]>([]);

  const importRef = useRef<HTMLInputElement>(null);

  // Load Specify schema on mount (independent of result id).
  useEffect(() => {
    setSchemaState("loading");
    fetchSpecifySchema()
      .then((s) => { setSchema(s); setSchemaState("ready"); })
      .catch((e) => { setSchemaErr(String(e)); setSchemaState("error"); });
  }, []);

  // Load Oracle outline whenever result ID changes.
  useEffect(() => {
    if (!resultId) return;
    setOutlineState("loading");
    fetchOraclePathOutline(resultId)
      .then((o) => { setOracleOutline(o); setOutlineState("ready"); })
      .catch((e) => { setOutlineErr(String(e)); setOutlineState("error"); });

    const catalog = new URLSearchParams(window.location.search).get("catalog") ?? "";
    setStore(loadStore(resultId, catalog));
  }, [resultId]);

  // Persist store whenever it changes.
  useEffect(() => {
    if (store.edges.length > 0 || store.result_id) saveStore(store);
  }, [store]);

  // Sync rfEdges → store (called by canvas when edges change).
  const handleCanvasEdgesChange = useCallback(
    (edges: Edge[]) => { setRfEdges(edges); },
    [],
  );

  // Add Oracle node from right panel click.
  const addOracleNode = useCallback((data: OracleNodeData) => {
    setRfNodes((prev) => {
      if (prev.some((n) => n.id === `oracle::${data.oracle_path}`)) return prev;
      const x = 50;
      const y = 60 + prev.filter((n) => n.type === "oracleNode").length * 80;
      return [
        ...prev,
        {
          id: `oracle::${data.oracle_path}`,
          type: "oracleNode",
          position: { x, y },
          data,
        },
      ];
    });
  }, []);

  // Add Specify node from left panel click.
  const addSpecifyNode = useCallback((data: SpecifyNodeData) => {
    setRfNodes((prev) => {
      if (prev.some((n) => n.id === `specify::${data.specify_table}.${data.specify_column}`)) return prev;
      const x = 620;
      const y = 60 + prev.filter((n) => n.type === "specifyNode").length * 80;
      return [
        ...prev,
        {
          id: `specify::${data.specify_table}.${data.specify_column}`,
          type: "specifyNode",
          position: { x, y },
          data,
        },
      ];
    });
  }, []);

  // Called when user confirms a mapping edge in the canvas.
  const onMappingConfirmed = useCallback(
    (edge: Omit<MappingEdge, "id" | "created_at">) => {
      setStore((s) => addEdge(s, edge));
    },
    [],
  );

  const onRemoveEdge = useCallback((edgeId: string) => {
    setStore((s) => removeEdge(s, edgeId));
  }, []);

  const handleLoadResult = () => {
    const id = pendingId.trim();
    if (!id) return;
    setResultId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("result", id);
    window.history.replaceState(null, "", url.toString());
  };

  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    importJSON(file, (s) => { setStore(s); }, alert);
    e.target.value = "";
  };

  return (
    <div style={styles.root}>
      {/* Top bar */}
      <header style={styles.topbar}>
        <span style={styles.brand}>Mapping Studio</span>
        <div style={styles.resultPicker}>
          <span style={{ color: "#94a3b8", marginRight: 6 }}>Result ID:</span>
          <input
            style={styles.input}
            value={pendingId}
            onChange={(e) => setPendingId(e.target.value)}
            placeholder="paste result id…"
            onKeyDown={(e) => e.key === "Enter" && handleLoadResult()}
          />
          <button style={styles.btn} onClick={handleLoadResult}>Load</button>
        </div>
        <div style={styles.topActions}>
          <button style={styles.btn} onClick={() => exportJSON(store)} title="Export mappings JSON">
            Export JSON
          </button>
          <button style={{ ...styles.btn, background: "#374151" }}
            onClick={() => importRef.current?.click()} title="Import mappings JSON">
            Import JSON
          </button>
          <input ref={importRef} type="file" accept=".json" style={{ display: "none" }} onChange={handleImport} />
          <span style={{ color: "#64748b", fontSize: 12, marginLeft: 8 }}>
            {store.edges.length} mapping{store.edges.length !== 1 ? "s" : ""}
          </span>
        </div>
      </header>

      {/* Status bar */}
      {(schemaState === "error" || outlineState === "error") && (
        <div style={styles.errBanner}>
          {schemaErr && <span>Schema: {schemaErr} </span>}
          {outlineErr && <span>Outline: {outlineErr}</span>}
        </div>
      )}

      {/* Main panels */}
      <div style={styles.panels}>
        {/* Left: Specify schema outline */}
        <aside style={styles.sidePanel}>
          {schemaState === "loading" && <Spinner label="Loading schema…" />}
          {schemaState === "ready" && schema && (
            <SchemaOutline
              schema={schema}
              mappings={store.edges}
              onAddNode={addSpecifyNode}
            />
          )}
          {schemaState === "idle" && <Placeholder text="Specify Schema" />}
        </aside>

        {/* Center: Mapping canvas */}
        <main style={styles.canvasPanel}>
          <MappingCanvas
            nodes={rfNodes}
            edges={rfEdges}
            store={store}
            onNodesChange={setRfNodes}
            onEdgesChange={handleCanvasEdgesChange}
            onMappingConfirmed={onMappingConfirmed}
            onRemoveEdge={onRemoveEdge}
          />
        </main>

        {/* Right: Oracle path explorer */}
        <aside style={styles.sidePanel}>
          {!resultId && <Placeholder text="Load a result ID to explore Oracle paths" />}
          {resultId && outlineState === "loading" && <Spinner label="Loading Oracle outline…" />}
          {outlineState === "ready" && oracleOutline && (
            <OracleExplorer
              outline={oracleOutline}
              mappings={store.edges}
              onAddNode={addOracleNode}
            />
          )}
        </aside>
      </div>

      {/* Bottom: Coverage matrix */}
      {schema && (
        <footer style={styles.footer}>
          <CoverageMatrix schema={schema} mappings={store.edges} />
        </footer>
      )}
    </div>
  );
}

function Spinner({ label }: { label: string }) {
  return <div style={{ color: "#94a3b8", padding: 20, textAlign: "center" }}>{label}</div>;
}

function Placeholder({ text }: { text: string }) {
  return (
    <div style={{ color: "#4b5563", padding: 24, textAlign: "center", fontSize: 13, lineHeight: 1.7 }}>
      {text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline styles (no CSS modules needed for a tool this size)
// ---------------------------------------------------------------------------
const styles = {
  root: {
    display: "flex",
    flexDirection: "column" as const,
    height: "100vh",
    background: "#0f1117",
    color: "#e2e8f0",
    overflow: "hidden",
  },
  topbar: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "0 16px",
    height: 48,
    background: "#161b22",
    borderBottom: "1px solid #21262d",
    flexShrink: 0,
    flexWrap: "wrap" as const,
  },
  brand: {
    fontWeight: 700,
    fontSize: 15,
    color: "#60a5fa",
    letterSpacing: "0.02em",
    marginRight: 8,
  },
  resultPicker: { display: "flex", alignItems: "center", gap: 6 },
  topActions: { display: "flex", alignItems: "center", gap: 8, marginLeft: "auto" },
  input: {
    background: "#0f1117",
    border: "1px solid #30363d",
    borderRadius: 4,
    color: "#e2e8f0",
    padding: "3px 8px",
    fontSize: 12,
    width: 260,
    fontFamily: "monospace",
  },
  btn: {
    background: "#1d4ed8",
    color: "#fff",
    border: "none",
    borderRadius: 4,
    padding: "4px 12px",
    cursor: "pointer",
    fontSize: 12,
    fontWeight: 600,
  },
  errBanner: {
    background: "#450a0a",
    color: "#fca5a5",
    padding: "4px 16px",
    fontSize: 12,
    flexShrink: 0,
  },
  panels: {
    display: "flex",
    flex: 1,
    overflow: "hidden",
    minHeight: 0,
  },
  sidePanel: {
    width: 300,
    minWidth: 240,
    flexShrink: 0,
    background: "#161b22",
    borderRight: "1px solid #21262d",
    overflowY: "auto" as const,
    overflowX: "hidden" as const,
  },
  canvasPanel: {
    flex: 1,
    minWidth: 0,
    position: "relative" as const,
  },
  footer: {
    height: 120,
    flexShrink: 0,
    background: "#161b22",
    borderTop: "1px solid #21262d",
    overflowX: "auto" as const,
    overflowY: "hidden" as const,
  },
};
