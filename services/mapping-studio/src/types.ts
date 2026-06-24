// ---------------------------------------------------------------------------
// Specify schema (from /api/specify-schema)
// ---------------------------------------------------------------------------

export interface SpecifyColumn {
  name: string;
  type: string;
  nullable: boolean;
  pk: boolean;
  auto_increment: boolean;
}

export interface SpecifyOutgoingFK {
  from_col: string;
  to_table: string;
  to_col: string;
}

export interface SpecifyIncomingFK {
  from_table: string;
  from_col: string;
  to_col: string;
}

export interface SpecifyTable {
  columns: SpecifyColumn[];
  outgoing_fks: SpecifyOutgoingFK[];
  incoming_fks: SpecifyIncomingFK[];
}

export interface SpecifySchema {
  schema: string;
  root_table: string;
  table_count: number;
  column_count: number;
  tables: Record<string, SpecifyTable>;
  fk_edges: Array<{
    from_table: string;
    from_col: string;
    to_table: string;
    to_col: string;
  }>;
}

// ---------------------------------------------------------------------------
// Path outline (from /result/:id/oracle-path-outline.json?generalize=1)
// ---------------------------------------------------------------------------

export interface TrieTerminal {
  path_count: number;
  examples: string[];
}

export interface TrieNode {
  branches?: Record<string, TrieNode>;
  terminal?: TrieTerminal;
  subtree_leaves?: number;
}

export interface PathOutline {
  schema: string;
  tree: TrieNode;
  meta: {
    total_leaf_paths: number;
    generalize_array_indices: boolean;
  };
}

// ---------------------------------------------------------------------------
// Value Index (from /result/:id/value-index.json)
// ---------------------------------------------------------------------------

export interface ValueIndex {
  schema: string;
  by_value: Record<string, string[]>;
  meta: {
    unique_leaf_values: number;
    total_leaf_occurrences: number;
    max_paths_for_one_value: number;
    max_key_chars_before_mask: number;
  };
}

export interface ValueIndexBundle {
  schema: string;
  catalog: string;
  oracle: ValueIndex;
  specify: ValueIndex;
}

// ---------------------------------------------------------------------------
// Mappings (persisted in localStorage)
// ---------------------------------------------------------------------------

export type TransformKind = "direct" | "concat" | "lookup" | "derived" | "constant" | "split" | "custom";

export interface MappingEdge {
  id: string;
  oracle_path: string;         // e.g. "events[*].COLLECTING_EVENT.legname_orig"
  specify_table: string;       // e.g. "collectingevent"
  specify_column: string;      // e.g. "verbatimlocality"
  transform: TransformKind;
  note: string;
  confirmed: boolean;
  created_at: string;          // ISO timestamp
}

export interface MappingStore {
  version: 1;
  catalog: string;
  result_id: string;
  edges: MappingEdge[];
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Canvas node/edge data (extends React Flow types)
// ---------------------------------------------------------------------------

export interface OracleNodeData {
  label: string;
  oracle_path: string;
  examples: string[];
  leaf_count: number;
  [key: string]: unknown;
}

export interface SpecifyNodeData {
  label: string;
  specify_table: string;
  specify_column: string;
  col_type: string;
  nullable: boolean;
  examples: string[];
  [key: string]: unknown;
}
