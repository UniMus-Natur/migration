import type { MappingEdge, MappingStore, TransformKind } from "./types";

const STORAGE_KEY = "mapping-studio-mappings";

export function loadStore(resultId: string, catalog: string): MappingStore {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as MappingStore;
      if (parsed.version === 1 && parsed.result_id === resultId) {
        return parsed;
      }
    }
  } catch {
    // Corrupt storage — start fresh.
  }
  return emptyStore(resultId, catalog);
}

export function emptyStore(resultId: string, catalog: string): MappingStore {
  return {
    version: 1,
    catalog,
    result_id: resultId,
    edges: [],
    updated_at: new Date().toISOString(),
  };
}

export function saveStore(store: MappingStore): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
}

export function addEdge(
  store: MappingStore,
  edge: Omit<MappingEdge, "id" | "created_at">,
): MappingStore {
  const existing = store.edges.findIndex(
    (e) => e.oracle_path === edge.oracle_path &&
           e.specify_table === edge.specify_table &&
           e.specify_column === edge.specify_column,
  );
  const full: MappingEdge = {
    ...edge,
    id: existing >= 0 ? store.edges[existing].id : crypto.randomUUID(),
    created_at: existing >= 0 ? store.edges[existing].created_at : new Date().toISOString(),
  };
  const edges = existing >= 0
    ? store.edges.map((e, i) => (i === existing ? full : e))
    : [...store.edges, full];
  return { ...store, edges, updated_at: new Date().toISOString() };
}

export function removeEdge(store: MappingStore, edgeId: string): MappingStore {
  return {
    ...store,
    edges: store.edges.filter((e) => e.id !== edgeId),
    updated_at: new Date().toISOString(),
  };
}

export function updateEdgeTransform(
  store: MappingStore,
  edgeId: string,
  transform: TransformKind,
  note: string,
): MappingStore {
  return {
    ...store,
    edges: store.edges.map((e) =>
      e.id === edgeId ? { ...e, transform, note } : e,
    ),
    updated_at: new Date().toISOString(),
  };
}

export function exportJSON(store: MappingStore): void {
  const text = JSON.stringify(store, null, 2);
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const date = new Date().toISOString().slice(0, 10);
  a.href = url;
  a.download = `mappings-${store.catalog || "unknown"}-${date}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function importJSON(
  file: File,
  onLoad: (store: MappingStore) => void,
  onError: (msg: string) => void,
): void {
  const reader = new FileReader();
  reader.onload = (ev) => {
    try {
      const parsed = JSON.parse(ev.target?.result as string) as MappingStore;
      if (parsed.version !== 1 || !Array.isArray(parsed.edges)) {
        onError("Invalid mapping file format (expected version:1 with edges array)");
        return;
      }
      onLoad(parsed);
    } catch (e) {
      onError(`Parse error: ${e}`);
    }
  };
  reader.readAsText(file);
}

/** Map of specify_table+column -> edge id for fast coverage lookup */
export function buildCoverageMap(
  edges: MappingEdge[],
): Map<string, MappingEdge> {
  const m = new Map<string, MappingEdge>();
  for (const e of edges) {
    m.set(`${e.specify_table}.${e.specify_column}`, e);
  }
  return m;
}
