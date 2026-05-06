#!/usr/bin/env python3
"""
Build a path-prefix trie from a JSON document: each node aggregates subtree leaf
counts and example scalar values at terminals. Paths match the migration-harness
value-index convention (dot + bracket segments).

CLI:
  python scripts/json_path_outline.py dump.json -o outline.json
  python scripts/json_path_outline.py dump.json --generalize --pretty
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_BRACKET_INDEX = re.compile(r"\[\d+]")


def _path_segments(path: str, *, generalize_array_indices: bool) -> list[str]:
    if not path:
        return ["$"]
    parts = path.split(".")
    if not generalize_array_indices:
        return parts
    return [_BRACKET_INDEX.sub("[*]", p) for p in parts]


def _example_string(v: Any, max_len: int) -> str:
    if v is None:
        return "<null>"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if len(v) > max_len:
            return v[: max_len - 3] + "..."
        return v
    if isinstance(v, bytes):
        return f"<bytes {len(v)}>"
    try:
        s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        s = str(v)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _walk_leaves(obj: Any, path: str, out: list[tuple[str, Any]]) -> None:
    """Collect (json_path, raw_scalar) for every leaf; path rules match harness value index."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            seg = str(k)
            if not seg.isidentifier():
                seg = "[" + json.dumps(seg, ensure_ascii=False) + "]"
                next_path = f"{path}{seg}" if path else seg.lstrip(".")
            else:
                next_path = f"{path}.{seg}" if path else seg
            _walk_leaves(v, next_path, out)
        return
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_leaves(v, f"{path}[{i}]", out)
        return
    out.append((path if path else "$", obj))


def _new_trie_node() -> dict[str, Any]:
    return {}


def _trie_insert(
    root: dict[str, Any],
    path: str,
    raw_value: Any,
    *,
    generalize_array_indices: bool,
    max_examples: int,
    max_example_len: int,
) -> None:
    segments = _path_segments(path, generalize_array_indices=generalize_array_indices)
    node = root
    for i, seg in enumerate(segments):
        branches = node.setdefault("branches", {})
        is_last = i == len(segments) - 1
        if is_last:
            leaf = branches.setdefault(seg, _new_trie_node())
            term = leaf.setdefault("terminal", {"path_count": 0, "examples": []})
            term["path_count"] = term.get("path_count", 0) + 1
            ex = term.setdefault("examples", [])
            s = _example_string(raw_value, max_example_len)
            if len(ex) < max_examples and s not in ex:
                ex.append(s)
        else:
            node = branches.setdefault(seg, _new_trie_node())


def _subtree_leaf_count(node: dict[str, Any]) -> int:
    n = node.get("terminal") or {}
    total = int(n.get("path_count", 0))
    for ch in (node.get("branches") or {}).values():
        total += _subtree_leaf_count(ch)
    node["subtree_leaves"] = total
    return total


def _compact_trie(node: dict[str, Any]) -> dict[str, Any]:
    """Stable, smaller JSON (sorted branch keys)."""
    out: dict[str, Any] = {}
    br = node.get("branches") or {}
    if br:
        out["branches"] = {k: _compact_trie(br[k]) for k in sorted(br.keys())}
    if node.get("terminal"):
        out["terminal"] = node["terminal"]
    if "subtree_leaves" in node:
        out["subtree_leaves"] = node["subtree_leaves"]
    return out


def build_path_outline(
    doc: Any,
    *,
    generalize_array_indices: bool = False,
    max_example_values_per_terminal: int = 5,
    max_example_string_len: int = 200,
) -> dict[str, Any]:
    """
    Path-prefix trie for one JSON document. Each terminal node holds path_count
    (always 1 per distinct path unless you merge via generalize) and example strings.
    """
    leaves: list[tuple[str, Any]] = []
    _walk_leaves(doc, "", leaves)
    root = _new_trie_node()
    for p, raw in leaves:
        _trie_insert(
            root,
            p,
            raw,
            generalize_array_indices=generalize_array_indices,
            max_examples=max_example_values_per_terminal,
            max_example_len=max_example_string_len,
        )
    total = _subtree_leaf_count(root)
    tree = _compact_trie(root)
    return {
        "schema": "migration-harness/path-outline/v1",
        "tree": tree,
        "meta": {
            "total_leaf_paths": total,
            "generalize_array_indices": generalize_array_indices,
            "max_example_values_per_terminal": max_example_values_per_terminal,
            "max_example_string_len": max_example_string_len,
        },
    }


def build_path_outline_bundle(
    oracle_doc: Any,
    specify_doc: Any,
    *,
    catalog: str | None = None,
    generalize_array_indices: bool = False,
    max_example_values_per_terminal: int = 5,
    max_example_string_len: int = 200,
) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "generalize_array_indices": generalize_array_indices,
        "max_example_values_per_terminal": max_example_values_per_terminal,
        "max_example_string_len": max_example_string_len,
    }
    return {
        "schema": "migration-harness/path-outline-bundle/v1",
        "catalog": catalog,
        "oracle": build_path_outline(oracle_doc, **kw),
        "specify": build_path_outline(specify_doc, **kw),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build path-outline trie JSON from a document.")
    parser.add_argument("input", help="Input JSON file (object or array root)")
    parser.add_argument("-o", "--output", help="Write here instead of stdout")
    parser.add_argument(
        "--generalize",
        action="store_true",
        help="Collapse numeric array indices to [*] (merge events[0] with events[2], etc.)",
    )
    parser.add_argument("--pretty", action="store_true", help="Indented JSON")
    parser.add_argument(
        "--max-examples",
        type=int,
        default=5,
        help="Max distinct example values stored per terminal node (default: 5)",
    )
    parser.add_argument(
        "--max-example-len",
        type=int,
        default=200,
        help="Truncate example strings (default: 200)",
    )
    args = parser.parse_args()
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    outline = build_path_outline(
        raw,
        generalize_array_indices=args.generalize,
        max_example_values_per_terminal=args.max_examples,
        max_example_string_len=args.max_example_len,
    )
    text = json.dumps(outline, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
