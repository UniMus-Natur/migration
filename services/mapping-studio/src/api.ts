import type { SpecifySchema, PathOutline, ValueIndexBundle } from "./types";

// BASE_PATH as configured in vite.config.ts base.  Strip trailing slash.
const BASE = "/migration-harness";

export async function fetchSpecifySchema(force = false): Promise<SpecifySchema> {
  const url = `${BASE}/api/specify-schema?pretty=0${force ? "&force=1" : ""}`;
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`specify-schema fetch failed ${res.status}: ${text}`);
  }
  return res.json();
}

export async function fetchOraclePathOutline(resultId: string): Promise<PathOutline> {
  return _fetchOutline(resultId, "oracle-path-outline");
}

export async function fetchSpecifyPathOutline(resultId: string): Promise<PathOutline> {
  return _fetchOutline(resultId, "specify-path-outline");
}

export async function fetchValueIndexBundle(resultId: string): Promise<ValueIndexBundle> {
  const url = `${BASE}/result/${resultId}/value-index.json`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`value-index fetch failed ${res.status}`);
  return res.json();
}

async function _fetchOutline(resultId: string, kind: string): Promise<PathOutline> {
  const url = `${BASE}/result/${resultId}/${kind}.json?generalize=1`;
  const res = await fetch(url);
  if (!res.ok) {
    if (res.status === 404) throw new Error(`Result ${resultId} not found (session may have expired)`);
    throw new Error(`${kind} fetch failed ${res.status}`);
  }
  return res.json();
}

/** Flatten a trie into a sorted list of (full_path, examples, leaf_count) */
export interface FlatPath {
  path: string;
  examples: string[];
  leaf_count: number;
}

export function flattenTrie(
  node: PathOutline["tree"],
  prefix = "",
  out: FlatPath[] = [],
): FlatPath[] {
  const branches = node.branches ?? {};
  for (const [seg, child] of Object.entries(branches)) {
    const full = prefix ? `${prefix}.${seg}` : seg;
    if (child.terminal) {
      out.push({
        path: full,
        examples: child.terminal.examples,
        leaf_count: child.terminal.path_count,
      });
    }
    flattenTrie(child, full, out);
  }
  return out;
}
