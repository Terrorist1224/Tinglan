"""Read-only inventory: count NTFS hard links once, never delete files."""
import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = (".venv-qwen", ".venv-test", ".pytest_cache", "test-results", ".tmp/uv-cache", ".tmp/npm-cache", ".tmp/e2e", ".tmp/e2e-report")


def audit(root):
    root = Path(root).resolve()
    files = {}
    folders = defaultdict(lambda: {"logical_bytes": 0, "files": 0, "exclusive_file_bytes": 0})
    candidates = defaultdict(lambda: {"logical_bytes": 0, "reclaimable_file_bytes": 0})
    skipped = []
    errors = []

    def visit(folder):
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative = path.relative_to(root).as_posix()
                    try:
                        if entry.is_symlink() or path.is_junction():
                            skipped.append(relative)
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            visit(path)
                        elif entry.is_file(follow_symlinks=False):
                            # DirEntry.stat() can report zero inode/link counts on Windows.
                            stat = path.stat(follow_symlinks=False)
                            if not stat.st_ino or not stat.st_nlink:
                                raise OSError("File identity unavailable")
                            top = relative.split("/")[0]
                            candidate = next((p for p in CANDIDATES if relative == p or relative.startswith(p + "/")), None)
                            folders[top]["logical_bytes"] += stat.st_size
                            folders[top]["files"] += 1
                            if candidate:
                                candidates[candidate]["logical_bytes"] += stat.st_size
                            record = files.setdefault((stat.st_dev, stat.st_ino), {
                                "size": stat.st_size, "links": stat.st_nlink,
                                "seen": 0, "folders": set(), "candidates": set(),
                            })
                            record["seen"] += 1
                            record["folders"].add(top)
                            record["candidates"].add(candidate)
                    except OSError as exc:
                        errors.append({"path": relative, "error": str(exc)})
        except OSError as exc:
            errors.append({"path": str(folder.relative_to(root)), "error": str(exc)})

    visit(root)
    combined = 0
    for record in files.values():
        if record["seen"] != record["links"]:
            continue
        if len(record["folders"]) == 1:
            folders[next(iter(record["folders"]))]["exclusive_file_bytes"] += record["size"]
        if None not in record["candidates"]:
            combined += record["size"]
            if len(record["candidates"]) == 1:
                candidates[next(iter(record["candidates"]))]["reclaimable_file_bytes"] += record["size"]
    return {
        "root": str(root),
        "logical_bytes": sum(row["logical_bytes"] for row in folders.values()),
        "unique_file_bytes": sum(row["size"] for row in files.values()),
        "top_level": [{"path": name, **row} for name, row in sorted(folders.items(), key=lambda pair: pair[1]["logical_bytes"], reverse=True)],
        "cleanup_candidates": [{"path": name, **row} for name, row in candidates.items()],
        "cleanup_reclaimable_file_bytes": combined,
        "skipped_links": sorted(skipped), "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = audit(ROOT)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print(f"Logical: {report['logical_bytes'] / 1024**3:.3f} GiB")
        print(f"Unique file content: {report['unique_file_bytes'] / 1024**3:.3f} GiB")
        print("File lengths are not NTFS allocated bytes. No files are deleted.")
        print(f"{'Directory':32} {'Logical GiB':>12} {'Exclusive GiB':>14}")
        for row in report["top_level"]:
            print(f"{row['path']:32} {row['logical_bytes'] / 1024**3:12.3f} {row['exclusive_file_bytes'] / 1024**3:14.3f}")
        print("Review-only cleanup candidates:")
        for row in report["cleanup_candidates"]:
            print(f"{row['path']:32} {row['reclaimable_file_bytes'] / 1024**2:.2f} MiB reclaimable")
        print(f"Combined candidate content: {report['cleanup_reclaimable_file_bytes'] / 1024**2:.2f} MiB")
        print(f"Skipped links: {len(report['skipped_links'])}; errors: {len(report['errors'])}")
        for error in report["errors"]:
            print(error["path"], error["error"])
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
