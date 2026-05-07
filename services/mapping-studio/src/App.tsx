import { useState, useEffect, useCallback, useRef } from "react";
import type { SpecifySchema, PathOutline, MappingStore, MappingEdge, OracleNodeData, SpecifyNodeData, ValueIndexBundle } from "./types";
import { fetchSpecifySchema, fetchOraclePathOutline, fetchValueIndexBundle } from "./api";
import { performAutoMap } from "./automap-logic";
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

  const [rfNodes, setRfNodes] = useState<Node[]>([]);
  const [rfEdges, setRfEdges] = useState<Edge[]>([]);

  const importRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setSchemaState("loading");
    fetchSpecifySchema()
      .then((s) => { setSchema(s); setSchemaState("ready"); })
      .catch((e) => { setSchemaErr(String(e)); setSchemaState("error"); });
  }, []);

  useEffect(() => {
    if (!resultId) return;
    setOutlineState("loading");
    fetchOraclePathOutline(resultId)
      .then((o) => { setOracleOutline(o); setOutlineState("ready"); })
      .catch((e) => { setOutlineErr(String(e)); setOutlineState("error"); });

    const catalog = new URLSearchParams(window.location.search).get("catalog") ?? "";
    setStore(loadStore(resultId, catalog));
  }, [resultId]);

  useEffect(() => {
    const nodeIds = new Set(rfNodes.map((n) => n.id));
    const newRfEdges: Edge[] = [];

    for (const mapping of store.edges) {
      const source = `oracle::${mapping.oracle_path}`;
      const target = `specify::${mapping.specify_table}.${mapping.specify_column}`;

      if (nodeIds.has(source) && nodeIds.has(target)) {
        newRfEdges.push({
          id: `edge-${source}-${target}`,
          source,
          target,
          label: mapping.transform,
          animated: mapping.transform === "direct",
          style: { stroke: transformColor(mapping.transform), strokeWidth: 2 },
          labelStyle: { fill: "#e2e8f0", fontSize: 10 },
          labelBgStyle: { fill: "#1e2530", fillOpacity: 0.9 },
        });
      }
    }
    setRfEdges(newRfEdges);
  }, [store.edges, rfNodes]);

  useEffect(() => {
    if (store.edges.length > 0 || store.result_id) saveStore(store);
  }, [store]);

  const handleCanvasEdgesChange = useCallback(
    (edges: Edge[]) => { setRfEdges(edges); },
    [],
  );

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

  const onMappingConfirmed = useCallback(
    (edge: Omit<MappingEdge, "id" | "created_at">) => {
      setStore((s) => addEdge(s, edge));
    },
    [],
  );

  const addMappingToCanvas = useCallback((mapping: MappingEdge) => {
    addOracleNode({
      label: mapping.oracle_path,
      oracle_path: mapping.oracle_path,
      examples: [], 
      leaf_count: 0,
    });
    const col = schema?.tables[mapping.specify_table]?.columns.find(c => c.name === mapping.specify_column);
    addSpecifyNode({
      label: `${mapping.specify_table}.${mapping.specify_column}`,
      specify_table: mapping.specify_table,
      specify_column: mapping.specify_column,
      col_type: col?.type ?? "unknown",
      nullable: col?.nullable ?? true,
    });
  }, [addOracleNode, addSpecifyNode, schema]);

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

  const handleAutoMap = async () => {
    if (!resultId) return;
  const [theme, setTheme] = useState<"light" | "dark">(() => 
    (localStorage.getItem("theme") as "light" | "dark") || "dark"
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  const toggleTheme = () => setTheme(t => t === "dark" ? "light" : "dark");

  return (
    <div style={styles.root}>
      <header style={styles.topbar}>
        <span style={styles.brand}>Mapping Studio</span>
        <div style={styles.resultPicker}>
          <span style={{ color: "var(--text-muted)", marginRight: 6 }}>Result ID:</span>
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
          <button 
            style={{ ...styles.btn, background: "transparent", border: "1px solid var(--border)", color: "var(--text-main)" }} 
            onClick={toggleTheme}
            title="Toggle Light/Dark Mode"
          >
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
          <button style={{ ...styles.btn, background: "var(--success)" }} onClick={handleAutoMap} title="Auto-map fields with identical values">
            Auto-map
          </button>
          <button style={styles.btn} onClick={() => exportJSON(store)} title="Export mappings JSON">
            Export JSON
          </button>
          <button style={{ ...styles.btn, background: "var(--text-dim)" }}
            onClick={() => importRef.current?.click()} title="Import mappings JSON">
            Import JSON
          </button>
          <input ref={importRef} type="file" accept=".json" style={{ display: "none" }} onChange={handleImport} />
          <span style={{ color: "var(--text-muted)", fontSize: 12, marginLeft: 8 }}>
            {store.edges.length} mapping{store.edges.length !== 1 ? "s" : ""}
          </span>
        </div>
      </header>

      {(schemaState === "error" || outlineState === "error") && (
        <div style={styles.errBanner}>
          {schemaErr && <span>Schema: {schemaErr} </span>}
          {outlineErr && <span>Outline: {outlineErr}</span>}
        </div>
      )}

      <div style={styles.panels}>
        {/* Left: Oracle path explorer */}
        <aside style={{ ...styles.sidePanel, borderRight: "1px solid var(--border)" }}>
          {!resultId && <Placeholder text="Load a result ID to explore Oracle paths" />}
          {resultId && outlineState === "loading" && <Spinner label="Loading Oracle outline…" />}
          {outlineState === "ready" && oracleOutline && (
            <OracleExplorer
              outline={oracleOutline}
              mappings={store.edges}
              onAddNode={addOracleNode}
              onShowMapping={addMappingToCanvas}
              onRemoveMapping={onRemoveEdge}
            />
          )}
        </aside>

        <main style={styles.canvasPanel}>
          <MappingCanvas
            nodes={rfNodes}
            edges={rfEdges}
            store={store}
            onNodesChange={setRfNodes}
            onEdgesChange={handleCanvasEdgesChange}
            onMappingConfirmed={onMappingConfirmed}
            onRemoveEdge={onRemoveEdge}
            theme={theme}
          />
        </main>

        <aside style={{ ...styles.sidePanel, borderLeft: "1px solid var(--border)", borderRight: "none" }}>
          {schemaState === "loading" && <Spinner label="Loading schema…" />}
          {schemaState === "ready" && schema && (
            <SchemaOutline
              schema={schema}
              mappings={store.edges}
              onAddNode={addSpecifyNode}
              onShowMapping={addMappingToCanvas}
              onRemoveMapping={onRemoveEdge}
            />
          )}
          {schemaState === "idle" && <Placeholder text="Specify Schema" />}
        </aside>
      </div>

      {schema && (
        <footer style={styles.footer}>
          <CoverageMatrix schema={schema} mappings={store.edges} />
        </footer>
      )}
    </div>
  );
}

function Spinner({ label }: { label: string }) {
  return <div style={{ color: "var(--text-muted)", padding: 20, textAlign: "center" }}>{label}</div>;
}

function Placeholder({ text }: { text: string }) {
  return (
    <div style={{ color: "var(--text-dim)", padding: 24, textAlign: "center", fontSize: 13, lineHeight: 1.7 }}>
      {text}
    </div>
  );
}

const styles = {
  root: {
    display: "flex",
    flexDirection: "column" as const,
    height: "100vh",
    background: "var(--bg-root)",
    color: "var(--text-main)",
    overflow: "hidden",
  },
  topbar: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "0 16px",
    height: 48,
    background: "var(--bg-panel)",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
    flexWrap: "wrap" as const,
  },
  brand: {
    fontWeight: 700,
    fontSize: 15,
    color: "var(--accent)",
    letterSpacing: "0.02em",
    marginRight: 8,
  },
  resultPicker: { display: "flex", alignItems: "center", gap: 6 },
  topActions: { display: "flex", alignItems: "center", gap: 8, marginLeft: "auto" },
  input: {
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    borderRadius: 4,
    color: "var(--text-main)",
    padding: "3px 8px",
    fontSize: 12,
    width: 260,
    fontFamily: "monospace",
  },
  btn: {
    background: "var(--accent)",
    color: "#fff",
    border: "none",
    borderRadius: 4,
    padding: "4px 12px",
    cursor: "pointer",
    fontSize: 12,
    fontWeight: 600,
  },
  errBanner: {
    background: "var(--error-bg)",
    color: "var(--error-text)",
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
    background: "var(--bg-panel)",
    borderRight: "1px solid var(--border)",
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
    background: "var(--bg-panel)",
    borderTop: "1px solid var(--border)",
    overflowX: "auto" as const,
    overflowY: "hidden" as const,
  },
};

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
