import { useMemo } from "react";
import type { SpecifySchema, MappingEdge } from "../types";
import { buildCoverageMap } from "../store";

interface Props {
  schema: SpecifySchema;
  mappings: MappingEdge[];
}

const PRIORITY = [
  "collectionobject",
  "collectingevent",
  "locality",
  "determination",
  "taxon",
  "preparation",
  "agent",
  "attachment",
];

export default function CoverageMatrix({ schema, mappings }: Props) {
  const coverage = useMemo(() => buildCoverageMap(mappings), [mappings]);

  const tableStats = useMemo(() => {
    return Object.entries(schema.tables)
      .map(([tname, t]) => {
        const total = t.columns.length;
        const mapped = t.columns.filter((c) => coverage.has(`${tname}.${c.name}`)).length;
        return { tname, total, mapped };
      })
      .filter((r) => r.total > 0)
      .sort((a, b) => {
        const ai = PRIORITY.indexOf(a.tname);
        const bi = PRIORITY.indexOf(b.tname);
        if (ai >= 0 && bi >= 0) return ai - bi;
        if (ai >= 0) return -1;
        if (bi >= 0) return 1;
        if (b.mapped !== a.mapped) return b.mapped - a.mapped;
        return a.tname.localeCompare(b.tname);
      });
  }, [schema, coverage]);

  const totalMapped = mappings.length;
  const totalCols = tableStats.reduce((s, r) => s + r.total, 0);
  const totalCovered = tableStats.reduce((s, r) => s + r.mapped, 0);

  return (
    <div style={s.container}>
      <div style={s.header}>
        <span style={s.title}>Coverage</span>
        <span style={s.summary}>
          {totalCovered} / {totalCols} columns mapped — {totalMapped} edges defined
        </span>
      </div>
      <div style={s.scroll}>
        {tableStats.map(({ tname, total, mapped }) => {
          const pct = total ? Math.round((mapped / total) * 100) : 0;
          return (
            <div key={tname} style={s.row} title={`${tname}: ${mapped}/${total} columns mapped`}>
              <span style={s.tname}>{tname}</span>
              <div style={s.barTrack}>
                <div
                  style={{
                    ...s.barFill,
                    width: `${Math.max(pct, pct > 0 ? 4 : 0)}%`,
                    background: pct >= 80 ? "#22c55e" : pct >= 40 ? "#f59e0b" : "#3b82f6",
                  }}
                />
              </div>
              <span style={s.pct}>{pct}%</span>
              <span style={s.count}>{mapped}/{total}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const s = {
  container: { display: "flex", flexDirection: "column" as const, height: "100%", fontSize: 11 },
  header: {
    display: "flex", alignItems: "center", gap: 12,
    padding: "5px 12px 3px", borderBottom: "1px solid #21262d", flexShrink: 0,
  },
  title: { fontWeight: 700, color: "#94a3b8", fontSize: 12, flexShrink: 0 },
  summary: { color: "#6b7280" },
  scroll: {
    display: "flex", flexDirection: "row" as const, gap: 0,
    overflowX: "auto" as const, overflowY: "hidden" as const,
    flex: 1, alignItems: "center", padding: "4px 8px",
  },
  row: {
    display: "flex", flexDirection: "column" as const, alignItems: "center",
    minWidth: 70, maxWidth: 90, flexShrink: 0, padding: "0 4px",
  },
  tname: {
    color: "#94a3b8", overflow: "hidden", textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const, width: "100%", textAlign: "center" as const, marginBottom: 2,
  },
  barTrack: {
    width: "100%", height: 10, background: "#1e2530",
    borderRadius: 3, overflow: "hidden", marginBottom: 2,
  },
  barFill: { height: "100%", borderRadius: 3, transition: "width 0.4s" },
  pct: { color: "#e2e8f0", fontWeight: 600 },
  count: { color: "#6b7280", fontSize: 10 },
};
