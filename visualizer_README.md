# Organization Plan Visualizer

Auxiliary tool to review `organization_plan.tsv` before running `--execute`.

It does **not** move, copy, delete, or modify files.  
It only reads the plan and generates human-readable reports.

## Usage

```bash
python3 visualize_organization_plan.py \
  --plan /run/media/lucas/HDD/dedup_audit/work/reports/organization_plan.tsv \
  --outdir /run/media/lucas/HDD/dedup_audit/work/reports/organization_review \
  --root /run/media/lucas/HDD/ORGANIZED_UNIQUE
```

## Outputs

```text
organization_review/
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
```

## Open HTML report

```bash
xdg-open /run/media/lucas/HDD/dedup_audit/work/reports/organization_review/organization_plan_review.html
```

## Inspect terminal reports

```bash
less /run/media/lucas/HDD/dedup_audit/work/reports/organization_review/destination_tree.txt

column -t -s $'\t' \
  /run/media/lucas/HDD/dedup_audit/work/reports/organization_review/summary_by_top_destination.tsv \
  | less -S

column -t -s $'\t' \
  /run/media/lucas/HDD/dedup_audit/work/reports/organization_review/largest_files.tsv \
  | less -S
```

## Why this helps

`organization_plan.tsv` is machine-readable but difficult to inspect manually.

This visualizer gives you:

- destination tree preview
- file counts by category
- file counts by origin
- file counts by year
- largest planned files
- samples from each destination folder
- destination names that look like collision-resolved files, e.g. `file__hash.txt`

Use this before `--execute`.
