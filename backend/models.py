# 本地模型注册表与生命周期管理：维护 Whisper 与 pyannote 模型清单、完整性校验、本地导入及下载线程。
import json
import shutil
import subprocess
import threading
from pathlib import Path
from .config import MODELS, PYTHON, ROOT
from .media import FLAGS

REGISTRY = {
    "whisper-large-v3": {
        "repo": "Systran/faster-whisper-large-v3",
        "name": "Whisper large-v3",
        "files": [
            "config.json",
            "model.bin",
            "preprocessor_config.json",
            "tokenizer.json",
            "vocabulary.json",
            "vocabulary.txt",
        ],
        "marker": "model.bin",
        "size_gb": 3.1,
        "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        "gated": False,
    },
    "community-1": {
        "repo": "pyannote/speaker-diarization-community-1",
        "name": "pyannote community-1",
        "files": [
            "config.yaml",
            "segmentation/pytorch_model.bin",
            "embedding/pytorch_model.bin",
            "plda/plda.npz",
            "plda/xvec_transform.npz",
        ],
        "marker": "segmentation/pytorch_model.bin",
        "size_gb": 0.04,
        "revision": "main",
        "gated": True,
    },
}
state = {"status": "idle", "model": "", "error": ""}
lock = threading.Lock()


# 检查模型是否已就绪：
# 读取 tinglan-model.json 清单，校验关键标记文件 (marker) 与全部必须文件是否存在且字节大小完全匹配
def ready(name):
    folder = MODELS / name
    try:
        manifest = json.loads(
            (folder / "tinglan-model.json").read_text(encoding="utf-8")
        )
        files = manifest["files"]
        marker = REGISTRY[name]["marker"]
        if not (folder / marker).is_file() or (
            marker in files and (folder / marker).stat().st_size != files[marker]
        ):
            return False
        if name == "community-1" and not set(REGISTRY[name]["files"]) <= files.keys():
            return False
        return all(
            (folder / file).is_file() and (folder / file).stat().st_size == size
            for file, size in files.items()
        )
    except (OSError, ValueError, KeyError):
        return False


def inventory():
    return [
        {"id": name, **value, "ready": ready(name)} for name, value in REGISTRY.items()
    ]


# 从用户本地路径导入已下载的模型文件，生成清单文件 tinglan-model.json
def import_model(name, source_path):
    if name not in REGISTRY:
        raise ValueError("未知模型")
    src = Path(source_path).resolve()
    if not src.is_dir():
        raise ValueError("导入路径不存在或不是目录")
    required = REGISTRY[name]["files"] if name == "community-1" else None
    if required and not all((src / file).is_file() for file in required):
        snapshots = (
            sorted((src / "snapshots").glob("*"))
            if (src / "snapshots").is_dir()
            else []
        )
        src = next(
            (
                path
                for path in reversed(snapshots)
                if all((path / file).is_file() for file in required)
            ),
            src,
        )
    marker = REGISTRY[name]["marker"]
    if not (src / marker).is_file():
        raise ValueError("导入目录中缺少模型核心文件: " + marker)
    if required:
        missing = [file for file in required if not (src / file).is_file()]
        if missing:
            raise ValueError("导入目录中缺少模型核心文件: " + "、".join(missing))
    folder = MODELS / name
    if folder.exists():
        current = sorted(
            {
                str(p.relative_to(folder))
                for p in folder.rglob("*")
                if p.is_file() and p.name != "tinglan-model.json"
            }
        )
        incoming = sorted(
            {
                str(p.relative_to(src))
                for p in src.rglob("*")
                if p.is_file() and ".cache" not in p.parts
            }
        )
        if current and current != incoming:
            raise ValueError("目标模型目录已有不同版本，请先清理后再导入")
    folder.mkdir(parents=True, exist_ok=True)
    for p in src.rglob("*"):
        if p.is_file() and ".cache" not in p.parts:
            rel = p.relative_to(src)
            dest = folder / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    manifest_files = required or [
        str(p.relative_to(folder))
        for p in folder.rglob("*")
        if p.is_file() and p.name != "tinglan-model.json"
    ]
    manifest = {
        "repo": REGISTRY[name]["repo"],
        "revision": "imported",
        "downloaded_at": 0,
        "files": {file: (folder / file).stat().st_size for file in manifest_files},
    }
    (folder / "tinglan-model.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "model": name}


# 启动后台线程调用 download_models.py 进行模型离线下载
def download(name, token=""):
    if name not in REGISTRY:
        raise ValueError("未知模型")
    if not lock.acquire(False):
        raise ValueError("已有模型正在下载")
    item = REGISTRY[name]
    needed_gb = item["size_gb"] + 1.0
    free_gb = shutil.disk_usage(MODELS).free / 1024**3
    if free_gb < needed_gb and not ready(name):
        lock.release()
        raise ValueError(f"磁盘空间不足，下载与运行该模型需要至少 {needed_gb:.1f}GB 空间，当前可用 {free_gb:.1f}GB")
    state.update(status="running", model=name, error="")

    def run():
        try:
            import os
            env = os.environ.copy()
            if token:
                env["HF_TOKEN"] = token
            cmd = [str(PYTHON), "-u", str(ROOT / "scripts" / "download_models.py"), name]
            process = subprocess.run(
                cmd,
                input="",
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=ROOT,
                env=env,
                creationflags=FLAGS,
            )
            if process.returncode:
                raise ValueError((process.stderr or process.stdout)[-1200:])
            state.update(status="complete")
        except Exception as exc:
            state.update(status="failed", error=str(exc))
        finally:
            lock.release()

    threading.Thread(target=run, daemon=True).start()