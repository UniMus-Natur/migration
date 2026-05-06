import { useState, useMemo } from "react";
import type { SpecifySchema, SpecifyColumn, MappingEdge, SpecifyNodeData } from "../types";
import { buildCoverageMap } from "../store";

interface Props {
  schema: SpecifySchema;
  mappings: MappingEdge[];
  onAddNode: (data: SpecifyNodeData) => void;
  onShowMapping: (mapping: MappingEdge) => void;
  onRemoveMapping: (id: string) => void;
}

const ROOT_TABLE = "collectionobject";

// Tables to show prominently at top (rest alpha-sorted after).
const PRIORITY = [
  "collectionobject",
  "collectingevent",
  "locality",
  "determination",
  "taxon",
  "agent",
  "preparation",
  "attachment",
];

export default function SchemaOutline({ schema, mappings, onAddNode, onShowMapping }: Props) {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set([ROOT_TABLE]),
  );

  const coverage = useMemo(() => buildCoverageMap(mappings), [mappings]);

  const tableNames = useMemo(() => {
    const all = Object.keys(schema.tables);
    const q = search.trim().toLowerCase();
    const filtered = q
      ? all.filter(
          (t) =>
            t.includes(q) ||
            schema.tables[t].columns.some((c) =>
              c.name.toLowerCase().includes(q),
            ),
        )
      : all;
    return [...filtered].sort((a, b) => {
      const ai = PRIORITY.indexOf(a);
      const bi = PRIORITY.indexOf(b);
      if (ai >= 0 && bi >= 0) return ai - bi;
      if (ai >= 0) return -1;
      if (bi >= 0) return 1;
      return a.localeCompare(b);
    });
  }, [schema, search]);

  const toggle = (t: string) =>
    setExpanded((prev) => {
      const s = new Set(prev);
      s.has(t) ? s.delete(t) : s.add(t);
      return s;
    });

  return (
    <div style={s.container}>
      <div style={s.header}>
        <span style={s.title}>Specify Schema</span>
        <span style={s.meta}>{schema.table_count}t / {schema.column_count}c</span>
      </div>
      <input
        style={s.search}
        placeholder="Search tables or columns…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div style={s.list}>
        {tableNames.map((tname) => (
          <TableRow
            key={tname}
            tname={tname}
            table={schema.tables[tname]}
            expanded={expanded.has(tname)}
            search={search.trim().toLowerCase()}
            coverage={coverage}
            onToggle={() => toggle(tname)}
            onAddNode={onAddNode}
            onShowMapping={onShowMapping}
            onRemoveMapping={onRemoveMapping}
          />
        ))}
      </div>
    </div>
  );
}

function TableRow({
  tname,
  table,
  expanded,
  search,
  coverage,
  onToggle,
  onAddNode,
  onShowMapping,
}: {
  tname: string;
  table: SpecifySchema["tables"][string];
  expanded: boolean;
  search: string;
  coverage: Map<string, MappingEdge>;
  onToggle: () => void;
  onAddNode: (d: SpecifyNodeData) => void;
  onShowMapping: (m: MappingEdge) => void;
  onRemoveMapping: (id: string) => void;
}) {
  const mapped = table.columns.filter((c) =>
    coverage.has(`${tname}.${c.name}`),
  ).length;
  const pct = table.columns.length
    ? Math.round((mapped / table.columns.length) * 100)
    : 0;

  const visibleCols = search
    ? table.columns.filter((c) => c.name.toLowerCase().includes(search) || tname.includes(search))
    : table.columns;

  return (
    <div>
      <div style={s.tableRow} onClick={onToggle}>
        <span style={s.caret}>{expanded ? "▾" : "▸"}</span>
        <span style={s.tableName}>{tname}</span>
        <span style={{ ...s.badge, opacity: mapped > 0 ? 1 : 0.3 }}>
          {mapped}/{table.columns.length}
        </span>
        <div style={{ ...s.miniBar, width: `${Math.max(pct, 2)}%` }} />
      </div>
      {expanded &&
        visibleCols.map((col) => (
          <ColRow
            key={col.name}
            tname={tname}
            col={col}
            mappedEdge={coverage.get(`${tname}.${col.name}`)}
            onAdd={() =>
              onAddNode({
                label: `${tname}.${col.name}`,
                specify_table: tname,
                specify_column: col.name,
                col_type: col.type,
                nullable: col.nullable,
              })
            }
            onShowMapping={onShowMapping}
            onRemoveMapping={onRemoveMapping}
          />
        ))}
    </div>
  );
}

function ColRow({
  tname,
  col,
  mappedEdge,
  onAdd,
  onShowMapping,
}: {
  tname: string;
  col: SpecifyColumn;
  mappedEdge?: MappingEdge;
  onAdd: () => void;
  onShowMapping: (m: MappingEdge) => void;
  onRemoveMapping: (id: string) => void;
}) {
  return (
    <div style={s.colRow} title={`${tname}.${col.name} — ${col.type}`}>
      <span style={{ ...s.dot, background: mappedEdge ? "#22c55e" : "#374151" }} />
      <div style={s.colInfo}>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={s.colName}>{col.name}</span>
          <span style={s.colType}>{col.type.replace(/\(.*\)/, "")}</span>
        </div>
        {mappedEdge && (
          <div style={s.sourceGroup}>
            <div
              style={s.sourceInfo}
              title={`Source: ${mappedEdge.oracle_path}\nClick to show on canvas`}
              onClick={() => onShowMapping(mappedEdge)}
            >
              ← {mappedEdge.oracle_path.split(".").slice(-1)[0]} 👁
            </div>
            <button
              style={s.removeBtn}
              onClick={(e) => { e.stopPropagation(); onRemoveMapping(mappedEdge.id); }}
              title="Remove mapping"
            >
              ×
            </button>
          </div>
        )}
      </div>
      <button
        style={s.addBtn}
        title="Add to canvas"
        onClick={(e) => { e.stopPropagation(); onAdd(); }}
      >
        +
      </button>
    </div>
  );
}

const s = {
  container: { display: "flex", flexDirection: "column" as const, height: "100%", fontSize: 12 },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "10px 12px 4px", borderBottom: "1px solid #21262d",
  },
  title: { fontWeight: 700, color: "#60a5fa", fontSize: 13 },
  meta: { color: "#6b7280", fontSize: 11 },
  search: {
    margin: "6px 8px",
    padding: "4px 8px",
    background: "#0f1117",
    border: "1px solid #30363d",
    borderRadius: 4,
    color: "#e2e8f0",
    fontSize: 12,
    width: "calc(100% - 16px)",
  },
  list: { flex: 1, overflowY: "auto" as const },
  tableRow: {
    display: "flex", alignItems: "center", gap: 4,
    padding: "5px 8px", cursor: "pointer",
    background: "#1e2530",
    borderBottom: "1px solid #21262d",
    position: "relative" as const,
    userSelect: "none" as const,
  },
  caret: { color: "#4b5563", width: 10, flexShrink: 0 },
  tableName: { fontWeight: 600, color: "#e2e8f0", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const },
  badge: { color: "#94a3b8", fontSize: 10, whiteSpace: "nowrap" as const },
  miniBar: {
    position: "absolute" as const, bottom: 0, left: 0, height: 2,
    background: "#22c55e", opacity: 0.6, transition: "width 0.3s",
  },
  colRow: {
    display: "flex", alignItems: "center", gap: 5,
    padding: "3px 8px 3px 22px",
    borderBottom: "1px solid #1a2030",
  },
  dot: { width: 6, height: 6, borderRadius: "50%", flexShrink: 0 },
  colInfo: { flex: 1, overflow: "hidden" },
  colName: { color: "#cbd5e1", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const },
  colType: { color: "#4b5563", fontSize: 10, fontFamily: "monospace" },
  sourceGroup: { display: "flex", alignItems: "center", gap: 4, marginTop: 1 },
  sourceInfo: { color: "#fbbf24", fontSize: 10, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const, cursor: "pointer" },
  removeBtn: { background: "transparent", border: "none", color: "#64748b", cursor: "pointer", fontSize: 12, padding: "0 2px", lineHeight: 1 },
  addBtn: {
    background: "transparent", border: "1px solid #374151", color: "#94a3b8",
    borderRadius: 3, cursor: "pointer", padding: "0 5px", lineHeight: "16px",
    fontSize: 13, flexShrink: 0,
    transition: "border-color 0.15s, color 0.15s",
  },
};
