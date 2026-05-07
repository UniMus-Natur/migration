import fs from "node:fs";
import path from "node:path";
import { performAutoMap } from "./automap-logic.ts";
import type { SpecifySchema, ValueIndexBundle } from "./types";

// Mocking the data loading since we are in Node.js
const REPO_ROOT = path.resolve("../.."); // assuming we run from services/mapping-studio

function loadJson(p: string) {
  return JSON.parse(fs.readFileSync(p, "utf-8"));
}

async function runTest() {
  console.log("--- Starting Auto-map Logic Test ---");
  
  const oracleDoc = loadJson(path.join(REPO_ROOT, "example-oracle.json"));
  const specifyDoc = loadJson(path.join(REPO_ROOT, "example-specify.json"));
  
  // We need a schema. Let's create a minimal mock schema based on what we expect.
  const schema: SpecifySchema = {
    tables: {
      agent: { columns: [{ name: "lastname", type: "string", nullable: true }] },
      collectingevent: { columns: [{ name: "verbatimlocality", type: "string", nullable: true }, { name: "verbatimdate", type: "string", nullable: true }] },
      locality: { columns: [{ name: "localityname", type: "string", nullable: true }] },
      collectionobject: { columns: [
        { name: "catalognumber", type: "string", nullable: true },
        { name: "fieldnumber", type: "string", nullable: true },
        { name: "lastmodifiedby", type: "string", nullable: true }
      ] }
    }
  };

  // Build the ValueIndexBundle manually (mimic backend logic)
  const bundle: ValueIndexBundle = {
    catalog: "test",
    oracle: buildValueIndex(oracleDoc),
    specify: buildValueIndex(specifyDoc)
  };

  const result = performAutoMap(bundle, schema, []);
  
  console.log("Stats:", result.stats);
  console.log("\nSample Matches:");
  result.newEdges.slice(0, 20).forEach(e => {
    console.log(`  ${e.oracle_path} -> ${e.specify_table}.${e.specify_column} (${e.note})`);
  });

  if (result.stats.added > 0) {
    console.log("\nSUCCESS: Auto-map found matches using the shared logic.");
  } else {
    console.log("\nFAILURE: Auto-map found nothing.");
  }
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
  return { by_value: acc };
}

runTest().catch(console.error);
