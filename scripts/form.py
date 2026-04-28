#!/usr/bin/env python3
"""Single entrypoint for Specify form workflows.

Usage:
  python3 scripts/form.py export --output-dir forms_all --clean --no-manifests
  python3 scripts/form.py plan --forms-dir forms_all --source-mode defaults --create-missing-views
  python3 scripts/form.py import --forms-dir forms_all --source-mode defaults --create-missing-views --apply
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def _patched_argv(argv: list[str]) -> Iterator[None]:
    old = sys.argv[:]
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = old


def _run_legacy(module_name: str, argv_tail: list[str]) -> None:
    try:
        mod = importlib.import_module(module_name)
    except ModuleNotFoundError:
        # Running as script (not package): load sibling module by path.
        script_name = module_name.split(".")[-1] + ".py"
        mod_path = Path(__file__).resolve().parent / script_name
        spec = importlib.util.spec_from_file_location(module_name, mod_path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"Unable to load {module_name} from {mod_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    if not hasattr(mod, "main"):
        raise SystemExit(f"{module_name} does not expose main()")
    with _patched_argv([module_name, *argv_tail]):
        mod.main()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Specify forms CLI (export / plan / import)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    export = sub.add_parser("export", help="Export forms from Specify to files")
    export.add_argument("--output-dir", default="forms", help="Output directory")
    export.add_argument("--collection", default=None, help="Collection name")
    export.add_argument("--clean", action="store_true", help="Delete output dir before writing")
    export.add_argument("--only-overrides", action="store_true", help="Write only overrides")
    export.add_argument("--no-manifests", action="store_true", help="Skip per-form manifest.json")

    plan = sub.add_parser("plan", help="Dry-run import plan (no writes)")
    plan.add_argument("--forms-dir", default="forms", help="Forms directory")
    plan.add_argument("--collection", default=None, help="Collection name")
    plan.add_argument("--viewset-name", default=None, help="Target viewset name")
    plan.add_argument("--verbose-missing", action="store_true", help="Print every missing mapping")
    plan.add_argument("--source-mode", choices=("auto", "defaults", "overrides"), default="auto")
    plan.add_argument("--create-missing-views", action="store_true", help="Create missing views in memory")

    imp = sub.add_parser("import", help="Import forms into Specify")
    imp.add_argument("--forms-dir", default="forms", help="Forms directory")
    imp.add_argument("--collection", default=None, help="Collection name")
    imp.add_argument("--viewset-name", default=None, help="Target viewset name")
    imp.add_argument("--backup", default=None, help="Backup remote XML to file before apply")
    imp.add_argument("--apply", action="store_true", help="Actually write changes")
    imp.add_argument("--verbose-missing", action="store_true", help="Print every missing mapping")
    imp.add_argument("--source-mode", choices=("auto", "defaults", "overrides"), default="auto")
    imp.add_argument("--create-missing-views", action="store_true", help="Create missing views")

    args = parser.parse_args()

    if args.cmd == "export":
        argv = []
        if args.output_dir:
            argv += ["--output-dir", args.output_dir]
        if args.collection:
            argv += ["--collection", args.collection]
        if args.clean:
            argv += ["--clean"]
        if args.only_overrides:
            argv += ["--only-overrides"]
        if args.no_manifests:
            argv += ["--no-manifests"]
        _run_legacy("scripts.export_specify_forms", argv)
        return

    if args.cmd == "plan":
        argv = [
            "--forms-dir",
            args.forms_dir,
            "--source-mode",
            args.source_mode,
        ]
        if args.collection:
            argv += ["--collection", args.collection]
        if args.viewset_name:
            argv += ["--viewset-name", args.viewset_name]
        if args.verbose_missing:
            argv += ["--verbose-missing"]
        if args.create_missing_views:
            argv += ["--create-missing-views"]
        _run_legacy("scripts.import_specify_forms", argv)
        return

    if args.cmd == "import":
        argv = [
            "--forms-dir",
            args.forms_dir,
            "--source-mode",
            args.source_mode,
        ]
        if args.collection:
            argv += ["--collection", args.collection]
        if args.viewset_name:
            argv += ["--viewset-name", args.viewset_name]
        if args.backup:
            argv += ["--backup", args.backup]
        if args.apply:
            argv += ["--apply"]
        if args.verbose_missing:
            argv += ["--verbose-missing"]
        if args.create_missing_views:
            argv += ["--create-missing-views"]
        _run_legacy("scripts.import_specify_forms", argv)
        return


if __name__ == "__main__":
    main()
