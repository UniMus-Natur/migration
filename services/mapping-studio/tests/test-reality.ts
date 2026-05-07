import fs from "node:fs";
import path from "node:path";
import { performAutoMap } from "../src/automap-logic.ts";
import type { SpecifySchema, ValueIndexBundle } from "../src/types.ts";

const REPO_ROOT = path.resolve("../.."); 

function loadJson(p: string) {
  return JSON.parse(fs.readFileSync(p, "utf-8"));
}

async function runTest() {
  console.log("--- Starting REAL Schema Auto-map Test ---");
  
  const oracleDoc = loadJson(path.join(REPO_ROOT, "example-oracle.json"));
  const specifyDoc = loadJson(path.join(REPO_ROOT, "example-specify.json"));
  const realSchema = loadJson(path.join(REPO_ROOT, "real-schema.json"));

  const bundle: ValueIndexBundle = {
    schema: "migration-harness/value-index-bundle/v1",
    catalog: "test",
    oracle: buildValueIndex(oracleDoc),
    specify: buildValueIndex(specifyDoc)
  };

  const result = performAutoMap(bundle, realSchema, []);
  
  console.log("Stats:", result.stats);
  console.log("\nSample Matches:");
  result.newEdges.slice(0, 20).forEach(e => {
    console.log(`  ${e.oracle_path} -> ${e.specify_table}.${e.specify_column} (${e.note})`);
  });
}

function buildValueIndex(doc: any): any {
  const acc: Record<string, string[]> = {};
  function walk(obj: any, p: string) {
    if (obj && typeof obj === "object") {
      if (Array.isArray(obj)) {
        obj.forEach((v, i) => walk(v, `${p}[${i}]`));
      } else {
        Object.entries(obj).forEach(([k, v]) => {
          const np = p ? `${p}.${k}` : k;
          walk(v, np);
        });
      }
      return;
    }
    const val = String(obj === null ? "<null>" : obj);
    if (!acc[val]) acc[val] = [];
    acc[val].push(p);
  }
  walk(doc, "");
  return { 
    schema: "v1",
    by_value: acc,
    meta: { unique_leaf_values: 0, total_leaf_occurrences: 0, max_paths_for_one_value: 0, max_key_chars_before_mask: 0 }
  };
}

runTest().catch(console.error);
