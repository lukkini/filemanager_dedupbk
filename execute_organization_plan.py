#!/usr/bin/env python3
import argparse, base64, csv, os, shutil
from pathlib import Path

REQ = {"source_path_b64","source_absolute_path","destination_path","duplicate_status"}

def read_plan(p):
    with Path(p).open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if not rows:
        raise SystemExit("ERROR: empty plan")
    missing = REQ - set(rows[0])
    if missing:
        raise SystemExit(f"ERROR: missing columns: {sorted(missing)}")
    return rows

def src_path(r):
    b = r.get("source_path_b64","")
    if b:
        try:
            return base64.b64decode(b.encode()).decode("utf-8", errors="surrogateescape")
        except Exception:
            pass
    return r.get("source_absolute_path","")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--copy-only", action="store_true")
    args = ap.parse_args()

    rows = read_plan(args.plan)
    log = Path(args.log).expanduser().resolve()
    log.parent.mkdir(parents=True, exist_ok=True)

    created = dry = skip = miss = fail = 0
    with log.open("w", encoding="utf-8") as L:
        L.write(f"PLAN\t{Path(args.plan).resolve()}\nEXECUTE\t{args.execute}\nCOPY_ONLY\t{args.copy_only}\n")
        for i,r in enumerate(rows,1):
            src, dest = src_path(r), r.get("destination_path","")
            if r.get("duplicate_status") == "duplicate":
                skip += 1; L.write(f"SKIP_DUPLICATE\t{i}\t{src}\t{dest}\n"); continue
            if not src or not dest:
                fail += 1; L.write(f"FAILED_BAD_ROW\t{i}\t{src}\t{dest}\n"); continue
            if not os.path.isfile(src):
                miss += 1; L.write(f"MISSING_SOURCE\t{i}\t{src}\t{dest}\n"); continue
            if os.path.exists(dest):
                skip += 1; L.write(f"SKIP_EXISTS\t{i}\t{src}\t{dest}\n"); continue
            if not args.execute:
                dry += 1; L.write(f"DRY_RUN\t{i}\t{src}\t{dest}\n"); continue
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            try:
                if not args.copy_only:
                    try:
                        os.link(src, dest)
                        created += 1; L.write(f"HARDLINK\t{i}\t{src}\t{dest}\n"); continue
                    except OSError:
                        pass
                shutil.copy2(src, dest)
                created += 1; L.write(f"COPY\t{i}\t{src}\t{dest}\n")
            except Exception as e:
                fail += 1; L.write(f"FAILED\t{i}\t{src}\t{dest}\t{type(e).__name__}: {e}\n")
        L.write(f"\nSUMMARY\nROWS\t{len(rows)}\nCREATED\t{created}\nDRY_RUN\t{dry}\nSKIPPED\t{skip}\nMISSING\t{miss}\nFAILED\t{fail}\n")
    print(f"Rows: {len(rows)}")
    print(f"Created: {created}")
    print(f"Dry-run: {dry}")
    print(f"Skipped: {skip}")
    print(f"Missing: {miss}")
    print(f"Failed: {fail}")
    print(f"Log: {log}")
    if fail:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
