"""
flatten_source.py — RIP source-tree flattening utility.

Copies ONLY the source files relevant to a chosen legacy language from a (possibly
type-organized, mixed-content) repository into a single flat folder, so RIP's Stage
2.5 module discovery falls back from FOLDER strategy to dependency/BFS clustering —
producing business-cohesive modules instead of file-type buckets. See the module
identification notes in the session handoff.

Why a tool: users should not have to know which extensions belong to a language.
The allowed extensions are read from the active language plugin's manifest (the
single source of truth), so this utility stays in sync automatically as plugins
add extensions.

Behaviour / safety:
  • Non-source files (docs, images, .mdb/.dat/.vbp/etc.) are simply not copied.
  • Filenames are PRESERVED verbatim (never renamed) — RIP resolves copybook COPY
    targets and program identities by file stem, so renaming would break linkage.
  • Same-name+extension collisions across sub-folders are detected and reported;
    the first occurrence is kept and later duplicates are skipped (not overwritten).
    A collision usually means duplicate members (e.g. mixing app-* variant folders)
    that the user should resolve deliberately.
  • Read-only w.r.t. the source; --dry-run previews without writing.

Usage:
    python flatten_source.py --lang cobol --src <repo> --out <flat_dir>
    python flatten_source.py --lang vb6   --src <repo> --out <flat_dir> --dry-run
    python flatten_source.py --list-langs
"""

import argparse
import importlib
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def discover_language_extensions() -> dict:
    """Return {plugin_name_lower: set(extensions)} by importing every language
    plugin and reading its manifest — the single source of truth."""
    try:
        from app.services.source_code_pipeline.src.scanner.plugin_base import (
            LanguagePluginBase,  # type: ignore
        )
    except Exception:
        from src.scanner.plugin_base import LanguagePluginBase  # type: ignore

    scanner_dir = ROOT / "src" / "scanner"
    langs: dict = {}
    for py in sorted(scanner_dir.glob("*.py")):
        if py.name in ("__init__.py", "plugin_base.py", "plugin_registry.py"):
            continue
        try:
            try:
                module = importlib.import_module(
                    f"app.services.source_code_pipeline.src.scanner.{py.stem}"
                )
            except Exception:
                module = importlib.import_module(f"src.scanner.{py.stem}")
        except Exception:
            continue
        for attr in dir(module):
            obj = getattr(module, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, LanguagePluginBase)
                and obj is not LanguagePluginBase
            ):
                try:
                    # Constructor needs a source dir; ROOT is a harmless placeholder
                    # (we only read the static manifest, not scan).
                    inst = obj(ROOT)
                    name = inst.get_manifest().plugin_name.strip().lower()
                    exts = {str(e).lower() for e in inst.supported_extensions}
                    if name and exts:
                        langs[name] = exts
                except Exception:
                    continue
    return langs


def main() -> int:
    langs = discover_language_extensions()

    ap = argparse.ArgumentParser(
        description="Flatten a legacy source tree to one folder (source files only)."
    )
    ap.add_argument("--lang", help="Language/paradigm (e.g. cobol, vb6, powerbuilder).")
    ap.add_argument("--src", help="Source repository root (scanned recursively).")
    ap.add_argument("--out", help="Output folder for the flattened source files.")
    ap.add_argument("--dry-run", action="store_true", help="Preview without copying.")
    ap.add_argument(
        "--list-langs",
        action="store_true",
        help="List supported languages and their extensions, then exit.",
    )
    args = ap.parse_args()

    if args.list_langs or not (args.lang and args.src and args.out):
        print("Supported languages (from plugin manifests):")
        for name in sorted(langs):
            print(f"  {name:14s} {sorted(langs[name])}")
        if args.list_langs:
            return 0
        print("\nRequired: --lang, --src, --out   (or --list-langs)")
        return 2

    lang = args.lang.strip().lower()
    if lang not in langs:
        print(f"[ERROR] Unknown language '{lang}'. Known: {sorted(langs)}")
        return 2

    exts = langs[lang]
    src = Path(args.src).resolve()
    out = Path(args.out).resolve()
    if not src.is_dir():
        print(f"[ERROR] Source directory not found: {src}")
        return 2
    if out == src or out in src.parents:
        print(f"[ERROR] --out must not be the source or a parent of it: {out}")
        return 2

    print(f"[FLATTEN] Language '{lang}' → extensions: {sorted(exts)}")
    print(f"[FLATTEN] Source: {src}")
    print(f"[FLATTEN] Output: {out}{'  (dry-run)' if args.dry_run else ''}")

    scanned = 0
    matched = []
    for p in sorted(src.rglob("*")):
        if p.is_file():
            scanned += 1
            if p.suffix.lower() in exts:
                matched.append(p)

    # Collision detection on name+extension (case-insensitive), first-wins.
    seen: dict = {}
    to_copy = []
    collisions = []
    for p in matched:
        key = p.name.lower()
        if key in seen:
            collisions.append((p, seen[key]))
            continue
        seen[key] = p
        to_copy.append(p)

    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        for p in to_copy:
            shutil.copy2(p, out / p.name)

    print(f"\n[FLATTEN] Files scanned:        {scanned}")
    print(f"[FLATTEN] Source files matched: {len(matched)}")
    print(
        f"[FLATTEN] Copied (unique):      {len(to_copy)}{' (dry-run: not written)' if args.dry_run else ''}"
    )
    print(f"[FLATTEN] Skipped non-source:   {scanned - len(matched)}")
    if collisions:
        print(
            f"\n[FLATTEN][WARN] {len(collisions)} name collision(s) — later duplicates SKIPPED "
            f"(kept the first; filenames are never renamed to preserve identity):"
        )
        for dup, kept in collisions:
            print(f"    SKIPPED {dup}")
            print(f"      kept  {kept}")
        print("    -> Resolve deliberately (e.g. you may be mixing app-* variant folders).")
    print("\n[FLATTEN] Done." + ("" if args.dry_run else f" Point RIP's source_path at: {out}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
