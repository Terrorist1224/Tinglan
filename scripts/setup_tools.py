"""Stage and verify the two required FFmpeg executables before installation."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

NAMES = ("ffmpeg.exe", "ffprobe.exe")


def install_archive(archive, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        bad = source.testzip()
        if bad:
            raise ValueError("Corrupt FFmpeg archive: " + bad)
        members = {}
        for name in NAMES:
            matches = [item for item in source.namelist() if tuple(Path(item).parts[-2:]) == ("bin", name)]
            if len(matches) != 1:
                raise ValueError("Expected exactly one bin/" + name)
            data = source.read(matches[0])
            if not data.startswith(b"MZ"):
                raise ValueError("Invalid Windows executable: " + name)
            members[name] = data
        # Never extract archive paths; write only the two fixed basenames.
        with tempfile.TemporaryDirectory(prefix="ffmpeg-", dir=destination) as folder:
            staged = Path(folder)
            manifest = {}
            for name, data in members.items():
                target = staged / name
                target.write_bytes(data)
                result = subprocess.run(
                    [str(target), "-version"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode or not result.stdout.strip():
                    raise ValueError("FFmpeg verification failed: " + name)
                manifest[name] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "version": result.stdout.splitlines()[0],
                }
            (staged / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            previous = staged / "previous"
            previous.mkdir()
            promoted = []
            names = (*NAMES, "manifest.json")
            for name in names:
                if (destination / name).exists():
                    shutil.copy2(destination / name, previous / name)
            try:
                for name in names:
                    os.replace(staged / name, destination / name)
                    promoted.append(name)
            except BaseException:
                for name in reversed(promoted):
                    if (previous / name).exists():
                        os.replace(previous / name, destination / name)
                    else:
                        (destination / name).unlink()
                raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    install_archive(args.archive, args.destination)
