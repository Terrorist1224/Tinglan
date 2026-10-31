import json

import pytest

from backend import models


COMMUNITY_FILES = {
    "config.yaml",
    "segmentation/pytorch_model.bin",
    "embedding/pytorch_model.bin",
    "plda/plda.npz",
    "plda/xvec_transform.npz",
}


def write_manifest(folder, files):
    (folder / "tinglan-model.json").write_text(
        json.dumps({"files": {rel: (folder / rel).stat().st_size for rel in files}}),
        encoding="utf-8",
    )


def write_files(folder, files):
    for rel in files:
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())


def test_community_model_requires_all_weights(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "MODELS", tmp_path)
    folder = tmp_path / "community-1"
    folder.mkdir()
    write_files(folder, {"config.yaml"})
    write_manifest(folder, {"config.yaml"})

    assert not models.ready("community-1")

    write_files(folder, COMMUNITY_FILES)
    write_manifest(folder, COMMUNITY_FILES)

    assert models.ready("community-1")


def test_community_import_rejects_incomplete_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "MODELS", tmp_path / "models")
    source = tmp_path / "source"
    source.mkdir()
    write_files(source, {"config.yaml"})

    with pytest.raises(ValueError, match="缺少模型核心文件"):
        models.import_model("community-1", source)


def test_community_import_accepts_hf_cache_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "MODELS", tmp_path / "models")
    source = tmp_path / "source"
    snapshot = source / "snapshots" / "revision"
    snapshot.mkdir(parents=True)
    write_files(snapshot, COMMUNITY_FILES)

    result = models.import_model("community-1", source)

    assert result == {"ok": True, "model": "community-1"}
    assert models.ready("community-1")


def test_existing_whisper_manifest_remains_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "MODELS", tmp_path)
    folder = tmp_path / "whisper-large-v3"
    files = {"config.json", "model.bin"}
    write_files(folder, files)
    write_manifest(folder, files)

    assert models.ready("whisper-large-v3")