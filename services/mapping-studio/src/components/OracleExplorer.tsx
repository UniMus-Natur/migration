import { useState, useMemo, useEffect } from "react";
import Fuse from "fuse.js";
import type { PathOutline, MappingEdge, OracleNodeData } from "../types";
import { flattenTrie, type FlatPath } from "../api";

interface Props {
  outline: PathOutline;
  mappings: MappingEdge[];
  onAddNode: (data: OracleNodeData) => void;
  onShowMapping: (mapping: MappingEdge) => void;
  onRemoveMapping: (id: string) => void;
}

export default function OracleExplorer({ outline, mappings, onAddNode, onShowMapping, onRemoveMapping }: Props) {
  const [search, setSearch] = useState("");
  const [showMapped, setShowMapped] = useState(true);

  const flatPaths = useMemo(() => flattenTrie(outline.tree), [outline]);

  const fuse = useMemo(
    () =>
      new Fuse(flatPaths, {
        keys: ["path", "examples"],
        threshold: 0.3,
        includeScore: true,
      }),
    [flatPaths],
  );

  const mapped = useMemo(() => {
    const m = new Map<string, MappingEdge[]>();
    for (const edge of mappings) {
      const list = m.get(edge.oracle_path) ?? [];
      list.push(edge);
      m.set(edge.oracle_path, list);
    }
    return m;
  }, [mappings]);

  const visible: FlatPath[] = useMemo(() => {
    let list = search.trim()
      ? fuse.search(search.trim()).map((r) => r.item)
      : flatPaths;
    if (!showMapped) list = list.filter((p) => !mapped.has(p.path));
    return list;
  }, [flatPaths, fuse, search, showMapped, mapped]);

  // Group into top-level buckets for visual clarity.
  const grouped = useMemo(() => {
    const g = new Map<string, FlatPath[]>();
    for (const fp of visible) {
      const top = fp.path.split(".")[0] ?? fp.path;
      const bucket = g.get(top) ?? [];
      bucket.push(fp);
      g.set(top, bucket);
    }
    return [...g.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [visible]);

  return (
    <div style={s.container}>
      <div style={s.header}>
        <span style={s.title}>Oracle Paths</span>
        <span style={s.meta}>{outline.meta.total_leaf_paths} leaves</span>
      </div>
      <div style={s.toolbar}>
        <input
          style={s.search}
          placeholder="Search paths…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label style={s.toggle}>
          <input
            type="checkbox"
            checked={showMapped}
            onChange={(e) => setShowMapped(e.target.checked)}
            style={{ marginRight: 4 }}
          />
          show mapped
        </label>
      </div>
      <div style={s.list}>
        {grouped.map(([bucket, paths]) => (
          <BucketGroup
            key={bucket}
            bucket={bucket}
            paths={paths}
            mapped={mapped}
            onAddNode={onAddNode}
            onShowMapping={onShowMapping}
            onRemoveMapping={onRemoveMapping}
          />
        ))}
        {visible.length === 0 && (
          <div style={{ color: "#6b7280", padding: "16px 12px", fontSize: 12 }}>
            No paths match.
          </div>
        )}
      </div>
    </div>
  );
}

function BucketGroup({
  bucket,
  paths,
  mapped,
  onAddNode,
  onShowMapping,
  onRemoveMapping,
}: {
  bucket: string;
  paths: FlatPath[];
  mapped: Map<string, MappingEdge[]>;
  onAddNode: (d: OracleNodeData) => void;
  onShowMapping: (m: MappingEdge) => void;
  onRemoveMapping: (id: string) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <div style={s.bucket} onClick={() => setOpen((v) => !v)}>
        <span style={s.caret}>{open ? "▾" : "▸"}</span>
        <span style={s.bucketName}>{bucket}</span>
        <span style={s.bucketCount}>{paths.length}</span>
      </div>
      {open &&
        paths.map((fp) => (
          <PathRow
            key={fp.path}
            fp={fp}
            mappedEdges={mapped.get(fp.path) ?? []}
            onAdd={() =>
              onAddNode({
                label: fp.path,
                oracle_path: fp.path,
                examples: fp.examples,
                leaf_count: fp.leaf_count,
              })
            }
            onShowMapping={onShowMapping}
            onRemoveMapping={onRemoveMapping}
          />
        ))}
    </div>
  );
}

function PathRow({
  fp,
  mappedEdges,
  onAdd,
  onShowMapping,
  onRemoveMapping,
}: {
  fp: FlatPath;
  mappedEdges: MappingEdge[];
  onAdd: () => void;
  onShowMapping: (m: MappingEdge) => void;
  onRemoveMapping: (id: string) => void;
}) {
  const isMapped = mappedEdges.length > 0;
  const shortLabel = fp.path.replace(/^[^.]+\./, ""); // strip top bucket
  return (
    <div style={{ ...s.pathRow, opacity: isMapped ? 0.8 : 1 }} title={fp.path}>
      <span style={{ ...s.dot, background: isMapped ? "#22c55e" : "#374151" }} />
      <div style={s.pathInfo}>
        <span style={s.pathName}>{shortLabel}</span>
        {isMapped && (
          <div style={s.targetInfo}>
            {mappedEdges.map((e) => (
              <div key={e.id} style={s.targetTagGroup}>
                <span
                  style={s.targetTag}
                  onClick={() => onShowMapping(e)}
                  title="Show connection on canvas"
                >
                  → {e.specify_table}.{e.specify_column} 👁
                </span>
                <button
                  style={s.removeBtn}
                  onClick={() => onRemoveMapping(e.id)}
                  title="Remove mapping"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        {!isMapped && fp.examples.length > 0 && (
          <span style={s.example}>{fp.examples[0]}</span>
        )}
      </div>
      <button style={s.addBtn} onClick={onAdd} title="Add to canvas">+</button>
    </div>
  );
}

const s = {
  container: { display: "flex", flexDirection: "column" as const, height: "100%", fontSize: 12 },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "10px 12px 4px", borderBottom: "1px solid #21262d",
  },
  title: { fontWeight: 700, color: "#f59e0b", fontSize: 13 },
  meta: { color: "#6b7280", fontSize: 11 },
  toolbar: { padding: "6px 8px", display: "flex", flexDirection: "column" as const, gap: 4 },
  search: {
    padding: "4px 8px",
    background: "#0f1117",
    border: "1px solid #30363d",
    borderRadius: 4,
    color: "#e2e8f0",
    fontSize: 12,
    width: "100%",
  },
  toggle: { fontSize: 11, color: "#94a3b8", cursor: "pointer", display: "flex", alignItems: "center" },
  list: { flex: 1, overflowY: "auto" as const },
  bucket: {
    display: "flex", alignItems: "center", gap: 4,
    padding: "5px 8px", cursor: "pointer",
    background: "#1a2030", borderBottom: "1px solid #21262d",
    userSelect: "none" as const,
  },
  caret: { color: "#4b5563", width: 10 },
  bucketName: { fontWeight: 600, color: "#fbbf24", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const },
  bucketCount: { color: "#6b7280", fontSize: 10, flexShrink: 0 },
  pathRow: {
    display: "flex", alignItems: "center", gap: 5,
    padding: "4px 8px 4px 20px",
    borderBottom: "1px solid #1a2030",
  },
  dot: { width: 6, height: 6, borderRadius: "50%", flexShrink: 0, marginTop: 2 },
  pathInfo: { flex: 1, overflow: "hidden" },
  pathName: { display: "block", color: "#cbd5e1", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const },
  targetInfo: { display: "flex", flexWrap: "wrap" as const, gap: 4, marginTop: 2 },
  targetTag: { color: "#60a5fa", fontSize: 10, fontWeight: 500, cursor: "pointer" },
  targetTagGroup: { display: "flex", alignItems: "center", gap: 2, background: "#0f172a", padding: "1px 4px", borderRadius: 3, border: "1px solid #1e293b" },
  removeBtn: { background: "transparent", border: "none", color: "#64748b", cursor: "pointer", fontSize: 12, padding: "0 2px", lineHeight: 1 },
  example: {
    display: "block", color: "#6b7280", fontSize: 10, fontFamily: "monospace",
    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const,
  },
  addBtn: {
    background: "transparent", border: "1px solid #374151", color: "#94a3b8",
    borderRadius: 3, cursor: "pointer", padding: "0 5px", lineHeight: "16px",
    fontSize: 13, flexShrink: 0,
  },
};
