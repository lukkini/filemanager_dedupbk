#!/usr/bin/env python3
"""
visualize_organization_plan.py

Generate human-readable review reports from organization_plan.tsv.

Input:
  organization_plan.tsv

Outputs:
  review/
    organization_plan_review.html
    destination_tree.txt
    summary_by_category.tsv
    summary_by_origin.tsv
    summary_by_year.tsv
    summary_by_top_destination.tsv
    largest_files.tsv
    sample_files_by_destination.tsv
    filename_collision_like_paths.tsv
    README_review.txt

Usage:
  python3 visualize_organization_plan.py \
    --plan /run/media/lucas/HDD/dedup_audit/work/reports/organization_plan.tsv \
    --outdir /run/media/lucas/HDD/dedup_audit/work/reports/organization_review

Optional:
  --root /run/media/lucas/HDD/ORGANIZED_UNIQUE
  --max-tree-files 5000
  --sample-per-dir 10
  --largest 100
"""

from __future__ import annotations

import argparse
import csv
import html
import os
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Any


def human_size(n: int) -> str:
    x = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if x < 1024:
            return f"{x:.2f} {unit}"
        x /= 1024
    return f"{x:.2f} EB"


def read_plan(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    required = {
        "action",
        "duplicate_status",
        "source_absolute_path",
        "destination_path",
        "media_category",
        "suspected_origin",
        "year",
        "size_bytes",
        "hash",
    }
    missing = required - set(rows[0].keys()) if rows else required
    if missing:
        raise SystemExit(f"ERROR: organization_plan.tsv is missing columns: {sorted(missing)}")
    return rows


def write_tsv(path: Path, rows: List[Dict[str, Any]], fields: List[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, delimiter="\t", fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def common_prefix(paths: List[str]) -> str:
    if not paths:
        return ""
    return os.path.commonpath(paths)


def destination_rel(path: str, root_hint: str | None = None, prefix: str | None = None) -> str:
    p = Path(path)
    if root_hint:
        try:
            return str(p.relative_to(root_hint))
        except Exception:
            pass
    if prefix:
        try:
            return os.path.relpath(path, prefix)
        except Exception:
            pass
    return str(p)


def build_tree(paths: List[str], max_files: int = 5000) -> str:
    """
    Build a readable pseudo-tree from destination paths.
    Does not require files to exist.
    """
    tree = {}
    truncated = False

    for i, p in enumerate(paths):
        if i >= max_files:
            truncated = True
            break
        parts = Path(p).parts
        node = tree
        for part in parts:
            node = node.setdefault(part, {})

    lines = []

    def rec(node, prefix=""):
        items = sorted(node.items(), key=lambda kv: (bool(kv[1]), kv[0].lower()))
        for idx, (name, child) in enumerate(items):
            connector = "└── " if idx == len(items) - 1 else "├── "
            lines.append(prefix + connector + name)
            extension = "    " if idx == len(items) - 1 else "│   "
            if child:
                rec(child, prefix + extension)

    rec(tree)
    if truncated:
        lines.append("")
        lines.append(f"[TRUNCATED] Tree limited to first {max_files} files.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate human-readable review reports from organization_plan.tsv"
    )
    parser.add_argument("--plan", required=True, help="Path to organization_plan.tsv")
    parser.add_argument("--outdir", required=True, help="Output directory for review files")
    parser.add_argument("--root", default=None, help="Optional organized root, e.g. ROOT/ORGANIZED_UNIQUE")
    parser.add_argument("--max-tree-files", type=int, default=5000)
    parser.add_argument("--sample-per-dir", type=int, default=10)
    parser.add_argument("--largest", type=int, default=100)
    args = parser.parse_args()

    plan_path = Path(args.plan).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    rows = read_plan(plan_path)
    if not rows:
        raise SystemExit("ERROR: organization_plan.tsv has no rows.")

    for r in rows:
        try:
            r["_size_int"] = int(r.get("size_bytes") or 0)
        except ValueError:
            r["_size_int"] = 0

    dests = [r["destination_path"] for r in rows]
    prefix = common_prefix(dests)
    root_hint = args.root

    total_files = len(rows)
    total_size = sum(r["_size_int"] for r in rows)

    by_category = Counter(r["media_category"] or "unknown" for r in rows)
    by_origin = Counter(r["suspected_origin"] or "unknown" for r in rows)
    by_year = Counter(r["year"] or "unknown_year" for r in rows)
    by_status = Counter(r["duplicate_status"] or "unknown" for r in rows)

    # Top destination directory grouping: first 2 or 3 logical path parts under ORGANIZED_UNIQUE.
    by_top_destination = defaultdict(lambda: {"files": 0, "bytes": 0})
    by_dir = defaultdict(list)

    for r in rows:
        rel = destination_rel(r["destination_path"], root_hint=root_hint, prefix=prefix)
        parts = Path(rel).parts
        if len(parts) >= 3:
            top = str(Path(*parts[:3]))
        elif parts:
            top = str(Path(*parts))
        else:
            top = "."
        by_top_destination[top]["files"] += 1
        by_top_destination[top]["bytes"] += r["_size_int"]

        parent = str(Path(rel).parent)
        by_dir[parent].append(r)

    write_tsv(
        outdir / "summary_by_category.tsv",
        [
            {"media_category": k, "files": v, "size_bytes": sum(r["_size_int"] for r in rows if (r["media_category"] or "unknown") == k),
             "size_human": human_size(sum(r["_size_int"] for r in rows if (r["media_category"] or "unknown") == k))}
            for k, v in by_category.most_common()
        ],
        ["media_category", "files", "size_bytes", "size_human"],
    )

    write_tsv(
        outdir / "summary_by_origin.tsv",
        [
            {"suspected_origin": k, "files": v, "size_bytes": sum(r["_size_int"] for r in rows if (r["suspected_origin"] or "unknown") == k),
             "size_human": human_size(sum(r["_size_int"] for r in rows if (r["suspected_origin"] or "unknown") == k))}
            for k, v in by_origin.most_common()
        ],
        ["suspected_origin", "files", "size_bytes", "size_human"],
    )

    write_tsv(
        outdir / "summary_by_year.tsv",
        [
            {"year": k, "files": v, "size_bytes": sum(r["_size_int"] for r in rows if (r["year"] or "unknown_year") == k),
             "size_human": human_size(sum(r["_size_int"] for r in rows if (r["year"] or "unknown_year") == k))}
            for k, v in sorted(by_year.items(), key=lambda kv: str(kv[0]))
        ],
        ["year", "files", "size_bytes", "size_human"],
    )

    write_tsv(
        outdir / "summary_by_top_destination.tsv",
        [
            {"destination_group": k, "files": v["files"], "size_bytes": v["bytes"], "size_human": human_size(v["bytes"])}
            for k, v in sorted(by_top_destination.items(), key=lambda kv: (-kv[1]["files"], kv[0]))
        ],
        ["destination_group", "files", "size_bytes", "size_human"],
    )

    largest = sorted(rows, key=lambda r: r["_size_int"], reverse=True)[: args.largest]
    write_tsv(
        outdir / "largest_files.tsv",
        [
            {
                "size_human": human_size(r["_size_int"]),
                "size_bytes": r["_size_int"],
                "media_category": r["media_category"],
                "suspected_origin": r["suspected_origin"],
                "year": r["year"],
                "source_absolute_path": r["source_absolute_path"],
                "destination_path": r["destination_path"],
            }
            for r in largest
        ],
        ["size_human", "size_bytes", "media_category", "suspected_origin", "year", "source_absolute_path", "destination_path"],
    )

    sample_rows = []
    for dirname, items in sorted(by_dir.items()):
        for r in items[: args.sample_per_dir]:
            sample_rows.append(
                {
                    "destination_dir": dirname,
                    "filename": Path(r["destination_path"]).name,
                    "size_human": human_size(r["_size_int"]),
                    "media_category": r["media_category"],
                    "suspected_origin": r["suspected_origin"],
                    "year": r["year"],
                    "source_absolute_path": r["source_absolute_path"],
                    "destination_path": r["destination_path"],
                }
            )

    write_tsv(
        outdir / "sample_files_by_destination.tsv",
        sample_rows,
        [
            "destination_dir",
            "filename",
            "size_human",
            "media_category",
            "suspected_origin",
            "year",
            "source_absolute_path",
            "destination_path",
        ],
    )

    # Collision-like files: names with __hash suffix generated by collision handling.
    collision_like = []
    for r in rows:
        name = Path(r["destination_path"]).name
        stem = Path(name).stem
        if "__" in stem:
            collision_like.append(
                {
                    "destination_filename": name,
                    "size_human": human_size(r["_size_int"]),
                    "media_category": r["media_category"],
                    "source_absolute_path": r["source_absolute_path"],
                    "destination_path": r["destination_path"],
                }
            )

    write_tsv(
        outdir / "filename_collision_like_paths.tsv",
        collision_like,
        ["destination_filename", "size_human", "media_category", "source_absolute_path", "destination_path"],
    )

    rel_dests = [destination_rel(d, root_hint=root_hint, prefix=prefix) for d in dests]
    tree_text = build_tree(rel_dests, max_files=args.max_tree_files)
    (outdir / "destination_tree.txt").write_text(tree_text, encoding="utf-8")

    # HTML report.
    def table_html(title: str, tsv_path: Path, max_rows: int = 30) -> str:
        with tsv_path.open("r", encoding="utf-8", newline="") as f:
            data = list(csv.DictReader(f, delimiter="\t"))
        if not data:
            return f"<h2>{html.escape(title)}</h2><p>No rows.</p>"
        fields = data[0].keys()
        rows_html = []
        for row in data[:max_rows]:
            rows_html.append(
                "<tr>" + "".join(f"<td>{html.escape(str(row.get(c, '')))}</td>" for c in fields) + "</tr>"
            )
        header = "<tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in fields) + "</tr>"
        more = "" if len(data) <= max_rows else f"<p><em>Showing {max_rows} of {len(data)} rows. See TSV file for full table.</em></p>"
        return f"<h2>{html.escape(title)}</h2>{more}<table>{header}{''.join(rows_html)}</table>"

    html_report = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Organization Plan Review</title>
<style>
body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; line-height: 1.45; }}
h1, h2 {{ color: #222; }}
.code, pre {{ background: #f5f5f5; padding: 1rem; border-radius: 8px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; font-size: 0.9rem; }}
th, td {{ border: 1px solid #ddd; padding: 0.4rem; vertical-align: top; }}
th {{ background: #eee; position: sticky; top: 0; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }}
.card {{ border: 1px solid #ddd; border-radius: 10px; padding: 1rem; background: #fafafa; }}
.small {{ color: #666; font-size: 0.9rem; }}
</style>
</head>
<body>
<h1>Organization Plan Review</h1>

<div class="summary">
  <div class="card"><strong>Total planned files</strong><br>{total_files}</div>
  <div class="card"><strong>Total planned size</strong><br>{human_size(total_size)}</div>
  <div class="card"><strong>Common destination prefix</strong><br><span class="small">{html.escape(prefix)}</span></div>
  <div class="card"><strong>Collision-like destination names</strong><br>{len(collision_like)}</div>
</div>

<h2>Status distribution</h2>
<pre>{html.escape(chr(10).join(f"{k}: {v}" for k, v in by_status.items()))}</pre>

{table_html("Summary by category", outdir / "summary_by_category.tsv")}
{table_html("Summary by origin", outdir / "summary_by_origin.tsv")}
{table_html("Summary by year", outdir / "summary_by_year.tsv")}
{table_html("Summary by top destination", outdir / "summary_by_top_destination.tsv")}
{table_html("Largest files", outdir / "largest_files.tsv", max_rows=50)}
{table_html("Filename collision-like paths", outdir / "filename_collision_like_paths.tsv", max_rows=50)}

<h2>Destination tree preview</h2>
<p class="small">Limited to first {args.max_tree_files} destination files. Full tree preview is also saved as destination_tree.txt.</p>
<pre>{html.escape(tree_text[:200000])}</pre>

</body>
</html>
"""
    (outdir / "organization_plan_review.html").write_text(html_report, encoding="utf-8")

    readme = f"""Organization plan review
========================

Input plan:
  {plan_path}

Output directory:
  {outdir}

Generated files:
  organization_plan_review.html
  destination_tree.txt
  summary_by_category.tsv
  summary_by_origin.tsv
  summary_by_year.tsv
  summary_by_top_destination.tsv
  largest_files.tsv
  sample_files_by_destination.tsv
  filename_collision_like_paths.tsv

How to inspect:
  xdg-open {outdir / "organization_plan_review.html"}
  less {outdir / "destination_tree.txt"}
  column -t -s $'\\t' {outdir / "summary_by_top_destination.tsv"} | less -S

Important:
  This script does not modify files.
  It only reads organization_plan.tsv and creates review reports.
"""
    (outdir / "README_review.txt").write_text(readme, encoding="utf-8")

    print(f"Review generated in: {outdir}")
    print(f"Open HTML report: {outdir / 'organization_plan_review.html'}")
    print(f"Tree preview: {outdir / 'destination_tree.txt'}")


if __name__ == "__main__":
    main()
