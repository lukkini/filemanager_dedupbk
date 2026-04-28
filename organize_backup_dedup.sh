#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

VERSION="2026-04-27-hardened-v4-contextual"

ROOT="/run/media/lucas/HDD"
WORKDIR=""
THREADS="${THREADS:-$(nproc 2>/dev/null || echo 1)}"
RUN=0
EXIF=1
FORCE=0
RESUME_FROM="scan"
ONLY_STAGE=""
SKIP_SPACE_CHECK=0
MIN_FREE_MB=512
ORG_STRATEGY="type"
ORGANIZED_NAME=""

print_help() {
cat <<'EOF'
Usage:
  bash organize_backup_dedup.sh [options]

Main options:
  --root PATH
  --workdir PATH
  --threads N
  --resume-from STAGE          scan | inventory | hash | reports | plan | summary | validate | organize
  --only STAGE                 preflight | scan | inventory | hash | reports | plan | summary | validate | organize
  --execute
  --dry-run
  --force
  --no-exif

Organization options:
  --org-strategy STRATEGY      type | contextual
                              type: original v3-style organization by category/type
                              contextual: preserve meaningful human folder context first

  --organized-name NAME        Destination folder name under ROOT.
                              Default:
                                ORGANIZED_UNIQUE for --org-strategy type
                                ORGANIZED_UNIQUE_CONTEXTUAL for --org-strategy contextual

Safety:
  --skip-space-check
  --min-free-mb N

Examples:
  Dry-run with original type-based organization:
    bash organize_backup_dedup.sh \
      --root /run/media/lucas/HDD \
      --workdir /run/media/lucas/HDD/dedup_audit/work \
      --org-strategy type \
      --threads 8

  Dry-run with contextual organization:
    bash organize_backup_dedup.sh \
      --root /run/media/lucas/HDD \
      --workdir /run/media/lucas/HDD/dedup_audit/work \
      --org-strategy contextual \
      --threads 8

  Execute contextual organization after review:
    bash organize_backup_dedup.sh \
      --root /run/media/lucas/HDD \
      --workdir /run/media/lucas/HDD/dedup_audit/work \
      --org-strategy contextual \
      --resume-from organize \
      --execute
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        --threads) THREADS="$2"; shift 2 ;;
        --resume-from) RESUME_FROM="$2"; shift 2 ;;
        --only) ONLY_STAGE="$2"; shift 2 ;;
        --execute) RUN=1; shift ;;
        --dry-run) RUN=0; shift ;;
        --force) FORCE=1; shift ;;
        --no-exif) EXIF=0; shift ;;
        --skip-space-check) SKIP_SPACE_CHECK=1; shift ;;
        --min-free-mb) MIN_FREE_MB="$2"; shift 2 ;;
        --org-strategy) ORG_STRATEGY="$2"; shift 2 ;;
        --organized-name) ORGANIZED_NAME="$2"; shift 2 ;;
        --help|-h) print_help; exit 0 ;;
        *) echo "[ERROR] Unknown option: $1" >&2; print_help; exit 1 ;;
    esac
done

abs_path() {
    local p="$1"
    if [[ "$p" = /* ]]; then printf '%s\n' "$p"; else printf '%s\n' "$(pwd -P)/$p"; fi
}

ROOT="$(abs_path "$ROOT")"; ROOT="${ROOT%/}"
if [[ -z "$WORKDIR" ]]; then WORKDIR="$ROOT/dedup_audit/work"; else WORKDIR="$(abs_path "$WORKDIR")"; fi
WORKDIR="${WORKDIR%/}"

case "$ORG_STRATEGY" in
    type|contextual) ;;
    *) echo "[ERROR] --org-strategy must be type or contextual" >&2; exit 1 ;;
esac

if [[ -z "$ORGANIZED_NAME" ]]; then
    if [[ "$ORG_STRATEGY" == "contextual" ]]; then
        ORGANIZED_NAME="ORGANIZED_UNIQUE_CONTEXTUAL"
    else
        ORGANIZED_NAME="ORGANIZED_UNIQUE"
    fi
fi

AUDIT_ROOT="$ROOT/dedup_audit"
ORGANIZED_DIR="$ROOT/$ORGANIZED_NAME"

LOG_DIR="$WORKDIR/logs"
TMP_DIR="$WORKDIR/tmp"
REPORT_DIR="$WORKDIR/reports"
STATE_DIR="$WORKDIR/state"
LOCK_FILE="$WORKDIR/.workflow.lock"

PLAN_SUFFIX="$ORG_STRATEGY"
LOG="$LOG_DIR/workflow_${PLAN_SUFFIX}.log"
PREFLIGHT_REPORT="$REPORT_DIR/preflight_report_${PLAN_SUFFIX}.txt"

ALL_FILES_NUL="$TMP_DIR/all_files.nul"
ALL_FILES_PATHS="$TMP_DIR/all_files.paths"
INVENTORY="$REPORT_DIR/file_inventory.tsv"
HASH_CANDIDATES="$TMP_DIR/hash_candidates.b64"
HASHES="$REPORT_DIR/hash_candidates.tsv"
INVENTORY_FINAL="$REPORT_DIR/file_inventory_with_duplicates.tsv"
DUP_GROUPS="$REPORT_DIR/duplicate_groups.tsv"
DUP_READABLE="$REPORT_DIR/duplicate_groups_readable.txt"
KEEPERS="$REPORT_DIR/proposed_keep_files.tsv"
DUPS_TO_QUARANTINE="$REPORT_DIR/proposed_duplicate_files_to_quarantine.tsv"

ORG_PLAN="$REPORT_DIR/organization_plan_${PLAN_SUFFIX}.tsv"
SUMMARY="$REPORT_DIR/summary_statistics_${PLAN_SUFFIX}.txt"
VALIDATION="$REPORT_DIR/validation_report_${PLAN_SUFFIX}.txt"
EXEC_LOG="$LOG_DIR/organization_execute_${PLAN_SUFFIX}.log"

die() { echo "[ERROR] $*" | tee -a "${LOG:-/dev/stderr}" >&2; exit 1; }
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

valid_stage() {
    case "$1" in preflight|scan|inventory|hash|reports|plan|summary|validate|organize) return 0 ;; *) return 1 ;; esac
}

preflight_before_mkdir() {
    [[ -d "$ROOT" ]] || die "ROOT does not exist or is not a directory: $ROOT"
    [[ "$ROOT" != "/" ]] || die "Refusing to process / as ROOT"
    if [[ -e "$WORKDIR" && ! -d "$WORKDIR" ]]; then die "--workdir exists but is not a directory: $WORKDIR"; fi
    if [[ -e "$ORGANIZED_DIR" && ! -d "$ORGANIZED_DIR" ]]; then die "Organized path exists but is not a directory: $ORGANIZED_DIR"; fi
    [[ "$THREADS" =~ ^[0-9]+$ && "$THREADS" -ge 1 ]] || die "--threads must be >= 1"
    [[ "$MIN_FREE_MB" =~ ^[0-9]+$ && "$MIN_FREE_MB" -ge 1 ]] || die "--min-free-mb must be >= 1"
    valid_stage "$RESUME_FROM" || die "Invalid --resume-from: $RESUME_FROM"
    [[ -z "$ONLY_STAGE" ]] || valid_stage "$ONLY_STAGE" || die "Invalid --only: $ONLY_STAGE"
}

bootstrap() { mkdir -p "$LOG_DIR" "$TMP_DIR" "$REPORT_DIR" "$STATE_DIR"; : > "$LOG"; }

acquire_lock() {
    if [[ -e "$LOCK_FILE" ]]; then
        old_pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
        if [[ "$old_pid" =~ ^[0-9]+$ && -d "/proc/$old_pid" ]]; then die "Another run is active: PID $old_pid"; fi
        rm -f "$LOCK_FILE"
    fi
    echo "$$" > "$LOCK_FILE"
}
release_lock() { rm -f "$LOCK_FILE" 2>/dev/null || true; }
trap release_lock EXIT
trap 'echo "[ERROR] Script failed at line $LINENO. See log: '"$LOG"'" >&2' ERR

check_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing dependency: $1"; }

stage_preflight() {
    log "PREFLIGHT started"
    for c in find stat awk sort uniq file base64 python3 sha256sum df; do check_cmd "$c"; done
    if [[ "$EXIF" == "1" ]] && ! command -v exiftool >/dev/null 2>&1; then
        log "WARNING: exiftool not found. Continuing with EXIF disabled."
        EXIF=0
    fi
    [[ -r "$ROOT" ]] || die "ROOT is not readable: $ROOT"
    [[ -w "$WORKDIR" ]] || die "WORKDIR is not writable: $WORKDIR"
    if [[ "$SKIP_SPACE_CHECK" == "0" ]]; then
        free_mb="$(df -Pm "$WORKDIR" | awk 'NR==2 {print $4}')"
        [[ -n "$free_mb" ]] || die "Could not determine free space"
        (( free_mb >= MIN_FREE_MB )) || die "Insufficient free space: ${free_mb} MB"
        log "Free space check passed: ${free_mb} MB available"
    fi
    {
        echo "Preflight report"
        echo "================"
        echo "Version: $VERSION"
        echo "ROOT: $ROOT"
        echo "WORKDIR: $WORKDIR"
        echo "ORG_STRATEGY: $ORG_STRATEGY"
        echo "ORGANIZED_DIR: $ORGANIZED_DIR"
        echo "THREADS: $THREADS"
        echo "RUN: $RUN"
        echo "EXIF: $EXIF"
    } > "$PREFLIGHT_REPORT"
    log "PREFLIGHT finished: $PREFLIGHT_REPORT"
}

mark_done() { date '+%F %T' > "$STATE_DIR/${1}_${PLAN_SUFFIX}.done"; }
require_file() { [[ -s "$1" ]] || die "$2 Missing or empty file: $1"; }
should_reuse() { [[ "$FORCE" == "0" && -s "$1" ]]; }

stage_scan() {
    if should_reuse "$ALL_FILES_NUL"; then log "SCAN already exists. Reusing: $ALL_FILES_NUL"; return; fi
    log "SCAN started"
    find "$ROOT" \( -path "$AUDIT_ROOT" -o -path "$AUDIT_ROOT/*" -o -path "$ROOT/ORGANIZED_UNIQUE" -o -path "$ROOT/ORGANIZED_UNIQUE/*" -o -path "$ROOT/ORGANIZED_UNIQUE_CONTEXTUAL" -o -path "$ROOT/ORGANIZED_UNIQUE_CONTEXTUAL/*" \) -prune -o -type f -print0 > "$ALL_FILES_NUL"
    tr '\0' '\n' < "$ALL_FILES_NUL" > "$ALL_FILES_PATHS"
    n="$(tr -cd '\0' < "$ALL_FILES_NUL" | wc -c)"
    log "SCAN finished. Files found: $n"
    mark_done scan
}

stage_inventory() {
    require_file "$ALL_FILES_NUL" "Cannot build inventory before scan."
    if should_reuse "$INVENTORY"; then log "INVENTORY already exists. Reusing: $INVENTORY"; return; fi
    log "INVENTORY started"
    python3 - "$ROOT" "$ALL_FILES_NUL" "$INVENTORY" "$EXIF" <<'PY'
import sys, os, csv, subprocess, base64, re
from datetime import datetime
root, nul_file, out_file, exif_enabled = sys.argv[1:]
exif_enabled = exif_enabled == "1"
photo_ext={"jpg","jpeg","png","heic","heif","tif","tiff","webp","bmp","gif","raw","cr2","nef","arw","dng"}
video_ext={"mp4","mov","avi","m4v","mkv","3gp","3g2","wmv","flv","webm","mts","m2ts","mpeg","mpg"}
doc_ext={"pdf","doc","docx","odt","rtf","txt","md","xls","xlsx","ods","ppt","pptx","csv","tsv"}
archive_ext={"zip","rar","7z","tar","gz","bz2","xz","tgz","tbz","txz"}
bio_ext={"fa","fasta","fna","faa","ffn","fastq","fq","sam","bam","cram","vcf","bcf","gff","gff3","gtf","bed","bedgraph","wig","bw","bigwig","aln","afa","phy","phylip","nwk","tree","gb","gbk","embl"}
code_ext={"sh","bash","py","r","rmd","pl","pm","js","ts","html","css","json","yaml","yml","xml","java","c","cpp","h","hpp","go","rs","sql","php","rb","ipynb","nf","smk","snakefile"}
def clean(s): return str(s).replace("\t"," ").replace("\n","\\n").replace("\r","\\r")
def b64_path(p): return base64.b64encode(p.encode("utf-8",errors="surrogateescape")).decode("ascii")
def mime_type(p):
    try: return subprocess.check_output(["file","-b","--mime-type",p], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception: return "unknown"
def category(ext,mime):
    if ext in photo_ext: return "photo"
    if ext in video_ext: return "video"
    if ext in doc_ext: return "document"
    if ext in archive_ext: return "archive"
    if ext in bio_ext: return "bioinformatics"
    if ext in code_ext: return "code"
    if mime.startswith("image/"): return "photo"
    if mime.startswith("video/"): return "video"
    if mime.startswith("text/"): return "document"
    return "other"
def origin(path, filename):
    p=path.lower(); f=filename.lower()
    if "whatsapp" in p or re.search(r"^(img|vid)-\d{8}-wa\d+", filename, re.I): return "whatsapp"
    if "screenshot" in f or "captura" in f or "screen shot" in f: return "screenshot"
    if "recovered" in p or "found." in p or "lost.dir" in p: return "recovered"
    if "download" in p or "descargas" in p: return "download"
    if "backup" in p or "bckup" in p or "windows7_pc" in p or "to_do_backup" in p: return "backup"
    if "dcim" in p or "camera" in p or re.search(r"^(dsc|img_|mov_)", filename, re.I): return "camera"
    return "unknown"
def owner(top):
    t=top.lower()
    if "andre" in t: return "Andre"
    if "echinococcus" in t: return "Echinococcus_project"
    if "eoligarthrus" in t: return "Eoligarthrus_project"
    if "fotos" in t or "imagenes" in t: return "photos_collection"
    if "windows7" in t: return "Windows7_PC_casa"
    if "backup" in t or "bckup" in t: return "backup_collection"
    if "recovered" in t: return "recovered_files"
    return "unknown"
def date_from_filename(filename):
    for pat in [r"([12]\d{3})[-_]?([01]\d)[-_]?([0-3]\d)", r"(?:IMG|VID)-([12]\d{3})([01]\d)([0-3]\d)-WA"]:
        m=re.search(pat, filename, re.I)
        if m: return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""
def exif_date(path):
    if not exif_enabled: return ""
    try:
        out=subprocess.check_output(["exiftool","-s3","-d","%Y-%m-%d","-DateTimeOriginal","-CreateDate","-MediaCreateDate","-ModifyDate",path], text=True, stderr=subprocess.DEVNULL, timeout=20)
        for line in out.splitlines():
            line=line.strip()
            if re.match(r"^[12]\d{3}-[01]\d-[0-3]\d$", line): return line
    except Exception: return ""
    return ""
with open(nul_file,"rb") as f: paths=[p.decode("utf-8",errors="surrogateescape") for p in f.read().split(b"\0") if p]
fields=["absolute_path","relative_path","top_level_source_folder","inferred_owner_source","filename","extension","size_bytes","modification_time","mime_type","media_category","suspected_origin","inferred_date","year","month","day","duplicate_status","hash","path_b64"]
with open(out_file,"w",encoding="utf-8",newline="") as out:
    w=csv.DictWriter(out,delimiter="\t",fieldnames=fields); w.writeheader()
    for path in paths:
        try: st=os.stat(path)
        except Exception: continue
        rel=os.path.relpath(path,root); top=rel.split(os.sep)[0] if os.sep in rel else "."
        filename=os.path.basename(path)
        ext=filename.rsplit(".",1)[1].lower() if "." in filename and not filename.startswith(".") else ""
        mime=mime_type(path); cat=category(ext,mime); org=origin(path,filename)
        inferred=exif_date(path) if cat in {"photo","video"} else ""
        if not inferred: inferred=date_from_filename(filename)
        if not inferred: inferred=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
        y=m=d=""; mm=re.match(r"^([12]\d{3})-([01]\d)-([0-3]\d)$", inferred)
        if mm: y,m,d=mm.group(1),mm.group(2),mm.group(3)
        w.writerow({"absolute_path":clean(path),"relative_path":clean(rel),"top_level_source_folder":clean(top),"inferred_owner_source":clean(owner(top)),"filename":clean(filename),"extension":clean(ext),"size_bytes":st.st_size,"modification_time":datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),"mime_type":clean(mime),"media_category":cat,"suspected_origin":org,"inferred_date":inferred,"year":y,"month":m,"day":d,"duplicate_status":"unknown","hash":"","path_b64":b64_path(path)})
PY
    log "INVENTORY finished: $INVENTORY"
    mark_done inventory
}

stage_hash() {
    require_file "$INVENTORY" "Cannot hash before inventory."
    if should_reuse "$HASHES"; then log "HASHES already exist. Reusing: $HASHES"; return; fi
    log "HASH candidate preparation started"
    python3 - "$INVENTORY" "$HASH_CANDIDATES" <<'PY'
import csv, sys
from collections import defaultdict
inventory,out_file=sys.argv[1:]
by_size=defaultdict(list)
with open(inventory,encoding="utf-8",newline="") as f:
    for row in csv.DictReader(f,delimiter="\t"):
        if row.get("size_bytes") and row.get("path_b64"): by_size[row["size_bytes"]].append(row["path_b64"])
with open(out_file,"w",encoding="utf-8") as out:
    for size, paths in by_size.items():
        if len(paths)>1:
            for p in paths: out.write(p+"\n")
PY
    n="$(wc -l < "$HASH_CANDIDATES" | awk '{print $1}')"; log "Hash candidates: $n"
    if [[ "$n" == "0" ]]; then printf 'hash\tsize_bytes\tpath_b64\tabsolute_path\n' > "$HASHES"; mark_done hash; return; fi
    python3 - "$HASH_CANDIDATES" "$HASHES" "$THREADS" <<'PY'
import sys,os,csv,base64,hashlib
from concurrent.futures import ThreadPoolExecutor,as_completed
candidates_file,hashes_file,threads=sys.argv[1:]; threads=int(threads)
def decode_path(b): return base64.b64decode(b.encode("ascii")).decode("utf-8",errors="surrogateescape")
def sha256_file(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        while True:
            block=f.read(4*1024*1024)
            if not block: break
            h.update(block)
    return h.hexdigest()
def clean(p): return p.replace("\t"," ").replace("\n","\\n").replace("\r","\\r")
def hash_one(b):
    try:
        p=decode_path(b)
        if not os.path.isfile(p): return None
        return {"hash":sha256_file(p),"size_bytes":str(os.path.getsize(p)),"path_b64":b,"absolute_path":clean(p)}
    except Exception as e:
        return {"hash":"","size_bytes":"","path_b64":b,"absolute_path":f"HASH_ERROR: {type(e).__name__}: {e}"}
with open(candidates_file,encoding="utf-8") as f: candidates=[l.strip() for l in f if l.strip()]
fields=["hash","size_bytes","path_b64","absolute_path"]
with open(hashes_file,"w",encoding="utf-8",newline="") as out:
    w=csv.DictWriter(out,delimiter="\t",fieldnames=fields); w.writeheader()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        for fut in as_completed([ex.submit(hash_one,p) for p in candidates]):
            row=fut.result()
            if row: w.writerow(row)
PY
    log "HASH finished: $HASHES"
    log "Hash rows written: $(tail -n +2 "$HASHES" | wc -l | awk '{print $1}')"
    mark_done hash
}

stage_reports() {
    require_file "$INVENTORY" "Cannot generate reports before inventory."
    require_file "$HASHES" "Cannot generate reports before hashes."
    if [[ "$FORCE" == "0" && -s "$DUP_GROUPS" && -s "$INVENTORY_FINAL" ]]; then log "REPORTS already exist. Reusing existing duplicate reports."; return; fi
    log "REPORTS started"
    python3 - "$INVENTORY" "$HASHES" "$INVENTORY_FINAL" "$DUP_GROUPS" "$DUP_READABLE" "$KEEPERS" "$DUPS_TO_QUARANTINE" <<'PY'
import csv, sys, os
from collections import defaultdict
inventory,hashes,inv_final,dup_groups,readable,keepers_out,quarantine_out=sys.argv[1:]
def read_tsv(path):
    with open(path,encoding="utf-8",newline="") as f: return list(csv.DictReader(f,delimiter="\t"))
inv=read_tsv(inventory); hash_rows=read_tsv(hashes); inv_by_b64={r["path_b64"]:r for r in inv}
groups=defaultdict(list)
for h in hash_rows:
    if h.get("hash") and h.get("size_bytes"): groups[(h["hash"],h["size_bytes"])].append(h)
dup_sets={k:v for k,v in groups.items() if len(v)>1}
def keeper_sort_key(row):
    path_original=row["absolute_path"]; path=path_original.lower(); top=row["top_level_source_folder"].lower(); cat=row["media_category"]; org=row["suspected_origin"]
    parts=[p for p in path.split(os.sep) if p]; parts_lc=[p.lower() for p in parts]; score=0
    if any(x in path for x in ["recovered","found.","lost.dir","$recycle.bin","system volume information",".trash","trash"]): score-=1000
    else: score+=100
    if any(x in top for x in ["backup","bckup","to_do_backup","recovered","recover"]): score-=80
    else: score+=80
    whatsapp_dump_terms=["whatsapp images","whatsappimages","whatsappimages0","whatsapp video","whatsappvideo","whatsapp videos","whatsapp","whatsap","media/whatsapp"]
    raw_dump_terms=["dcim/.thumbnails",".thumbnails","thumbnails","cache","tmp","temp","received files"]
    if cat in {"photo","video"}:
        if any(x in path for x in whatsapp_dump_terms): score-=250
        if any(x in path for x in raw_dump_terms): score-=200
    curated_keywords=["chloe","familia","family","vacaciones","viaje","trip","cumple","birthday","boda","wedding","album","seleccion","selected","editadas","edited"]
    if any(x in path for x in curated_keywords): score+=350
    generic_folder_names={"whatsapp","whatsap","media","whatsapp images","whatsappimages","whatsappimages0","whatsapp video","whatsapp videos","dcim","camera","pictures","images","fotos","imagenes","download","downloads","descargas","backup","bckup","to_do_backup_2023_02","android","data","cache","tmp","temp","received files"}
    parent=parts_lc[-2] if len(parts_lc)>=2 else ""; grandparent=parts_lc[-3] if len(parts_lc)>=3 else ""
    if parent and parent not in generic_folder_names: score+=180
    if grandparent and grandparent not in generic_folder_names: score+=80
    if cat in {"photo","video"}:
        if org=="camera": score+=90
        if "dcim" in path or "camera" in path: score+=60
        if "fotos" in path or "imagenes" in path or "pictures" in path: score+=40
    if org=="whatsapp": score-=40
    score+=min(path_original.count(os.sep),25)
    return (-score,row["modification_time"],row["absolute_path"])
duplicate_status={r["path_b64"]:"unique" for r in inv}; hash_by_b64={}
dup_records=[]; keeper_records=[]; quarantine_records=[]; group_id=0
for (h,size), rows in sorted(dup_sets.items(), key=lambda x:(int(x[0][1]),x[0][0])):
    enriched=[]
    for hr in rows:
        r=inv_by_b64.get(hr["path_b64"])
        if r:
            r=dict(r); r["hash"]=h; enriched.append(r)
    if len(enriched)<2: continue
    group_id+=1; enriched.sort(key=keeper_sort_key); keeper=enriched[0]
    for r in enriched:
        status="keeper" if r["path_b64"]==keeper["path_b64"] else "duplicate"
        duplicate_status[r["path_b64"]]=status; hash_by_b64[r["path_b64"]]=h
        dup_records.append({"group_id":group_id,"hash":h,"size_bytes":size,"status":status,"absolute_path":r["absolute_path"],"relative_path":r["relative_path"],"media_category":r["media_category"],"suspected_origin":r["suspected_origin"],"modification_time":r["modification_time"],"path_b64":r["path_b64"]})
        if status=="keeper":
            keeper_records.append({"group_id":group_id,"hash":h,"size_bytes":size,"absolute_path":r["absolute_path"],"relative_path":r["relative_path"],"media_category":r["media_category"],"suspected_origin":r["suspected_origin"],"modification_time":r["modification_time"],"path_b64":r["path_b64"]})
        else:
            quarantine_records.append({"group_id":group_id,"hash":h,"size_bytes":size,"duplicate_absolute_path":r["absolute_path"],"keeper_absolute_path":keeper["absolute_path"],"duplicate_path_b64":r["path_b64"],"keeper_path_b64":keeper["path_b64"]})
for r in inv:
    b=r["path_b64"]; r["duplicate_status"]=duplicate_status.get(b,"unique"); r["hash"]=hash_by_b64.get(b,"")
with open(inv_final,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,delimiter="\t",fieldnames=inv[0].keys()); w.writeheader(); w.writerows(inv)
with open(dup_groups,"w",encoding="utf-8",newline="") as f:
    fields=["group_id","hash","size_bytes","status","absolute_path","relative_path","media_category","suspected_origin","modification_time","path_b64"]; w=csv.DictWriter(f,delimiter="\t",fieldnames=fields); w.writeheader(); w.writerows(dup_records)
with open(keepers_out,"w",encoding="utf-8",newline="") as f:
    fields=["group_id","hash","size_bytes","absolute_path","relative_path","media_category","suspected_origin","modification_time","path_b64"]; w=csv.DictWriter(f,delimiter="\t",fieldnames=fields); w.writeheader(); w.writerows(keeper_records)
with open(quarantine_out,"w",encoding="utf-8",newline="") as f:
    fields=["group_id","hash","size_bytes","duplicate_absolute_path","keeper_absolute_path","duplicate_path_b64","keeper_path_b64"]; w=csv.DictWriter(f,delimiter="\t",fieldnames=fields); w.writeheader(); w.writerows(quarantine_records)
with open(readable,"w",encoding="utf-8") as f:
    last=None
    for r in dup_records:
        if r["group_id"]!=last:
            last=r["group_id"]; f.write("\n"+"="*100+"\n"); f.write(f"GROUP {last} | size={r['size_bytes']} | hash={r['hash']}\n"); f.write("="*100+"\n")
        f.write(f"[{r['status'].upper()}] {r['absolute_path']}\n")
PY
    log "REPORTS finished"; mark_done reports
}

stage_plan() {
    require_file "$INVENTORY_FINAL" "Cannot generate organization plan before duplicate reports."
    if should_reuse "$ORG_PLAN"; then log "PLAN already exists. Reusing: $ORG_PLAN"; return; fi
    log "PLAN started with ORG_STRATEGY=$ORG_STRATEGY"
    python3 - "$INVENTORY_FINAL" "$ORG_PLAN" "$ORGANIZED_DIR" "$ORG_STRATEGY" <<'PY'
import csv, sys, os, re, hashlib
from pathlib import Path
inv_final,plan,organized,strategy=sys.argv[1:]
organized=Path(organized)
bio={"fa":"fasta","fasta":"fasta","fna":"fasta","faa":"fasta","ffn":"fasta","fastq":"fastq","fq":"fastq","sam":"alignments","bam":"alignments","cram":"alignments","vcf":"variants","bcf":"variants","gff":"genomes","gff3":"genomes","gtf":"genomes","bed":"genomes","bedgraph":"genomes","wig":"genomes","bw":"genomes","bigwig":"genomes"}
def safe(s):
    s=re.sub(r"[/\0]+","_",s or "unknown"); s=re.sub(r"\s+"," ",s).strip(); return s[:180] if s else "unknown"
def type_dest(row):
    cat=row["media_category"]; org=row["suspected_origin"]; year=row["year"] or "unknown_year"; ext=row["extension"] or "no_extension"; filename=safe(row["filename"])
    if cat=="photo":
        org=org if org in {"camera","whatsapp","screenshot"} else "unknown"; return organized/"by_type"/"photos"/org/year/filename
    if cat=="video":
        org=org if org in {"camera","whatsapp"} else "unknown"; return organized/"by_type"/"videos"/org/year/filename
    if cat=="document": return organized/"by_type"/"documents"/"by_extension"/safe(ext)/filename
    if cat=="archive": return organized/"by_type"/"archives"/filename
    if cat=="bioinformatics": return organized/"by_type"/"bioinformatics"/bio.get(ext.lower(),"other")/filename
    if cat=="code": return organized/"by_type"/"code"/safe(ext)/filename
    return organized/"by_type"/"other"/safe(ext)/filename
generic={"","." ,"backup","bckup","to_do_backup","to_do_backup_2023_02","dedup_audit","organized_unique","organized_unique_contextual","downloads","download","descargas","media","whatsapp","whatsap","whatsapp images","whatsappimages","whatsappimages0","whatsapp video","whatsapp videos","dcim","camera","pictures","images","imagenes","fotos","android","data","cache","tmp","temp","received files","documents","documentos","desktop","escritorio"}
generic_pats=[re.compile(r"^backup[_ -]?\d{4}",re.I),re.compile(r"^bckup[_ -]?\d{4}",re.I),re.compile(r"^.*backup.*$",re.I),re.compile(r"^whatsapp.*$",re.I),re.compile(r"^\d+$"),re.compile(r"^\d+\.\d+$")]
def is_generic(x):
    n=x.strip().lower()
    if n in generic: return True
    return any(p.match(n) for p in generic_pats)
def is_context(x):
    n=x.strip().lower()
    return bool(n and not is_generic(n) and any(c.isalpha() for c in n))
def context_dest(row):
    rel=row["relative_path"]; filename=safe(row["filename"]); cat=row["media_category"]; year=row["year"] or "unknown_year"
    parts=[p for p in Path(rel).parent.parts if p not in {"","."}]
    meaningful=[p for p in parts if is_context(p)]
    if meaningful:
        context=Path(*[safe(p) for p in meaningful[-4:]])
        if cat in {"photo","video"} and year:
            return organized/"by_context"/context/year/filename, "context_plus_year", str(context)
        return organized/"by_context"/context/filename, "context", str(context)
    return type_dest(row), "fallback_by_type", ""
def unique_dest(dest,row,used):
    s=str(dest)
    if s not in used:
        used.add(s); return dest
    base=dest.with_suffix(""); suffix=dest.suffix
    token=hashlib.sha1((row.get("hash") or row.get("path_b64") or s).encode()).hexdigest()[:12]
    cand=Path(f"{base}__{token}{suffix}")
    if str(cand) not in used:
        used.add(str(cand)); return cand
    i=1
    while True:
        cand=Path(f"{base}__{token}_{i}{suffix}")
        if str(cand) not in used:
            used.add(str(cand)); return cand
        i+=1
with open(inv_final,encoding="utf-8",newline="") as f: rows=list(csv.DictReader(f,delimiter="\t"))
fields=["action","duplicate_status","source_absolute_path","source_path_b64","destination_path","organization_strategy","preserved_context","media_category","suspected_origin","year","size_bytes","hash"]
used=set()
with open(plan,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,delimiter="\t",fieldnames=fields); w.writeheader()
    for row in rows:
        if row["duplicate_status"]=="duplicate": continue
        if strategy=="contextual":
            dest,org_strategy,context=context_dest(row)
        else:
            dest=type_dest(row); org_strategy="type"; context=""
        dest=unique_dest(dest,row,used)
        w.writerow({"action":"hardlink_or_copy","duplicate_status":row["duplicate_status"],"source_absolute_path":row["absolute_path"],"source_path_b64":row["path_b64"],"destination_path":str(dest),"organization_strategy":org_strategy,"preserved_context":context,"media_category":row["media_category"],"suspected_origin":row["suspected_origin"],"year":row["year"] or "unknown_year","size_bytes":row["size_bytes"],"hash":row["hash"]})
PY
    log "PLAN finished: $ORG_PLAN"; mark_done plan
}

stage_summary() {
    require_file "$INVENTORY_FINAL" "Cannot generate summary before reports."; require_file "$ORG_PLAN" "Cannot generate summary before organization plan."
    log "SUMMARY started"
    python3 - "$INVENTORY_FINAL" "$DUP_GROUPS" "$ORG_PLAN" "$SUMMARY" "$ORG_STRATEGY" <<'PY'
import csv,sys
from collections import Counter
inv_final,dup_groups,plan_file,summary,strategy=sys.argv[1:]
def read_tsv(path):
    with open(path,encoding="utf-8",newline="") as f: return list(csv.DictReader(f,delimiter="\t"))
inv=read_tsv(inv_final); plan=read_tsv(plan_file)
def human(n):
    n=float(n)
    for unit in ["B","KB","MB","GB","TB","PB"]:
        if n<1024: return f"{n:.2f} {unit}"
        n/=1024
    return f"{n:.2f} EB"
total=sum(int(r["size_bytes"] or 0) for r in inv); dup=sum(int(r["size_bytes"] or 0) for r in inv if r["duplicate_status"]=="duplicate"); uniq=sum(int(r["size_bytes"] or 0) for r in inv if r["duplicate_status"]!="duplicate")
cat=Counter(r["media_category"] for r in inv); origin=Counter(r["suspected_origin"] for r in inv); status=Counter(r["duplicate_status"] for r in inv); strat=Counter(r.get("organization_strategy","") for r in plan); context=Counter(r.get("preserved_context","") or "NO_CONTEXT" for r in plan)
group_ids=set()
try:
    with open(dup_groups,encoding="utf-8",newline="") as f:
        for r in csv.DictReader(f,delimiter="\t"): group_ids.add(r["group_id"])
except FileNotFoundError: pass
with open(summary,"w",encoding="utf-8") as f:
    f.write("Backup deduplication and organization summary\n"+"="*70+"\n\n")
    f.write(f"Organization strategy: {strategy}\n")
    f.write(f"Total files: {len(inv)}\nTotal size: {human(total)}\nDuplicate groups: {len(group_ids)}\nDuplicate files proposed for quarantine: {sum(1 for r in inv if r['duplicate_status']=='duplicate')}\nDuplicate bytes recoverable after manual review: {human(dup)}\nUnique/keeper files: {sum(1 for r in inv if r['duplicate_status']!='duplicate')}\nUnique/keeper size: {human(uniq)}\nPlanned organized files: {len(plan)}\n\n")
    f.write("Files by category:\n")
    for k,v in cat.most_common(): f.write(f"  {k}: {v}\n")
    f.write("\nFiles by suspected origin:\n")
    for k,v in origin.most_common(): f.write(f"  {k}: {v}\n")
    f.write("\nFiles by duplicate status:\n")
    for k,v in status.most_common(): f.write(f"  {k}: {v}\n")
    f.write("\nPlanned files by organization strategy:\n")
    for k,v in strat.most_common(): f.write(f"  {k}: {v}\n")
    f.write("\nTop preserved contexts:\n")
    for k,v in context.most_common(25): f.write(f"  {k}: {v}\n")
PY
    log "SUMMARY finished: $SUMMARY"; mark_done summary
}

stage_validate() {
    log "VALIDATION started"
    set +e
    python3 - "$ROOT" "$AUDIT_ROOT" "$ROOT/ORGANIZED_UNIQUE" "$ROOT/ORGANIZED_UNIQUE_CONTEXTUAL" "$ALL_FILES_NUL" "$INVENTORY" "$INVENTORY_FINAL" "$DUP_GROUPS" "$ORG_PLAN" "$VALIDATION" <<'PY'
import sys,os,csv,base64
from collections import defaultdict
root,audit_root,org1,org2,scan_nul,inventory,inventory_final,dup_groups,org_plan,validation=sys.argv[1:]
results=[]
def record(name,ok,detail): results.append((name,"PASS" if ok else "FAIL",detail))
def count_nul(path):
    if not os.path.exists(path): return None
    with open(path,"rb") as f: return f.read().count(b"\0")
def read_tsv(path):
    if not os.path.exists(path): return []
    with open(path,encoding="utf-8",newline="") as f: return list(csv.DictReader(f,delimiter="\t"))
scan_count=count_nul(scan_nul); record("scan_file_exists",scan_count is not None,f"scan_count={scan_count}")
inv=read_tsv(inventory); record("inventory_exists",bool(inv),f"inventory_rows={len(inv)}")
if scan_count is not None and inv: record("scan_count_equals_inventory_rows",scan_count==len(inv),f"scan={scan_count}, inventory={len(inv)}")
missing=excluded=bad_b64=0
for r in inv:
    try: p=base64.b64decode(r["path_b64"]).decode("utf-8",errors="surrogateescape")
    except Exception: bad_b64+=1; p=r.get("absolute_path","")
    if not os.path.exists(p): missing+=1
    if p.startswith(audit_root+os.sep) or p.startswith(org1+os.sep) or p.startswith(org2+os.sep): excluded+=1
record("inventory_paths_exist",missing==0,f"missing_paths={missing}")
record("inventory_path_b64_decodable",bad_b64==0,f"bad_b64={bad_b64}")
record("excluded_dirs_absent_from_inventory",excluded==0,f"excluded_hits={excluded}")
inv_final=read_tsv(inventory_final); record("final_inventory_exists",bool(inv_final),f"final_inventory_rows={len(inv_final)}" if inv_final else "missing")
if inv and inv_final: record("inventory_count_equals_final_inventory_count",len(inv)==len(inv_final),f"inventory={len(inv)}, final={len(inv_final)}")
dups=read_tsv(dup_groups)
if dups:
    by_group=defaultdict(list)
    for r in dups: by_group[r["group_id"]].append(r)
    bad=0
    for gid,rows in by_group.items():
        if len([r for r in rows if r["status"]=="keeper"])!=1 or len([r for r in rows if r["status"]=="duplicate"])<1 or len({r["hash"] for r in rows})!=1 or len({r["size_bytes"] for r in rows})!=1: bad+=1
    record("duplicate_group_consistency",bad==0,f"bad_groups={bad}, total_groups={len(by_group)}")
else: record("duplicate_group_consistency",True,"no duplicate groups or report empty")
plan=read_tsv(org_plan)
if plan:
    bad_dup=sum(1 for r in plan if r.get("duplicate_status")=="duplicate"); dests=[r["destination_path"] for r in plan]; dest_dups=len(dests)-len(set(dests))
    required={"action","duplicate_status","source_absolute_path","source_path_b64","destination_path","organization_strategy","preserved_context","media_category","suspected_origin","year","size_bytes","hash"}
    missing_cols=required-set(plan[0].keys())
    record("organization_plan_exists",True,f"planned_files={len(plan)}")
    record("organization_plan_schema_consistent",not missing_cols,f"missing_cols={sorted(missing_cols)}")
    record("organization_plan_excludes_duplicates",bad_dup==0,f"duplicate_entries={bad_dup}")
    record("organization_plan_destination_unique",dest_dups==0,f"destination_collisions={dest_dups}")
else: record("organization_plan_exists",False,"missing or empty")
with open(validation,"w",encoding="utf-8") as f:
    f.write("Validation report\n"+"="*70+"\n\n")
    for name,status,detail in results: f.write(f"{status}\t{name}\t{detail}\n")
    failed=[r for r in results if r[1]=="FAIL"]; f.write(f"\nTotal checks: {len(results)}\nFailed checks: {len(failed)}\n")
if any(r[1]=="FAIL" for r in results): sys.exit(2)
PY
    code=$?; set -e
    if [[ "$code" == "0" ]]; then log "VALIDATION finished: PASS"; else log "VALIDATION finished: FAIL. See: $VALIDATION"; return "$code"; fi
    mark_done validate
}

stage_organize() {
    require_file "$ORG_PLAN" "Cannot organize before organization plan."
    if [[ "$RUN" != "1" ]]; then log "ORGANIZE dry-run. No files created."; log "Review plan: $ORG_PLAN"; return; fi
    log "ORGANIZE started with plan: $ORG_PLAN"
    tail -n +2 "$ORG_PLAN" | while IFS=$'\t' read -r action status src_display src_b64 dest org_strategy preserved_context category origin year size hash; do
        src="$(printf '%s' "$src_b64" | base64 -d)"
        if [[ ! -f "$src" ]]; then printf 'MISSING_SOURCE\t%s\t%s\n' "$src_display" "$dest" >> "$EXEC_LOG"; continue; fi
        if [[ -e "$dest" ]]; then printf 'SKIP_EXISTS\t%s\t%s\n' "$src" "$dest" >> "$EXEC_LOG"; continue; fi
        mkdir -p "$(dirname -- "$dest")"
        if ln -- "$src" "$dest" 2>/dev/null; then printf 'HARDLINK\t%s\t%s\n' "$src" "$dest" >> "$EXEC_LOG"
        elif cp -a --reflink=auto -- "$src" "$dest" 2>/dev/null; then printf 'COPY\t%s\t%s\n' "$src" "$dest" >> "$EXEC_LOG"
        else printf 'FAILED\t%s\t%s\n' "$src" "$dest" >> "$EXEC_LOG"; fi
    done
    log "ORGANIZE finished. Log: $EXEC_LOG"; mark_done organize
}

run_from_stage() {
    local start="$1"; local stages=(scan inventory hash reports plan summary validate organize); local run=0
    for stage in "${stages[@]}"; do
        [[ "$stage" == "$start" ]] && run=1
        if [[ "$run" == "1" ]]; then
            case "$stage" in scan) stage_scan ;; inventory) stage_inventory ;; hash) stage_hash ;; reports) stage_reports ;; plan) stage_plan ;; summary) stage_summary ;; validate) stage_validate ;; organize) stage_organize ;; esac
        fi
    done
}

run_only_stage() {
    case "$1" in preflight) stage_preflight ;; scan) stage_scan ;; inventory) stage_inventory ;; hash) stage_hash ;; reports) stage_reports ;; plan) stage_plan ;; summary) stage_summary ;; validate) stage_validate ;; organize) stage_organize ;; *) die "Unknown --only stage: $1" ;; esac
}

echo "[START] organize_backup_dedup.sh version $VERSION"
preflight_before_mkdir
bootstrap
acquire_lock
log "Workflow initialized"
log "Version=$VERSION"
log "ROOT=$ROOT"
log "WORKDIR=$WORKDIR"
log "ORG_STRATEGY=$ORG_STRATEGY"
log "ORGANIZED_DIR=$ORGANIZED_DIR"
log "ORG_PLAN=$ORG_PLAN"
log "THREADS=$THREADS"
log "RUN=$RUN"
log "EXIF=$EXIF"
log "FORCE=$FORCE"
log "RESUME_FROM=$RESUME_FROM"
log "ONLY_STAGE=${ONLY_STAGE:-none}"
stage_preflight
if [[ -n "$ONLY_STAGE" ]]; then
    [[ "$ONLY_STAGE" == "preflight" ]] || run_only_stage "$ONLY_STAGE"
else
    run_from_stage "$RESUME_FROM"
fi
log "Workflow finished"
log "Reports: $REPORT_DIR"
log "Plan: $ORG_PLAN"
log "Validation: $VALIDATION"
