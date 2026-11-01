# 全局路径与环境变量配置：
# 强制指定 Python 与模型库离线模式，并将缓存目录重定向至项目内 .tmp/，避免污染用户全局目录。
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("TINGLAN_DATA", ROOT / "data")).resolve()
MODELS = ROOT / "models"
os.environ.update(
    {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "HF_HOME": str(ROOT / ".tmp" / "huggingface"),
        "TORCH_HOME": str(ROOT / ".tmp" / "torch"),
        "TORCHINDUCTOR_CACHE_DIR": str(ROOT / ".tmp" / "inductor"),
        "TRANSFORMERS_OFFLINE": "1",
    }
)
FFMPEG = (
    str(ROOT / "tools" / "ffmpeg.exe")
    if (ROOT / "tools" / "ffmpeg.exe").exists()
    else "ffmpeg"
)
FFPROBE = (
    str(ROOT / "tools" / "ffprobe.exe")
    if (ROOT / "tools" / "ffprobe.exe").exists()
    else "ffprobe"
)
PYTHON = Path(sys.executable).resolve()
