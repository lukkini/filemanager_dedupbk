#!/usr/bin/env python3

from pathlib import Path
import argparse
import hashlib
import subprocess
import time
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

PHOTO_EXT = {
    "jpg", "jpeg", "png", "heic", "heif",
    "tif", "tiff", "webp", "bmp", "gif",
    "raw", "cr2", "nef", "arw", "dng"
}

VIDEO_EXT = {
    "mp4", "mov", "avi", "m4v", "mkv",
    "3gp", "3g2", "wmv", "flv", "webm",
    "mts", "m2ts", "mpeg", "mpg"
}

ALL_EXT = PHOTO_EXT | VIDEO_EXT

seen_hashes = {}
seen_hashes_lock = threading.Lock()

progress_lock = threading.Lock()
active_jobs = {}
stop_reporter = threading.Event()


def sha256_file(path: Path, block_size=1024 * 1024):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(block_size):
            h.update(chunk)
    return h.hexdigest()


def format_seconds(seconds):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def rsync_copy(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rsync",
        "-a",
        "--protect-args",
        str(src),
        str(dest)
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )

    return result.returncode == 0, result.stderr.strip()


def discover_files(src: Path, scan_report_every: int):
    by_size = defaultdict(list)
    scanned = 0
    media = 0
    skipped = 0
    start = time.time()

    print(f"Scanning source: {src}", flush=True)

    for path in src.rglob("*"):
        scanned += 1

        if scanned % scan_report_every == 0:
            elapsed = time.time() - start
            speed = scanned / elapsed if elapsed > 0 else 0
            print(
                f"\rScanning... entries={scanned:,} | "
                f"media={media:,} | "
                f"speed={speed:.1f} entries/sec",
                end="",
                flush=True
            )

        if not path.is_file():
            continue

        ext = path.suffix.lower().lstrip(".")

        if ext not in ALL_EXT:
            skipped += 1
            continue

        try:
            size = path.stat().st_size
        except Exception:
            skipped += 1
            continue

        by_size[size].append(path)
        media += 1

    print()
    elapsed = time.time() - start

    print(
        f"Finished scan | entries={scanned:,} | "
        f"media={media:,} | non_media={skipped:,} | "
        f"time={elapsed:.1f}s",
        flush=True
    )

    return by_size, media, skipped


def build_destination(file: Path, src_root: Path, dest_parent: Path, preserve_subdirs: bool):
    """
    Example source root:
      /run/media/lucas/Elements

    Example file:
      /run/media/lucas/Elements/2025-05-27_imagenes_fotos/celu_Andre_A22_241011/DCIM/Winter 2024/20240911_182337.heic

    Default:
      DEST/Elements/2025-05-27_imagenes_fotos/heic/20240911_182337.heic

    With --preserve-subdirs:
      DEST/Elements/2025-05-27_imagenes_fotos/Winter 2024/heic/20240911_182337.heic
    """

    ext = file.suffix.lower().lstrip(".")
    rel = file.relative_to(src_root)
    parts = rel.parts

    root_name = src_root.name

    if len(parts) >= 2:
        first_dir = parts[0]
    else:
        first_dir = "_ROOT"

    if preserve_subdirs:
        containing_dir = file.parent.name
        dest_dir = dest_parent / root_name / first_dir / containing_dir / ext
    else:
        dest_dir = dest_parent / root_name / first_dir / ext

    return dest_dir / file.name


def process_file(file: Path, src_root: Path, dest_parent: Path, preserve_subdirs: bool, needs_hash: bool):
    thread_id = threading.get_ident()
    ext = file.suffix.lower().lstrip(".")

    try:
        size = file.stat().st_size
    except Exception as e:
        return {
            "status": "ERROR_STAT",
            "extension": ext,
            "size": "",
            "sha256": "",
            "source": str(file),
            "destination": "",
            "duplicate_of": "",
            "error": str(e)
        }

    file_hash = ""

    if needs_hash:
        with progress_lock:
            active_jobs[thread_id] = f"HASHING: {file}"

        try:
            file_hash = sha256_file(file)
        except Exception as e:
            with progress_lock:
                active_jobs.pop(thread_id, None)

            return {
                "status": "ERROR_HASH",
                "extension": ext,
                "size": size,
                "sha256": "",
                "source": str(file),
                "destination": "",
                "duplicate_of": "",
                "error": str(e)
            }

        with seen_hashes_lock:
            if file_hash in seen_hashes:
                with progress_lock:
                    active_jobs.pop(thread_id, None)

                return {
                    "status": "DUPLICATE_SKIPPED",
                    "extension": ext,
                    "size": size,
                    "sha256": file_hash,
                    "source": str(file),
                    "destination": "",
                    "duplicate_of": seen_hashes[file_hash],
                    "error": ""
                }

            seen_hashes[file_hash] = str(file)

    dest = build_destination(file, src_root, dest_parent, preserve_subdirs)

    with progress_lock:
        active_jobs[thread_id] = f"COPYING: {file} -> {dest}"

    ok, err = rsync_copy(file, dest)

    with progress_lock:
        active_jobs.pop(thread_id, None)

    return {
        "status": "COPIED_HASHED" if needs_hash and ok else "COPIED_UNIQUE_SIZE" if ok else "ERROR_COPY",
        "extension": ext,
        "size": size,
        "sha256": file_hash,
        "source": str(file),
        "destination": str(dest) if ok else "",
        "duplicate_of": "",
        "error": "" if ok else err
    }


def reporter(total_media, counters, start_time, progress_every):
    while not stop_reporter.wait(progress_every):
        with progress_lock:
            done = counters["copied"] + counters["duplicates"] + counters["errors"]
            elapsed = time.time() - start_time
            speed = done / elapsed if elapsed > 0 else 0
            remaining = total_media - done
            eta = remaining / speed if speed > 0 else 0

            active_preview = ""
            if active_jobs:
                active_preview = " | " + next(iter(active_jobs.values()))

            print(
                f"\rProcessed={done:,}/{total_media:,} | "
                f"Copied={counters['copied']:,} | "
                f"Duplicates={counters['duplicates']:,} | "
                f"Errors={counters['errors']:,} | "
                f"Speed={speed:.1f} files/sec | "
                f"Elapsed={format_seconds(elapsed)} | "
                f"ETA={format_seconds(eta)}"
                f"{active_preview[:180]}",
                end="",
                flush=True
            )


def write_manifest(path: Path, results):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as out:
        out.write(
            "status\textension\tsize\tsha256\t"
            "source\tdestination\tduplicate_of\terror\n"
        )

        for r in results:
            out.write(
                f"{r['status']}\t"
                f"{r['extension']}\t"
                f"{r['size']}\t"
                f"{r['sha256']}\t"
                f"{r['source']}\t"
                f"{r['destination']}\t"
                f"{r['duplicate_of']}\t"
                f"{r['error']}\n"
            )


def main():
    parser = argparse.ArgumentParser(
        description="Collect media files by extension using rsync, size-first SHA256 dedup, and multicore execution."
    )

    parser.add_argument("--src", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--scan-report-every", type=int, default=10000)
    parser.add_argument("--progress-every", type=float, default=5.0)

    parser.add_argument(
        "--preserve-subdirs",
        action="store_true",
        help="Add the immediate containing directory of each file before the extension directory."
    )

    args = parser.parse_args()

    src = Path(args.src.rstrip("/")).expanduser().resolve()
    dest_parent = Path(args.dest).expanduser().resolve()

    if not src.exists() or not src.is_dir():
        raise SystemExit(f"ERROR: source does not exist or is not a directory: {src}")

    root_out = dest_parent / src.name
    manifest = root_out / "media_collection_manifest.tsv"

    by_size, total_media, skipped_non_media = discover_files(
        src=src,
        scan_report_every=args.scan_report_every
    )

    jobs_input = []
    unique_size_files = 0
    hashed_files = 0

    for _, files in by_size.items():
        if len(files) == 1:
            jobs_input.append((files[0], False))
            unique_size_files += 1
        else:
            for f in files:
                jobs_input.append((f, True))
                hashed_files += 1

    layout = (
        f"{dest_parent}/{src.name}/<first_dir>/<containing_dir>/<extension>/<file>"
        if args.preserve_subdirs
        else f"{dest_parent}/{src.name}/<first_dir>/<extension>/<file>"
    )

    print()
    print("========== PLAN ==========")
    print(f"Source:              {src}")
    print(f"Destination parent:  {dest_parent}")
    print(f"Output layout:       {layout}")
    print(f"Manifest:            {manifest}")
    print(f"Threads:             {args.threads}")
    print(f"Media files:         {total_media:,}")
    print(f"Copied without hash: {unique_size_files:,}")
    print(f"Files needing hash:  {hashed_files:,}")
    print("==========================")
    print()

    results = []
    counters = {
        "copied": 0,
        "duplicates": 0,
        "errors": 0
    }

    start = time.time()

    rep = threading.Thread(
        target=reporter,
        args=(total_media, counters, start, args.progress_every),
        daemon=True
    )
    rep.start()

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = [
            executor.submit(
                process_file,
                file,
                src,
                dest_parent,
                args.preserve_subdirs,
                needs_hash
            )
            for file, needs_hash in jobs_input
        ]

        for future in as_completed(futures):
            r = future.result()
            results.append(r)

            with progress_lock:
                if r["status"].startswith("COPIED"):
                    counters["copied"] += 1
                elif r["status"] == "DUPLICATE_SKIPPED":
                    counters["duplicates"] += 1
                else:
                    counters["errors"] += 1

    stop_reporter.set()
    rep.join(timeout=1)

    print()
    print("Writing manifest...", flush=True)
    write_manifest(manifest, results)

    ext_counter = defaultdict(int)
    status_counter = defaultdict(int)

    for r in results:
        ext_counter[r["extension"]] += 1
        status_counter[r["status"]] += 1

    elapsed = time.time() - start

    print()
    print("========== SUMMARY ==========")
    print(f"Source:              {src}")
    print(f"Destination parent:  {dest_parent}")
    print(f"Manifest:            {manifest}")
    print(f"Elapsed:             {format_seconds(elapsed)}")
    print(f"Media files:         {total_media:,}")
    print(f"Copied:              {counters['copied']:,}")
    print(f"Duplicates skipped:  {counters['duplicates']:,}")
    print(f"Errors:              {counters['errors']:,}")
    print(f"Non-media skipped:   {skipped_non_media:,}")

    print()
    print("Status counts:")
    for status in sorted(status_counter):
        print(f"  {status}: {status_counter[status]:,}")

    print()
    print("Per-extension counts:")
    for ext in sorted(ext_counter):
        print(f"  {ext}: {ext_counter[ext]:,}")

    print("=============================")


if __name__ == "__main__":
    main()
