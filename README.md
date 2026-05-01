# Backup Deduplication, Review, and Organization Workflow

This repository provides a safe, auditable workflow to inventory, deduplicate, review, and organize a large Linux backup without deleting original files automatically.

The workflow is built around three scripts:

```text
organize_backup_dedup.sh          # main inventory/dedup/planning workflow
interactive_plan_reviewer_v6.py  # browser-based plan reviewer and selector
execute_organization_plan.py     # explicit executor for a final reviewed plan
```

The design separates the process into three phases:

```text
1. Build data evidence     -> scan, inventory, hashes, duplicate reports
2. Build/review plans      -> type plan, contextual plan, interactive final plan
3. Execute chosen plan     -> create organized output, never delete originals
```

---

## 1. What this workflow does

The workflow can:

- recursively scan files under a backup root
- build a file inventory
- detect exact duplicates using file size + SHA256 hash
- select one keeper per duplicate group
- generate two alternative organization plans
- review both plans interactively in HTML
- export a final curated plan
- execute exactly that final plan

It does **not** automatically delete original files.

---

## 2. Safety model

The workflow is conservative by design.

It does **not**:

```text
delete original files
move original files
rename original files
modify file contents
overwrite existing destination files
quarantine duplicates automatically
```

The final execution step creates organized files from the selected plan. By default, the executor tries hardlinks first and falls back to copies. A copy-only mode is available if you want a fully independent output.

---

## 3. Exact duplicate definition

A file is considered a duplicate only if both conditions match:

```text
same file size
same SHA256 hash
```

Therefore:

```text
same filename + different content  -> NOT duplicate
different filename + same content  -> duplicate
```

---

## 4. Required dependencies

Install dependencies on Ubuntu/Linux:

```bash
sudo apt update
sudo apt install -y coreutils findutils gawk file python3 libimage-exiftool-perl rsync
```

The workflow uses standard Linux tools plus Python 3. `exiftool` is recommended for better media date detection.

---

## 5. Recommended project layout

Place the scripts in one directory, for example:

```text
backup_dedup_workflow/
  organize_backup_dedup.sh
  interactive_plan_reviewer_v6.py
  execute_organization_plan.py
  README.md
```

Make scripts executable:

```bash
chmod +x organize_backup_dedup.sh
chmod +x interactive_plan_reviewer_v6.py
chmod +x execute_organization_plan.py
```

---

## 6. Define ROOT and WORKDIR

For the whole HDD:

```bash
ROOT="/run/media/lucas/HDD"
WORKDIR="/run/media/lucas/HDD/dedup_audit/work"
```

For a smaller subset, for example only Andre:

```bash
ROOT="/run/media/lucas/HDD/to_do_backup_2023_02/Andre"
WORKDIR="/run/media/lucas/HDD/to_do_backup_2023_02/workdir_Andre"
```

The `ROOT` is the directory being analyzed. The `WORKDIR` stores reports, logs, temporary files, and generated plans.

---

## 7. Main script overview

The main script supports two organization strategies:

```text
--org-strategy type
--org-strategy contextual
```

### Type strategy

The type strategy organizes primarily by file type/category:

```text
ORGANIZED_UNIQUE/
  by_type/
    photos/
    videos/
    documents/
    bioinformatics/
    code/
    other/
```

### Contextual strategy

The contextual strategy preserves meaningful human folder context first. This avoids splitting folders like:

```text
Chloe/
  video.mp4
  notes.docx
  document.pdf
  photo.jpg
```

into unrelated folders by extension. Instead, contextual organization keeps that folder meaning together:

```text
ORGANIZED_UNIQUE_CONTEXTUAL/
  by_context/
    Andre/
      Andre_celu_11062022/
        Chloe/
          2022/
            video.mp4
          notes.docx
          document.pdf
          photo.jpg
```

If no meaningful context is detected, contextual mode falls back to type-based organization.

---

## 8. First full dry-run

Run one full pipeline first. Contextual is usually the better default for human backups:

```bash
bash organize_backup_dedup.sh \
  --root "$ROOT" \
  --workdir "$WORKDIR" \
  --org-strategy contextual \
  --threads 8
```

This runs:

```text
preflight -> scan -> inventory -> hash -> reports -> plan -> summary -> validate -> organize dry-run
```

Because `--execute` was not used, no organized files are created.

---

## 9. Important outputs from the first run

Reports are written under:

```text
$WORKDIR/reports/
```

Important shared reports:

```text
file_inventory.tsv
file_inventory_with_duplicates.tsv
duplicate_groups.tsv
duplicate_groups_readable.txt
proposed_keep_files.tsv
proposed_duplicate_files_to_quarantine.tsv
```

Contextual plan outputs:

```text
organization_plan_contextual.tsv
summary_statistics_contextual.txt
validation_report_contextual.txt
```

Logs are written under:

```text
$WORKDIR/logs/
```

---

## 10. Validate the contextual run

Check validation:

```bash
cat "$WORKDIR/reports/validation_report_contextual.txt"
```

Expected:

```text
Failed checks: 0
```

Review summary:

```bash
cat "$WORKDIR/reports/summary_statistics_contextual.txt"
```

Review duplicate groups:

```bash
less "$WORKDIR/reports/duplicate_groups_readable.txt"
```

---

## 11. Generate the alternative type plan without rehashing

After one full run exists, you do not need to scan and hash again. Generate the type-based plan from the existing inventory and duplicate reports:

```bash
bash organize_backup_dedup.sh \
  --root "$ROOT" \
  --workdir "$WORKDIR" \
  --org-strategy type \
  --resume-from plan \
  --threads 8
```

This generates:

```text
$WORKDIR/reports/organization_plan_type.tsv
$WORKDIR/reports/summary_statistics_type.txt
$WORKDIR/reports/validation_report_type.txt
```

If a plan already exists and you want to regenerate it, add `--force`:

```bash
bash organize_backup_dedup.sh \
  --root "$ROOT" \
  --workdir "$WORKDIR" \
  --org-strategy type \
  --resume-from plan \
  --force \
  --threads 8
```

---

## 12. Generate or regenerate contextual plan without rehashing

If you already ran the type strategy first and now want contextual:

```bash
bash organize_backup_dedup.sh \
  --root "$ROOT" \
  --workdir "$WORKDIR" \
  --org-strategy contextual \
  --resume-from plan \
  --threads 8
```

Use `--force` if you want to overwrite an existing contextual plan:

```bash
bash organize_backup_dedup.sh \
  --root "$ROOT" \
  --workdir "$WORKDIR" \
  --org-strategy contextual \
  --resume-from plan \
  --force \
  --threads 8
```

---

## 13. Plan schemas

Both `organization_plan_type.tsv` and `organization_plan_contextual.tsv` use the same columns:

```text
action
duplicate_status
source_absolute_path
source_path_b64
destination_path
organization_strategy
preserved_context
media_category
suspected_origin
year
size_bytes
hash
```

This is important because the same HTML reviewer can read both plans.

---

## 14. Generate the interactive HTML reviewer

After both plans exist, generate the interactive review page:

```bash
python3 interactive_plan_reviewer_v6.py \
  --plans \
    "$WORKDIR/reports/organization_plan_type.tsv" \
    "$WORKDIR/reports/organization_plan_contextual.tsv" \
  --out-html \
    "$WORKDIR/reports/interactive_plan_review_v6.html"
```

Open it:

```bash
xdg-open "$WORKDIR/reports/interactive_plan_review_v6.html"
```

---

## 15. What the HTML reviewer lets you do

The HTML reviewer is intended for human decision-making before execution.

It supports:

- global search
- filtering by plan source
- filtering by organization strategy
- filtering by media category
- filtering by origin
- filtering by year
- filtering by preserved context
- filtering by destination folder text
- selecting visible rows
- unselecting visible rows
- selecting all rows
- unselecting all rows
- inverting visible selection
- selecting/unselecting whole groups
- exporting a final selected plan

Group-level selection is available by:

```text
plan source
organization strategy
preserved context
destination folder
media category
suspected origin
year
```

This avoids selecting or removing files one by one.

---

## 16. Recommended review workflow in the HTML

A practical review strategy is:

1. Filter by `organization_strategy=contextual`.
2. Inspect destination folders.
3. Use the `Destination folder` group tab to remove full folders that should not be organized.
4. Use the `Context` group tab to include/exclude whole human contexts.
5. Use category/origin/year group tabs for broader cleanup.
6. Use individual row checkboxes only for exceptions.
7. Export the final selected TSV.

The browser downloads:

```text
organization_plan_final.tsv
```

Move it into the reports directory:

```bash
mv ~/Downloads/organization_plan_final.tsv \
  "$WORKDIR/reports/organization_plan_final.tsv"
```

---

## 17. Dry-run the final reviewed plan

Before creating anything, run the explicit executor without `--execute`:

```bash
python3 execute_organization_plan.py \
  --plan "$WORKDIR/reports/organization_plan_final.tsv" \
  --log "$WORKDIR/logs/execute_final_plan.log"
```

This does not create files. It logs what would happen.

Review the log:

```bash
less "$WORKDIR/logs/execute_final_plan.log"
```

Look for:

```text
FAILED
MISSING_SOURCE
```

---

## 18. Execute the final reviewed plan

After the dry-run log looks correct:

```bash
python3 execute_organization_plan.py \
  --plan "$WORKDIR/reports/organization_plan_final.tsv" \
  --log "$WORKDIR/logs/execute_final_plan.log" \
  --execute
```

By default, the executor tries:

```text
hardlink first
copy fallback
```

It never deletes originals and never overwrites existing destination files.

---

## 19. Execute as copy-only

If you want the organized output to be fully independent from the original files, use `--copy-only`:

```bash
python3 execute_organization_plan.py \
  --plan "$WORKDIR/reports/organization_plan_final.tsv" \
  --log "$WORKDIR/logs/execute_final_plan_copy_only.log" \
  --execute \
  --copy-only
```

This avoids hardlinks and creates independent copies.

Use this if your final goal is one independent organized dataset.

---

## 20. Hardlinks vs copies

Default execution creates hardlinks when possible.

A hardlink is not an independent copy. It is another path pointing to the same data on disk.

Check whether two paths are hardlinked:

```bash
ls -li /path/to/original/file /path/to/organized/file
```

Same inode number means hardlink. Different inode number means independent copy.

Important implications:

```text
Deleting one hardlink path does not delete data if another hardlink remains.
Editing one hardlinked file modifies the shared data.
For a fully independent final dataset, use --copy-only.
```

---

## 21. Verify the organized output

After execution, inspect created files:

```bash
find "$ROOT/ORGANIZED_UNIQUE_CONTEXTUAL" -type f -printf '%s\t%p\n' | head
```

or, for type strategy:

```bash
find "$ROOT/ORGANIZED_UNIQUE" -type f -printf '%s\t%p\n' | head
```

If your exported final plan mixes destinations from different plans, inspect the destination paths directly:

```bash
cut -f5 "$WORKDIR/reports/organization_plan_final.tsv" | head
```

Count planned files:

```bash
tail -n +2 "$WORKDIR/reports/organization_plan_final.tsv" | wc -l
```

Count created files under a destination root:

```bash
find "$ROOT/ORGANIZED_UNIQUE_CONTEXTUAL" -type f | wc -l
```

Review execution summary:

```bash
tail -n 20 "$WORKDIR/logs/execute_final_plan.log"
```

---

## 22. If something is wrong

If the destination structure is wrong before execution:

```text
Do not execute.
Go back to the HTML reviewer.
Adjust selected groups/rows.
Export a new organization_plan_final.tsv.
Dry-run again.
```

If you executed but the output destination is wrong, originals were not modified. You can remove only the generated organized destination folder and rebuild from the plan:

```bash
rm -rf "$ROOT/ORGANIZED_UNIQUE_CONTEXTUAL"
```

Only do that if you are sure the directory is generated output.

---

## 23. Do not delete duplicates automatically

The file:

```text
$WORKDIR/reports/proposed_duplicate_files_to_quarantine.tsv
```

is a proposal only.

Do not delete duplicates until:

```text
validation reports passed
the final organization plan was reviewed
the organized output was created correctly
sample files were opened/read successfully
you have a separate backup or confirmed final copy
```

---

## 24. Minimal command sequence for production use

```bash
ROOT="/run/media/lucas/HDD"
WORKDIR="/run/media/lucas/HDD/dedup_audit/work"
```

Run contextual full workflow:

```bash
bash organize_backup_dedup.sh \
  --root "$ROOT" \
  --workdir "$WORKDIR" \
  --org-strategy contextual \
  --threads 8
```

Generate type plan:

```bash
bash organize_backup_dedup.sh \
  --root "$ROOT" \
  --workdir "$WORKDIR" \
  --org-strategy type \
  --resume-from plan \
  --threads 8
```

Open review HTML:

```bash
python3 interactive_plan_reviewer_v6.py \
  --plans \
    "$WORKDIR/reports/organization_plan_type.tsv" \
    "$WORKDIR/reports/organization_plan_contextual.tsv" \
  --out-html \
    "$WORKDIR/reports/interactive_plan_review_v6.html"

xdg-open "$WORKDIR/reports/interactive_plan_review_v6.html"
```

Move exported final plan:

```bash
mv ~/Downloads/organization_plan_final.tsv \
  "$WORKDIR/reports/organization_plan_final.tsv"
```

Dry-run final execution:

```bash
python3 execute_organization_plan.py \
  --plan "$WORKDIR/reports/organization_plan_final.tsv" \
  --log "$WORKDIR/logs/execute_final_plan.log"
```

Execute final plan:

```bash
python3 execute_organization_plan.py \
  --plan "$WORKDIR/reports/organization_plan_final.tsv" \
  --log "$WORKDIR/logs/execute_final_plan.log" \
  --execute
```

For independent copies instead of hardlinks:

```bash
python3 execute_organization_plan.py \
  --plan "$WORKDIR/reports/organization_plan_final.tsv" \
  --log "$WORKDIR/logs/execute_final_plan_copy_only.log" \
  --execute \
  --copy-only
```

---

## 25. Mental model

```text
organize_backup_dedup.sh
  creates evidence and candidate organization plans

interactive_plan_reviewer_v6.py
  lets you inspect, filter, select, and export the final plan

execute_organization_plan.py
  applies exactly the final plan you provide
```

The final executor does not guess. It uses exactly the file passed with:

```bash
--plan organization_plan_final.tsv
```


# execute_organization_plan_rsync_batch_v3

Fast batch executor for final reviewed organization plans.

## Why this version is faster

The previous executor launched one `rsync` process per file.

This version:

1. reads the plan,
2. creates a temporary symlink tree with the final organized structure,
3. runs **one single rsync**:

```bash
rsync -aL --ignore-existing --info=progress2 STAGING/ DEST_ROOT/
```

The `-L` option dereferences symlinks, so the destination receives **real independent files**, not symlinks.

## Dry-run

```bash
python3 execute_organization_plan_rsync_batch_v3.py \
  --plan work_Andre/reports/organization_plan_final.tsv \
  --log work_Andre/execute_batch.log \
  --source-root /run/media/lucas/HDD/to_do_backup_2023_02/Andre \
  --dest-parent /run/media/lucas/HDD/to_do_backup_2023_02 \
  --dest-prefix ORGANIZED_UNIQUE_CONTEXTUAL
```

No files are copied without `--execute`.

## Execute

```bash
python3 execute_organization_plan_rsync_batch_v3.py \
  --plan work_Andre/reports/organization_plan_final.tsv \
  --log work_Andre/execute_batch.log \
  --execute \
  --source-root /run/media/lucas/HDD/to_do_backup_2023_02/Andre \
  --dest-parent /run/media/lucas/HDD/to_do_backup_2023_02 \
  --dest-prefix ORGANIZED_UNIQUE_CONTEXTUAL
```

This copies to:

```text
/run/media/lucas/HDD/to_do_backup_2023_02/ORGANIZED_UNIQUE_CONTEXTUAL_Andre
```

## Explicit destination root

```bash
python3 execute_organization_plan_rsync_batch_v3.py \
  --plan work_Andre/reports/organization_plan_final.tsv \
  --log work_Andre/execute_batch.log \
  --execute \
  --dest-root /run/media/lucas/HDD/to_do_backup_2023_02/ORGANIZED_UNIQUE_CONTEXTUAL_Andre
```

## Extra rsync flags

You can pass extra rsync args:

```bash
--rsync-arg --checksum
```

or:

```bash
--rsync-arg --bwlimit=50M
```

## Safety

- Dry-run by default
- Does not delete originals
- Does not move originals
- Does not overwrite destination files
- Uses `--ignore-existing`
- Destination can be outside analyzed folder
- Copies are independent files

## Notes

The staging directory contains symlinks only. It is temporary and deleted after rsync unless `--keep-staging` is used.

