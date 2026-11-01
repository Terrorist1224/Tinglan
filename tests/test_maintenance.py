import hashlib
import os
import subprocess
import sys
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import setup_tools, make_fixtures
from scripts.space import audit


def test_tool_archive_validates_both_files_before_replacing(tmp_path, monkeypatch):
    archive = tmp_path / "ffmpeg.zip"
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "ffmpeg.exe").write_bytes(b"MZold")
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("release/bin/ffmpeg.exe", b"MZnew")
        out.writestr("release/bin/ffprobe.exe", b"invalid")
    with pytest.raises(ValueError, match="Invalid Windows executable"):
        setup_tools.install_archive(archive, tools)
    assert (tools / "ffmpeg.exe").read_bytes() == b"MZold"
    assert not (tools / "ffprobe.exe").exists()


def test_tool_archive_uses_fixed_names_and_verifies_programs(tmp_path, monkeypatch):
    archive = tmp_path / "ffmpeg.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("release/bin/ffmpeg.exe", b"MZffmpeg")
        out.writestr("release/bin/ffprobe.exe", b"MZffprobe")
        out.writestr("../../escaped.txt", b"must not extract")
    seen = []

    def run(command, **kwargs):
        seen.append(Path(command[0]).name)
        assert command[1:] == ["-version"]
        return SimpleNamespace(returncode=0, stdout="fixture version\n")

    monkeypatch.setattr(setup_tools.subprocess, "run", run)
    tools = tmp_path / "tools"
    setup_tools.install_archive(archive, tools)
    assert seen == ["ffmpeg.exe", "ffprobe.exe"]
    assert (tools / "ffmpeg.exe").read_bytes() == b"MZffmpeg"
    assert (tools / "manifest.json").is_file()
    assert not (tmp_path / "escaped.txt").exists()


def test_tool_promotion_restores_old_tools_on_io_error(tmp_path, monkeypatch):
    archive = tmp_path / "ffmpeg.zip"
    tools = tmp_path / "tools"
    tools.mkdir()
    with zipfile.ZipFile(archive, "w") as out:
        for name in setup_tools.NAMES:
            (tools / name).write_bytes(b"MZold-" + name.encode())
            out.writestr("release/bin/" + name, b"MZnew")
    monkeypatch.setattr(setup_tools.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="version"))
    original = setup_tools.os.replace

    def replace(source, target):
        if Path(source).name == "ffprobe.exe" and Path(source).parent.name != "previous":
            raise PermissionError("locked")
        return original(source, target)

    monkeypatch.setattr(setup_tools.os, "replace", replace)
    with pytest.raises(PermissionError, match="locked"):
        setup_tools.install_archive(archive, tools)
    for name in setup_tools.NAMES:
        assert (tools / name).read_bytes() == b"MZold-" + name.encode()


def test_fixture_audio_is_repeatable_and_does_not_require_a_voice(tmp_path):
    sound, video = make_fixtures.make_fixtures(tmp_path)
    first = hashlib.sha256(sound.read_bytes()).digest()
    make_fixtures.make_fixtures(tmp_path)
    assert hashlib.sha256(sound.read_bytes()).digest() == first
    assert video.stat().st_size > 0
    with wave.open(str(sound)) as source:
        assert source.getnframes() == 17 * 16000
        assert source.getnchannels() == 1


def test_space_audit_counts_shared_external_and_candidate_links(tmp_path):
    root = tmp_path / "project"
    live = root / ".venv"
    old = root / ".venv-qwen"
    tests = root / ".venv-test"
    for folder in (live, old, tests):
        folder.mkdir(parents=True)
    (live / "shared").write_bytes(b"12345")
    os.link(live / "shared", old / "shared")
    (old / "orphan").write_bytes(b"123")
    (old / "external").write_bytes(b"12")
    os.link(old / "external", tmp_path / "outside")
    (old / "both").write_bytes(b"1234")
    os.link(old / "both", tests / "both")
    for name in ("data", "models", "record", ".tmp/whisper-benchmark"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "keep").write_bytes(b"x")
    report = audit(root)
    assert report["logical_bytes"] == 27
    assert report["unique_file_bytes"] == 18
    assert report["cleanup_reclaimable_file_bytes"] == 7
    assert {row["path"] for row in report["cleanup_candidates"]} == {".venv-qwen", ".venv-test"}
    assert report["errors"] == []
    assert (old / "orphan").exists()


def test_space_audit_skips_directory_junctions(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows junction")
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "keep").write_bytes(b"outside")
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(root / "link"), str(outside)], capture_output=True)
    assert result.returncode == 0
    report = audit(root)
    assert report["unique_file_bytes"] == 0
    assert report["skipped_links"] == ["link"]


def test_installer_refuses_running_project_python():
    if sys.platform != "win32":
        pytest.skip("Windows installer")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); & './scripts/install.ps1' -Dev",
    ], cwd=root, capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode != 0
    assert "\u4e3b\u73af\u5883\u6b63\u5728\u4f7f\u7528" in result.stdout + result.stderr


@pytest.mark.parametrize("fail_build", [False, True])
def test_installer_verifies_native_tools_and_preserves_previous_build(tmp_path, fail_build):
    if sys.platform != "win32":
        pytest.skip("Windows installer")
    import shutil

    source = Path(__file__).resolve().parents[1]
    project = tmp_path / "installer project"
    for name in ("scripts", ".venv/Scripts", "tools", "frontend/dist"):
        (project / name).mkdir(parents=True)
    shutil.copy2(source / "scripts/install.ps1", project / "scripts/install.ps1")
    shutil.copy2(source / ".venv/Scripts/python.exe", project / ".venv/Scripts/python.exe")
    shutil.copy2(source / ".venv/pyvenv.cfg", project / ".venv/pyvenv.cfg")
    for name in setup_tools.NAMES:
        os.link(source / "tools" / name, project / "tools" / name)
    (project / "frontend/dist/index.html").write_text("previous build", encoding="utf-8")
    harness = project / "exercise.ps1"
    code = r'''
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
function uv { $global:LASTEXITCODE = 0; if ($args[1] -eq 'freeze') { Write-Output 'fixture==1' } }
function npm.cmd {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'run') {
        $output = [string]$args[-1]
        New-Item -ItemType Directory -Path $output -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $output 'index.html') -Value 'new build' -Encoding utf8
        if (FAIL_BUILD) { $global:LASTEXITCODE = 42 }
    }
}
& "$PSScriptRoot/scripts/install.ps1" -Dev
'''.replace("FAIL_BUILD", "$true" if fail_build else "$false")
    harness.write_text(code, encoding="utf-8-sig")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        cwd=project, capture_output=True, encoding="utf-8", timeout=30,
    )
    current = (project / "frontend/dist/index.html").read_text(encoding="utf-8-sig").strip()
    if fail_build:
        assert result.returncode != 0
        assert current == "previous build"
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert current == "new build"
        previous = list((project / ".tmp/setup").glob("*/frontend-dist-before/index.html"))
        assert len(previous) == 1
        assert previous[0].read_text(encoding="utf-8") == "previous build"
