# 多媒体处理模块：封装 FFmpeg/FFprobe 命令，负责媒体信息探测、音频分块截取、预览流转码以及源文件路径解析。
import json
import shutil
import subprocess
from pathlib import Path
from .config import DATA, FFMPEG, FFPROBE

FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MEDIA_SUFFIXES = {
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
    ".m4v",
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".ogg",
    ".aac",
    ".opus",
    ".ts",
}


def run(command, timeout=120):
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=timeout, creationflags=FLAGS
        )
    except FileNotFoundError as exc:
        raise ValueError("缺少 FFmpeg / FFprobe，请先运行 安装.cmd") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("媒体处理超时，请检查文件") from exc
    if result.returncode:
        raise ValueError(
            "媒体处理失败：" + result.stderr.decode("utf-8", errors="replace")[-1200:]
        )
    return result.stdout


def probe(path):
    value = json.loads(
        run(
            [
                FFPROBE,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    audios = [s for s in value.get("streams", []) if s["codec_type"] == "audio"]
    if not audios:
        raise ValueError("文件中没有音轨")
    duration = float(
        value.get("format", {}).get("duration")
        or max((float(s.get("duration", 0)) for s in audios), default=0)
    )
    if duration <= 0:
        raise ValueError("无法读取有效媒体时长")
    return {
        "duration": duration,
        "audio": [
            {
                "index": s["index"],
                "codec": s.get("codec_name"),
                "language": s.get("tags", {}).get("language", "und"),
                "title": s.get("tags", {}).get("title", ""),
                "default": bool(s.get("disposition", {}).get("default")),
            }
            for s in audios
        ],
        "subtitles": [
            {
                "index": s["index"],
                "codec": s.get("codec_name"),
                "language": s.get("tags", {}).get("language", "und"),
            }
            for s in value["streams"]
            if s["codec_type"] == "subtitle"
        ],
        "video": next(
            (
                {
                    "codec": s.get("codec_name"),
                    "width": s.get("width"),
                    "height": s.get("height"),
                }
                for s in value["streams"]
                if s["codec_type"] == "video"
            ),
            None,
        ),
        "format": value.get("format", {}).get("format_name", ""),
    }


# 校验磁盘剩余可用空间，防止大文件转码耗尽存储空间
def ensure_space(estimated):
    if shutil.disk_usage(DATA).free < estimated + 256 * 1024**2:
        raise ValueError("磁盘空间不足，请先清理缓存或腾出空间")


# 解析媒体源文件的绝对路径
# 边界约束：
# - 若 task['media'] 为绝对路径，说明是通过“极速导入”直读的用户外置文件；
# - 若为相对路径，说明是上传到应用托管目录 DATA / task_id / 下的文件；
# - 外置源文件属于用户资产，听澜仅拥有只读权限，清理任务或缓存时绝对不可删除外置源文件。
def get_media_path(task):
    source = Path(task["media"])
    if not source.is_absolute():
        source = DATA / task["id"] / source
    if not source.is_file():
        raise ValueError(f"源文件未找到：{source}；请恢复原文件，或删除任务后重新导入")
    return source


# 从原始媒体中提取指定时间段的 16kHz 单声道 PCM16 WAV 音频，供 Whisper 识别
def audio(task, output, start=None, duration=None):
    source = get_media_path(task)
    params = task["params"]
    args = [FFMPEG, "-nostdin", "-v", "error", "-y"]
    if start is not None:
        args += ["-ss", str(start)]
    args += ["-i", str(source), "-map", f"0:{params['audio_track']}", "-vn"]
    if duration is not None:
        args += ["-t", str(duration)]
    args += [
        "-af",
        "aresample=async=1:first_pts=0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    ensure_space((duration or task["probe"]["duration"]) * 32000)
    run(args, max(120, task["probe"]["duration"]))


# 生成供网页前端播放的低码率预览视频 (preview.mp4) 或音频 (preview.m4a)
def preview(task):
    folder = DATA / task["id"]
    output = folder / ("preview.mp4" if task["probe"]["video"] else "preview.m4a")
    if output.exists():
        return output
    ensure_space(task["probe"]["duration"] * 400000)
    temp = output.with_stem("preview.partial")
    source = get_media_path(task)
    args = [
        FFMPEG,
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-copyts",
        "-start_at_zero",
        "-i",
        str(source),
        "-map",
        f"0:{task['params']['audio_track']}",
    ]
    if task["probe"]["video"]:
        args += [
            "-map",
            "0:v:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "25",
            "-vf",
            "scale='min(1280,iw)':-2",
        ]
    args += ["-c:a", "aac", "-movflags", "+faststart", str(temp)]
    try:
        run(args, max(300, task["probe"]["duration"] * 3))
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    return output
