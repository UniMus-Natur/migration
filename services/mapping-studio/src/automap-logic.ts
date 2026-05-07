import type { MappingEdge, MappingStore, ValueIndexBundle, SpecifySchema } from "./types";

export interface AutoMapResult {
  newEdges: Omit<MappingEdge, "id" | "created_at">[];
  stats: {
    added: number;
    skipped_junk: number;
    skipped_collision: number;
    skipped_schema: number;
  };
}

const IGNORE_VALUES = new Set(["<null>", "", "0", "1", "true", "false", "[]", "{}", "none", "null", "undefined"]);
const IGNORE_COLUMNS = new Set(["version", "ordinal", "timestampcreated", "timestampmodified", "id", "collectionmemberid"]);

export function performAutoMap(
  bundle: ValueIndexBundle,
  schema: SpecifySchema,
  existingEdges: MappingEdge[]
): AutoMapResult {
  let added = 0;
  let skipped_junk = 0;
  let skipped_collision = 0;
  let skipped_schema = 0;
  const newEdges: Omit<MappingEdge, "id" | "created_at">[] = [];

  // Create a normalized map for Specify values
  const specifyByNormalized = new Map<string, string[]>();
  for (const [val, paths] of Object.entries(bundle.specify.by_value)) {
    const norm = val.trim();
    if (!specifyByNormalized.has(norm)) specifyByNormalized.set(norm, []);
    specifyByNormalized.get(norm)!.push(...paths);
  }

  for (const [val, oraclePaths] of Object.entries(bundle.oracle.by_value)) {
    const v = val.trim();
    const vLower = v.toLowerCase();
    if (IGNORE_VALUES.has(vLower)) { skipped_junk++; continue; }
    if (v.length <= 2 && /^\d+$/.test(v)) { skipped_junk++; continue; }

    const specifyPaths = specifyByNormalized.get(v);
    if (!specifyPaths || specifyPaths.length === 0) continue;

    // Heuristic: unique targets
    const uniqueTargets = new Set(specifyPaths.map(p => {
      const parts = p.split(".");
      return parts.length >= 3 ? `${parts[1].replace(/\[.*\]$/, "")}.${parts[parts.length - 1]}` : p;
    }));
    if (uniqueTargets.size > 5) { skipped_collision++; continue; }

    for (const oPath of oraclePaths) {
      if (oPath.startsWith("_meta")) continue;
      for (const sPath of specifyPaths) {
        if (sPath.startsWith("_meta")) continue;
        const parts = sPath.split(".");
        if (parts.length < 3) continue;

        const tablePart = parts[1];
        const table = tablePart.replace(/\[.*\]$/, "");
        const col = parts[parts.length - 1];

        if (IGNORE_COLUMNS.has(col.toLowerCase())) continue;

        const tableSchema = schema.tables[table];
        if (!tableSchema) { skipped_schema++; continue; }
        
        // Match columns case-insensitively
        const colLower = col.toLowerCase();
        const matchedCol = tableSchema.columns.find((c) => c.name.toLowerCase() === colLower);
        if (!matchedCol) { skipped_schema++; continue; }

        const targetCol = matchedCol.name; // Use the actual case from the schema for the mapping
        const genOraclePath = oPath.replace(/\[\d+\]/g, "[*]");
        const exists = existingEdges.some(
          (e) => e.oracle_path === genOraclePath && e.specify_table === table && e.specify_column === targetCol,
        );
        if (!exists) {
          newEdges.push({
            oracle_path: genOraclePath,
            specify_table: table,
            specify_column: targetCol,
            transform: "direct",
            note: `Auto-mapped (matched value: ${v.slice(0, 30)}${v.length > 30 ? "…" : ""})`,
            confirmed: false,
          });
          added++;
        }
      }
    }
  }

  return {
    newEdges,
    stats: { added, skipped_junk, skipped_collision, skipped_schema }
  };
}
