# Hugging Face 模型离线下载脚本：
# 检测磁盘可用空间，按预设清单下载必须权重并生成 tinglan-model.json 完整性清单。
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.models import REGISTRY
from backend.config import MODELS

os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "300")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
os.environ["HF_HOME"] = str(ROOT / ".tmp" / "huggingface")


def main():
    import urllib.request

    # 自动继承系统代理设置，确保网络环境可正常连接 Hugging Face
    proxy = urllib.request.getproxies().get("https") or urllib.request.getproxies().get(
        "http"
    )
    if proxy and not os.environ.get("HTTPS_PROXY"):
        os.environ["HTTPS_PROXY"] = proxy
    from huggingface_hub import HfApi, snapshot_download

    token = os.environ.get("HF_TOKEN") or False
    names = sys.argv[1:] or ["whisper-large-v3"]
    for name in names:
        item = REGISTRY[name]
        folder = MODELS / name
        revision = item["revision"]
        needed_bytes = int((item["size_gb"] + 1.0) * 1024**3)
        free_bytes = shutil.disk_usage(MODELS).free
        if free_bytes < needed_bytes and not (folder / item["marker"]).exists():
            raise RuntimeError(
                f"磁盘空间不足，下载与运行需要至少 {item['size_gb'] + 1.0:.1f}GB 空间，当前可用 {free_bytes / 1024**3:.1f}GB"
            )
        print("Downloading " + name + " @ " + revision, flush=True)
        info = HfApi(token=token).model_info(item["repo"], revision=revision)
        patterns = item.get("files")
        if not patterns:
            weight = (
                "*.safetensors"
                if any(s.rfilename.endswith(".safetensors") for s in info.siblings)
                else "*.bin"
            )
            patterns = [
                "*.json",
                weight,
                "*.txt",
                "*.yaml",
                "*.model",
                "*.tiktoken",
                "*.pt",
            ]
        snapshot_download(
            repo_id=item["repo"],
            revision=revision,
            local_dir=folder,
            allow_patterns=patterns,
            ignore_patterns=["*.onnx", "*.msgpack", "*.h5", "*.ot"],
            token=token,
            max_workers=2,
        )
        missing = [file for file in item["files"] if not (folder / file).is_file()]
        if missing:
            raise RuntimeError("模型下载不完整：" + name + "，缺少 " + "、".join(missing))
        manifest = {
            "repo": item["repo"],
            "revision": revision,
            "downloaded_at": time.time(),
            "files": {
                str(p.relative_to(folder)): p.stat().st_size
                for p in folder.rglob("*")
                if p.is_file()
                and ".cache" not in p.parts
                and p.name != "tinglan-model.json"
            },
        }
        # 生成模型清单与文件尺寸记录，供 backend/models.py:ready() 执行快速离线校验
        (folder / "tinglan-model.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Ready: " + name, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never echo a credential or an HTTP header on download errors.
        print(
            type(exc).__name__
            + ": 模型下载失败；请检查网络、剩余空间和 Hugging Face 访问权限及协议授权。",
            file=sys.stderr,
        )
        sys.exit(1)
