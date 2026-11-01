"""系统能力与本地模型管理接口；模型的下载、导入和完整性校验委托 models。"""
from fastapi import APIRouter
import shutil
from .. import models
from ..config import DATA
from ..models import import_model
from ..schemas import ModelDownload, ModelImport

router = APIRouter()


@router.get("/api/system")
def system():
    import importlib.metadata
    from ..media import FFMPEG, FFPROBE

    disk = shutil.disk_usage(DATA)
    versions = {}
    for name in ("faster-whisper", "ctranslate2", "torch"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "未安装 / 独立环境"
    return {
        "disk_free_gb": round(disk.free / 1024**3, 2),
        "models": models.inventory(),
        "download": models.state,
        "ffmpeg": bool(shutil.which(FFMPEG)),
        "ffprobe": bool(shutil.which(FFPROBE)),
        "versions": versions,
        "privacy": "音视频与识别结果留在本机；点击润色或翻译后，字幕、相邻上下文及术语发送至所配置 API。",
    }


@router.post("/api/models/download")
def model_download(body: ModelDownload):
    models.download(body.model, body.token)
    return models.state


@router.post("/api/models/import")
def model_import_endpoint(body: ModelImport):
    return import_model(body.model, body.path)
