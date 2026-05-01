#!/usr/bin/env python3
"""
execute_organization_plan_rsync_batch_v3.py

Efficient batch executor for reviewed organization plans.

Main design
-----------
The previous executor ran one rsync process per file. That is safe but slow.

This version is faster because it:

  1. Reads the final organization plan TSV.
  2. Builds a temporary symlink staging tree that mirrors the final destination layout.
  3. Runs ONE batch rsync command:
        rsync -aL --ignore-existing --info=progress2 STAGING/ DEST_ROOT/
  4. Dereferences symlinks with -L, so the destination receives real files, not symlinks.

Why this is efficient
---------------------
It avoids launching one rsync process per file. rsync sees a normal tree and copies it
as one batch.

Safety
------
- Dry-run by default.
- Does not delete originals.
- Does not move originals.
- Does not overwrite existing destination files.
- Uses --ignore-existing.
- Destination can be external to analyzed folder.
- Copy output is independent, not hardlinked.

Typical usage
-------------

Dry-run:

  python3 execute_organization_plan_rsync_batch_v3.py \\
    --plan work_Andre/reports/organization_plan_final.tsv \\
    --log work_Andre/execute_batch.log \\
    --source-root /run/media/lucas/HDD/to_do_backup_2023_02/Andre \\
    --dest-parent /run/media/lucas/HDD/to_do_backup_2023_02 \\
    --dest-prefix ORGANIZED_UNIQUE_CONTEXTUAL

Execute:

  python3 execute_organization_plan_rsync_batch_v3.py \\
    --plan work_Andre/reports/organization_plan_final.tsv \\
    --log work_Andre/execute_batch.log \\
    --execute \\
    --source-root /run/media/lucas/HDD/to_do_backup_2023_02/Andre \\
    --dest-parent /run/media/lucas/HDD/to_do_backup_2023_02 \\
    --dest-prefix ORGANIZED_UNIQUE_CONTEXTUAL

This creates:

  /run/media/lucas/HDD/to_do_backup_2023_02/ORGANIZED_UNIQUE_CONTEXTUAL_Andre
"""

from __future__ import annotations

import argparse
import base64
import csv
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REQ = {"source_path_b64", "source_absolute_path", "destination_path", "duplicate_status"}

MARKERS = [
    "ORGANIZED_UNIQUE_CONTEXTUAL",
    "ORGANIZED_UNIQUE",
    "PREVIEW_ORGANIZED_UNIQUE_CONTEXTUAL",
    "PREVIEW_ORGANIZED_UNIQUE",
]


def read_plan(plan_path: Path) -> List[Dict[str, str]]:
    with plan_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    if not rows:
        raise SystemExit(f"ERROR: empty plan: {plan_path}")

    missing = REQ - set(rows[0])
    if missing:
        raise SystemExit(f"ERROR: missing columns in plan: {sorted(missing)}")

    return rows


def decode_source(row: Dict[str, str]) -> str:
    b64 = row.get("source_path_b64", "")
    if b64:
        try:
            return base64.b64decode(b64.encode("ascii")).decode("utf-8", errors="surrogateescape")
        except Exception:
            pass
    return row.get("source_absolute_path", "")


def detect_relative_inside_organized_root(destination_path: str) -> Optional[str]:
    parts = Path(destination_path).parts
    for marker in MARKERS:
        if marker in parts:
            idx = parts.index(marker)
            rel_parts = parts[idx + 1 :]
            if rel_parts:
                return str(Path(*rel_parts))
    return None


def infer_external_dest_root(
    source_root: Optional[Path],
    dest_parent: Optional[Path],
    dest_prefix: str,
) -> Optional[Path]:
    if source_root is None and dest_parent is None:
        return None

    if source_root is None:
        raise SystemExit("ERROR: --dest-parent requires --source-root")

    source_name = source_root.name.rstrip("/")
    if not source_name:
        raise SystemExit(f"ERROR: cannot infer basename from --source-root: {source_root}")

    if dest_parent is None:
        dest_parent = source_root.parent

    return dest_parent / f"{dest_prefix}_{source_name}"


def remap_destination(original_dest: str, external_dest_root: Optional[Path]) -> Tuple[str, str]:
    """
    Returns:
      (final_destination_absolute, relative_destination_inside_final_root)

    If external_dest_root is provided:
      original plan destination is remapped into external_dest_root.
    Else:
      original destination is used, and relative path is inferred from markers if possible.
    """
    rel = detect_relative_inside_organized_root(original_dest)

    if external_dest_root is not None:
        if rel:
            return str(external_dest_root / rel), rel
        # Fallback: preserve only filename to avoid recreating absolute paths.
        fallback_rel = Path(original_dest).name
        return str(external_dest_root / fallback_rel), fallback_rel

    # No external destination root: use the plan destination exactly.
    if rel:
        return original_dest, rel

    # Last-resort relative path for staging.
    return original_dest, Path(original_dest).name


def require_rsync() -> None:
    if shutil.which("rsync") is None:
        raise SystemExit("ERROR: rsync not found. Install with: sudo apt install -y rsync")


def safe_unlink_or_rmtree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def build_staging_tree(
    rows: List[Dict[str, str]],
    staging_root: Path,
    external_dest_root: Optional[Path],
    log,
    verbose: bool,
) -> Dict[str, int]:
    """
    Build symlink tree mirroring final destination relative structure.

    Returns counts.
    """
    counts = {
        "rows": len(rows),
        "staged": 0,
        "skipped_duplicate": 0,
        "missing_source": 0,
        "bad_row": 0,
        "dest_collision": 0,
    }

    seen_rel = {}

    if staging_root.exists() or staging_root.is_symlink():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)

    for i, row in enumerate(rows, start=1):
        status = row.get("duplicate_status", "")
        src = decode_source(row)
        original_dest = row.get("destination_path", "")

        if status == "duplicate":
            counts["skipped_duplicate"] += 1
            log.write(f"SKIP_DUPLICATE\t{i}\t{src}\t{original_dest}\n")
            continue

        if not src or not original_dest:
            counts["bad_row"] += 1
            log.write(f"BAD_ROW\t{i}\t{src}\t{original_dest}\n")
            continue

        if not os.path.isfile(src):
            counts["missing_source"] += 1
            log.write(f"MISSING_SOURCE\t{i}\t{src}\t{original_dest}\n")
            continue

        final_dest, rel = remap_destination(original_dest, external_dest_root)

        if rel in seen_rel:
            counts["dest_collision"] += 1
            log.write(f"DEST_COLLISION\t{i}\t{src}\t{rel}\tprevious={seen_rel[rel]}\n")
            continue

        seen_rel[rel] = src

        link_path = staging_root / rel
        link_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            os.symlink(src, link_path)
            counts["staged"] += 1
            if verbose and (counts["staged"] <= 20 or counts["staged"] % 1000 == 0):
                print(f"[STAGE] {counts['staged']} files staged; latest: {rel}", flush=True)
            log.write(f"STAGED\t{i}\t{src}\t{final_dest}\t{rel}\n")
        except FileExistsError:
            counts["dest_collision"] += 1
            log.write(f"STAGE_EXISTS_COLLISION\t{i}\t{src}\t{rel}\n")
        except Exception as e:
            counts["bad_row"] += 1
            log.write(f"STAGE_FAILED\t{i}\t{src}\t{rel}\t{type(e).__name__}: {e}\n")

    return counts


def run_batch_rsync(
    staging_root: Path,
    dest_root: Path,
    dry_run: bool,
    verbose: bool,
    extra_rsync_args: List[str],
) -> int:
    dest_root.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rsync",
        "-aL",
        "--ignore-existing",
        "--human-readable",
        "--info=progress2",
        "--partial",
        "--protect-args",
    ]

    if dry_run:
        cmd.append("--dry-run")

    cmd.extend(extra_rsync_args)

    # trailing slash is important: copy contents of staging root into dest root
    cmd.append(str(staging_root) + "/")
    cmd.append(str(dest_root) + "/")

    print("=" * 80)
    print("BATCH RSYNC")
    print("=" * 80)
    print(" ".join(cmd))
    print("=" * 80)

    return subprocess.run(cmd).returncode


def verify_dest_root_from_plan(
    rows: List[Dict[str, str]],
    external_dest_root: Optional[Path],
) -> Optional[Path]:
    """
    If external dest root is provided, use it.
    Otherwise infer a common root from destination_path markers. If impossible,
    return None.
    """
    if external_dest_root is not None:
        return external_dest_root

    roots = set()
    for row in rows:
        dest = row.get("destination_path", "")
        parts = Path(dest).parts
        for marker in MARKERS:
            if marker in parts:
                idx = parts.index(marker)
                root = Path(*parts[: idx + 1])
                roots.add(str(root))
                break

    if len(roots) == 1:
        return Path(next(iter(roots)))

    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Efficient batch rsync executor for organization plans.")
    ap.add_argument("--plan", required=True, help="Input organization plan TSV")
    ap.add_argument("--log", required=True, help="Execution log path")
    ap.add_argument("--execute", action="store_true", help="Actually copy files. Default is dry-run.")
    ap.add_argument("--quiet", action="store_true", help="Reduce screen output during staging")

    ap.add_argument("--source-root", default=None, help="Analyzed source folder, e.g. .../Andre")
    ap.add_argument("--dest-parent", default=None, help="Parent where DEST_PREFIX_<source-name> is created")
    ap.add_argument("--dest-prefix", default="ORGANIZED_UNIQUE_CONTEXTUAL", help="Default: ORGANIZED_UNIQUE_CONTEXTUAL")
    ap.add_argument("--dest-root", default=None, help="Explicit external destination root. Overrides inference.")

    ap.add_argument("--workdir", default=None, help="Directory for staging symlink tree. Default: beside log file")
    ap.add_argument("--keep-staging", action="store_true", help="Do not delete staging symlink tree after rsync")
    ap.add_argument("--rsync-arg", action="append", default=[], help="Additional rsync argument; may be repeated")

    args = ap.parse_args()

    require_rsync()

    plan_path = Path(args.plan).expanduser().resolve()
    log_path = Path(args.log).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    source_root = Path(args.source_root).expanduser().resolve() if args.source_root else None
    dest_parent = Path(args.dest_parent).expanduser().resolve() if args.dest_parent else None

    if args.dest_root:
        external_dest_root = Path(args.dest_root).expanduser().resolve()
    else:
        external_dest_root = infer_external_dest_root(source_root, dest_parent, args.dest_prefix)

    rows = read_plan(plan_path)

    dest_root = verify_dest_root_from_plan(rows, external_dest_root)
    if dest_root is None:
        raise SystemExit(
            "ERROR: could not infer destination root from plan. "
            "Provide --dest-root or --source-root/--dest-parent."
        )

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if args.workdir:
        workdir = Path(args.workdir).expanduser().resolve()
    else:
        workdir = log_path.parent / "rsync_batch_work"
    staging_root = workdir / f"staging_{timestamp}"

    verbose = not args.quiet

    print("=" * 80)
    print("EXECUTE ORGANIZATION PLAN - BATCH RSYNC")
    print("=" * 80)
    print(f"Plan:        {plan_path}")
    print(f"Log:         {log_path}")
    print(f"Execute:     {args.execute}")
    print(f"Source root: {source_root if source_root else '(not provided)'}")
    print(f"Dest root:   {dest_root}")
    print(f"Staging:     {staging_root}")
    print("=" * 80)

    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"PLAN\t{plan_path}\n")
        log.write(f"EXECUTE\t{args.execute}\n")
        log.write(f"SOURCE_ROOT\t{source_root if source_root else ''}\n")
        log.write(f"DEST_ROOT\t{dest_root}\n")
        log.write(f"STAGING_ROOT\t{staging_root}\n")

        counts = build_staging_tree(
            rows=rows,
            staging_root=staging_root,
            external_dest_root=external_dest_root,
            log=log,
            verbose=verbose,
        )

        print("=" * 80)
        print("STAGING SUMMARY")
        print("=" * 80)
        for k, v in counts.items():
            print(f"{k}: {v}")
            log.write(f"{k.upper()}\t{v}\n")

        if counts["staged"] == 0:
            print("No files staged. Nothing to rsync.")
            log.write("NO_FILES_STAGED\n")
            return

        rc = run_batch_rsync(
            staging_root=staging_root,
            dest_root=dest_root,
            dry_run=not args.execute,
            verbose=verbose,
            extra_rsync_args=args.rsync_arg,
        )

        log.write(f"RSYNC_RETURN_CODE\t{rc}\n")

    if not args.keep_staging:
        safe_unlink_or_rmtree(staging_root)

    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print(f"Plan rows:       {len(rows)}")
    print(f"Files staged:    {counts['staged']}")
    print(f"Rsync rc:        {rc}")
    print(f"Executed:        {args.execute}")
    print(f"Destination:     {dest_root}")
    print(f"Log:             {log_path}")
    if args.keep_staging:
        print(f"Staging kept:    {staging_root}")
    print("=" * 80)

    if rc != 0:
        raise SystemExit(rc)


if __name__ == "__main__":
    main()
