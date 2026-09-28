"""
Stage ONLY business source files into an input folder, excluding vendor-library/framework code
(e.g. the PowerBuilder Foundation Classes). Uses the same per-language exclusion rules as the pipeline
(prompts/language_profiles.yaml -> library_excludes, plus project_config.json -> source_filters).

Why: a big legacy repo can be mostly framework (Shinetsu PB: ~522 of ~937 files are PFC). Those files are
migrated to target-framework equivalents, not reverse-engineered as business modules, so they should not be
flattened into input/ or fed to module derivation.

Usage (from the repo root):
    python stage_business_source.py <raw_src_dir> <dest_input_dir> [--paradigm pb] [--config PATH]
                                    [--preserve] [--exts .sru,.srw,...] [--dry-run]

    # Shinetsu example (dry run first to see the split):
    python stage_business_source.py \
        projects/sample_project/input/pb_shinetsu_pat \
        projects/sample_project/input/pb_shinetsu_pat_business --paradigm pb --dry-run

Options:
    --paradigm   source paradigm (pb|vb6|cobol...). Default: read from --config, else 'pb'.
    --config     project_config.json to read source_filters overrides from (optional).
    --preserve   keep the source sub-folder structure under dest (default: flatten to a single folder).
    --exts       comma-separated extensions to include (default: infer common legacy source extensions).
    --dry-run    report the business/framework split without copying.
"""

import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from app.services.source_code_pipeline.src.tools.framework_filter import (  # noqa: E402
        is_framework,
        load_filters,
        load_project_config,
    )
except Exception:
    from src.tools.framework_filter import (  # noqa: E402
        is_framework,
        load_filters,
        load_project_config,
    )

_DEFAULT_EXTS = {
    ".sru",
    ".srw",
    ".srd",
    ".srm",
    ".sra",
    ".srf",
    ".srs",
    ".srq",
    ".srj",
    ".cbl",
    ".cpy",
    ".pco",
    ".jcl",
    ".frm",
    ".bas",
    ".cls",
    ".ctl",
    ".vbp",
}


def main():
    ap = argparse.ArgumentParser(
        description="Stage business-only source (exclude framework/library)."
    )
    ap.add_argument("raw_src")
    ap.add_argument("dest")
    ap.add_argument("--paradigm", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--preserve", action="store_true")
    ap.add_argument("--exts", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = Path(args.raw_src)
    dest = Path(args.dest)
    if not raw.is_dir():
        print(f"[STAGE][ERROR] source not found: {raw}")
        sys.exit(1)

    config = load_project_config(".", args.config) if args.config else {}
    paradigm = args.paradigm or (config.get("project", {}) or {}).get("source_paradigm") or "pb"
    filters = load_filters(config, paradigm)
    exts = (
        {e if e.startswith(".") else "." + e for e in args.exts.lower().split(",")}
        if args.exts
        else _DEFAULT_EXTS
    )

    all_files = [p for p in raw.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    business = [p for p in all_files if not is_framework(p, filters)]
    framework = [p for p in all_files if is_framework(p, filters)]

    # per top-level category tally
    def cat(p):
        rel = p.relative_to(raw).parts
        return rel[0] if len(rel) > 1 else "(root)"

    cats = {}
    for p in business:
        cats[cat(p)] = cats.get(cat(p), 0) + 1

    print(
        f"[STAGE] paradigm={paradigm}  exclude_dirs={sorted(filters['exclude_dirs'])}  "
        f"patterns={filters['exclude_globs']}"
    )
    print(f"[STAGE] total source files: {len(all_files)}")
    print(f"[STAGE] BUSINESS (staged):  {len(business)}")
    print(f"[STAGE] FRAMEWORK (skipped):{len(framework)}")
    print("[STAGE] business by category: " + ", ".join(f"{k}={v}" for k, v in sorted(cats.items())))

    if args.dry_run:
        print("[STAGE] dry-run — nothing copied.")
        return

    dest.mkdir(parents=True, exist_ok=True)
    copied, collisions = 0, 0
    for p in business:
        if args.preserve:
            target = dest / p.relative_to(raw)
        else:
            target = dest / p.name
            if target.exists():  # flatten collision -> prefix with category to keep both
                collisions += 1
                target = dest / f"{cat(p)}__{p.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        copied += 1
    print(
        f"[STAGE] copied {copied} business file(s) -> {dest}"
        + (f" ({collisions} flatten collision(s) disambiguated by category)" if collisions else "")
    )


if __name__ == "__main__":
    main()
