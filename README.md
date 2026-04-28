# Backup Deduplication Workflow v4

v4 integrates both organization strategies into the main script:

```text
--org-strategy type
--org-strategy contextual
```

Both produce plans with the same schema, so the same visualizer can be used.

## Strategy 1: type

```bash
bash organize_backup_dedup.sh \
  --root /run/media/lucas/HDD \
  --workdir /run/media/lucas/HDD/dedup_audit/work \
  --org-strategy type \
  --threads 8
```

Output plan:

```text
reports/organization_plan_type.tsv
```

Destination if executed:

```text
/run/media/lucas/HDD/ORGANIZED_UNIQUE
```

## Strategy 2: contextual

```bash
bash organize_backup_dedup.sh \
  --root /run/media/lucas/HDD \
  --workdir /run/media/lucas/HDD/dedup_audit/work \
  --org-strategy contextual \
  --threads 8
```

Output plan:

```text
reports/organization_plan_contextual.tsv
```

Destination if executed:

```text
/run/media/lucas/HDD/ORGANIZED_UNIQUE_CONTEXTUAL
```

## Same TSV schema for both plans

Both plans contain:

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

This allows the same visualizer to read either plan.

## Visualize type plan

```bash
python3 visualize_any_organization_plan.py \
  --plan /run/media/lucas/HDD/dedup_audit/work/reports/organization_plan_type.tsv \
  --outdir /run/media/lucas/HDD/dedup_audit/work/reports/plan_review_type \
  --root-label ORGANIZED_UNIQUE
```

## Visualize contextual plan

```bash
python3 visualize_any_organization_plan.py \
  --plan /run/media/lucas/HDD/dedup_audit/work/reports/organization_plan_contextual.tsv \
  --outdir /run/media/lucas/HDD/dedup_audit/work/reports/plan_review_contextual \
  --root-label ORGANIZED_UNIQUE_CONTEXTUAL
```

## Execute selected strategy

Type:

```bash
bash organize_backup_dedup.sh \
  --root /run/media/lucas/HDD \
  --workdir /run/media/lucas/HDD/dedup_audit/work \
  --org-strategy type \
  --resume-from organize \
  --execute
```

Contextual:

```bash
bash organize_backup_dedup.sh \
  --root /run/media/lucas/HDD \
  --workdir /run/media/lucas/HDD/dedup_audit/work \
  --org-strategy contextual \
  --resume-from organize \
  --execute
```

## Why contextual exists

Type-based organization can split a meaningful folder:

```text
Chloe/
  video.mp4
  notes.docx
  document.pdf
  photo.jpg
```

into:

```text
videos/
documents/by_extension/
photos/
```

Contextual organization preserves folder meaning first:

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

If no context is detected, it falls back to `by_type/`.

## Safety

- No originals are deleted.
- No files are moved.
- `--execute` creates hardlinks if possible, otherwise copies.
- Duplicates are excluded from organized output.
