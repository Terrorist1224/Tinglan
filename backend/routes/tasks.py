"""任务导入、参数、调度入口与媒体/导出；外部源文件的所有权由 media 层判定。"""
from fastapi import APIRouter
import asyncio
import threading
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from fastapi import File, UploadFile
from fastapi.responses import FileResponse, Response
from .. import db
from ..config import DATA, PYTHON, ROOT
from ..engine import ALL_PHASES, CLOUD_PHASES, engine
from ..export import render
from ..media import FLAGS, MEDIA_SUFFIXES, ensure_space, get_media_path, probe
from ..translation import DEFAULT_POLISH_PROMPT, DEFAULT_TRANSLATE_PROMPT
from ..schemas import PickFile, MediaPath, Params, Action
from .common import get_task
from ..settings import get_api_key, load_settings

router = APIRouter()
pick_dialog_lock = threading.Lock()
_preview_tasks: dict[str, dict] = {}
_preview_lock = threading.Lock()


def _run_preview(task_id: str):
    try:
        task = get_task(task_id)
        from ..media import preview
        preview(task)
        with _preview_lock:
            _preview_tasks[task_id] = {"status": "complete", "error": None}
    except Exception as exc:
        with _preview_lock:
            _preview_tasks[task_id] = {"status": "failed", "error": str(exc)}


@router.get("/api/tasks")
def tasks():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id,name,media,status,phase,progress,error,control,params,created,updated FROM tasks ORDER BY created DESC"
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["params"] = json.loads(item["params"])
            item.update(engine.queue_state(item["id"]))
            result.append(item)
        return result


def default_params(info, defaults=None):
    track = next((r for r in info["audio"] if r["default"]), info["audio"][0])[
        "index"
    ]
    defaults = defaults or {}
    defaults = {k: v for k, v in defaults.items() if v is not None}
    settings = load_settings()
    return {
        "audio_track": track,
        "language": "",
        "device": "cuda",
        "glossary": "",
        "topic": "",
        "target_language": "简体中文",
        "polish_prompt": DEFAULT_POLISH_PROMPT,
        "translate_prompt": DEFAULT_TRANSLATE_PROMPT,
        "context_mode": "batch",
        "diarize": bool(defaults.get("default_diarize", settings.get("default_diarize", True))),
        "auto_polish_translate": bool(defaults.get("default_auto_polish_translate", settings.get("default_auto_polish_translate", False))),
        "num_speakers": None,
        "speakers": {},
    }


def task_with_queue(task):
    return {**task, **engine.queue_state(task["id"])}


def create_task(name, media, info, defaults=None):
    task_id = db.ident()
    folder = DATA / task_id
    folder.mkdir()
    try:
        now = time.time()
        with db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO tasks(id,name,media,probe,params,created,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    task_id,
                    name,
                    media,
                    json.dumps(info, ensure_ascii=False),
                    json.dumps(default_params(info, defaults), ensure_ascii=False),
                    now,
                    now,
                ),
            )
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return get_task(task_id)


@router.post("/api/tasks/pick-file")
def pick_file(body: PickFile | None = None):
    multiple = bool(body and body.multiple)
    if not pick_dialog_lock.acquire(blocking=False):
        raise ValueError("已有正在打开的文件选择窗口，请在打开的窗口中选择")
    try:
        python_bin = PYTHON if PYTHON.exists() else Path(sys.executable)
        cmd = [str(python_bin), "-m", "scripts.pick_dialog"]
        if multiple:
            cmd.append("--multiple")
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
            creationflags=FLAGS,
            cwd=str(ROOT),
        )
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            raise ValueError(f"无法打开系统文件选择窗口，请直接输入本地路径（退出码 {res.returncode}）")
        output = res.stdout.strip()
        if not output:
            return {"path": "", "paths": []}
        data = json.loads(output)
        if "error" in data:
            raise ValueError(f"无法打开系统文件选择窗口，请直接输入本地路径（{data['error']}）")
        return data
    except subprocess.TimeoutExpired:
        raise ValueError("文件选择超时，请重新选择或直接输入本地路径")
    except json.JSONDecodeError:
        raise ValueError("文件选择窗口返回异常，请直接输入本地路径")
    finally:
        pick_dialog_lock.release()


@router.post("/api/tasks/import-path")
async def import_path(body: MediaPath, default_diarize: bool | None = None, default_auto_polish_translate: bool | None = None):
    defaults = {"default_diarize": default_diarize, "default_auto_polish_translate": default_auto_polish_translate}
    raw = body.path.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = raw[1:-1].strip()
    source = Path(raw).expanduser()
    if not source.is_absolute():
        raise ValueError("请输入本地绝对路径")
    source = source.resolve()
    if not source.is_file():
        raise ValueError("文件不存在或不是普通文件")
    if source.suffix.lower() not in MEDIA_SUFFIXES:
        raise ValueError("不支持的文件扩展名")
    info = await asyncio.to_thread(probe, source)
    return create_task(source.name, str(source), info, defaults)


@router.post("/api/tasks")
@router.post("/api/tasks/upload")
async def upload(file: UploadFile = File(...), default_diarize: bool | None = None, default_auto_polish_translate: bool | None = None):
    defaults = {"default_diarize": default_diarize, "default_auto_polish_translate": default_auto_polish_translate}
    task_id = db.ident()
    folder = DATA / task_id
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        raise ValueError("不支持的文件扩展名")
    media = "source" + suffix
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with (folder / media).open("wb") as output:
            while chunk := await file.read(4 * 1024 * 1024):
                ensure_space(len(chunk))
                output.write(chunk)
        info = await asyncio.to_thread(probe, folder / media)
        params = default_params(info, defaults)
        now = time.time()
        with db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO tasks(id,name,media,probe,params,created,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    task_id,
                    file.filename or "未命名",
                    media,
                    json.dumps(info, ensure_ascii=False),
                    json.dumps(params, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return get_task(task_id)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


@router.get("/api/tasks/{task_id}")
def detail(task_id: str):
    item = get_task(task_id)
    folder = DATA / task_id
    has_preview = (folder / "preview.mp4").exists() or (folder / "preview.m4a").exists()
    return {
        **task_with_queue(item),
        "subtitles": db.subtitles(task_id),
        "has_preview": has_preview,
    }


@router.put("/api/tasks/{task_id}/params")
def update_params(task_id: str, body: Params):
    task = get_task(task_id)
    if engine.is_busy(task_id):
        raise ValueError("任务正在运行或排队，请先暂停或退出队列再修改参数")
    if body.num_speakers is not None and body.num_speakers < 1:
        raise ValueError("说话人数必须为正整数，留空表示自动检测")
    if body.device not in ("cpu", "cuda") or body.audio_track not in [
        r["index"] for r in task["probe"]["audio"]
    ]:
        raise ValueError("无效设备或音轨")
    if body.audio_track != task["params"]["audio_track"]:
        if db.subtitles(task_id):
            raise ValueError("已有字幕不可更改音轨，请新建任务")
        for name in ("preview.mp4", "preview.m4a", "audio.wav"):
            (DATA / task_id / name).unlink(missing_ok=True)
    db.update_task(task_id, params={**task["params"], **body.model_dump()})
    return get_task(task_id)


@router.post("/api/tasks/{task_id}/run")
def run(task_id: str, body: Action):
    task = get_task(task_id)
    if body.phase == "preview":
        raise ValueError("请使用 /api/tasks/{task_id}/preview 生成预览")
    if body.phase not in ALL_PHASES:
        raise ValueError("无效处理阶段")
    if task["status"] in ("running", "queued") and not engine.is_busy(task_id):
        raise ValueError("任务状态与调度不一致，请先刷新或重启后端")
    if body.phase in CLOUD_PHASES and not db.subtitles(task_id):
        raise ValueError("请先识别或导入字幕；没有字幕的任务不能提前排队翻译")
    key = get_api_key()
    settings_data = load_settings()
    result = engine.start(task_id, body.phase, settings_data, key, body.ids, body.overwrite, body.resume)
    return {"ok": True, **result, **engine.queue_state(task_id)}


@router.post("/api/tasks/{task_id}/control/{action}")
def control(task_id: str, action: str):
    get_task(task_id)
    engine.control(task_id, action)
    return {"ok": True}


@router.get("/api/tasks/{task_id}/media")
def media(task_id: str):
    item = get_task(task_id)
    folder = DATA / task_id
    if (folder / "preview.mp4").exists():
        target = folder / "preview.mp4"
    elif (folder / "preview.m4a").exists():
        target = folder / "preview.m4a"
    else:
        target = get_media_path(item)
    return FileResponse(target)


@router.post("/api/tasks/{task_id}/preview")
def create_preview(task_id: str):
    get_task(task_id)
    folder = DATA / task_id
    if (folder / "preview.mp4").exists() or (folder / "preview.m4a").exists():
        return {"ready": True, "generating": False, "error": None}
    with _preview_lock:
        state = _preview_tasks.get(task_id)
        if state and state.get("status") == "generating":
            return {"ready": False, "generating": True, "error": None}
        _preview_tasks[task_id] = {"status": "generating", "error": None}
    thread = threading.Thread(target=_run_preview, args=(task_id,), daemon=True)
    thread.start()
    return {"ready": False, "generating": True, "error": None}


@router.get("/api/tasks/{task_id}/preview/status")
def preview_status(task_id: str):
    get_task(task_id)
    folder = DATA / task_id
    ready = (folder / "preview.mp4").exists() or (folder / "preview.m4a").exists()
    with _preview_lock:
        state = _preview_tasks.get(task_id, {})
        generating = state.get("status") == "generating" and not ready
        error = state.get("error") if state.get("status") == "failed" and not ready else None
    return {"ready": ready, "generating": generating, "error": error}


@router.get("/api/tasks/{task_id}/export")
def export(
    task_id: str,
    fmt: str = "srt",
    mode: str = "bilingual",
    allow_incomplete: bool = False,
):
    task = get_task(task_id)
    speakers = task["params"].get("speakers", {})
    text = render(
        db.subtitles(task_id),
        fmt,
        mode,
        speakers=speakers,
        allow_incomplete=allow_incomplete,
    )
    media_type = (
        "text/plain; charset=utf-8"
        if fmt == "txt"
        else "application/json"
        if fmt == "json"
        else "text/vtt"
        if fmt == "vtt"
        else "application/x-subrip"
    )
    return Response(
        text,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="tinglan-{mode}.{fmt}"'},
    )


@router.delete("/api/tasks/{task_id}/cache")
def clear_cache(task_id: str):
    get_task(task_id)
    if engine.active == task_id:
        raise ValueError("请先停止任务")
    with _preview_lock:
        _preview_tasks.pop(task_id, None)
    for name in (
        "chunk.wav",
        "align.wav",
        "review.wav",
        "diarize.wav",
        "preview.mp4",
        "preview.m4a",
        "preview.partial.mp4",
        "preview.partial.m4a",
    ):
        (DATA / task_id / name).unlink(missing_ok=True)
    return {"ok": True}


@router.delete("/api/tasks/{task_id}")
def delete(task_id: str):
    get_task(task_id)
    if engine.active == task_id or engine.is_busy(task_id):
        raise ValueError("请先停止任务")
    with _preview_lock:
        _preview_tasks.pop(task_id, None)
    engine.remove_from_queue(task_id)
    folder = DATA / task_id
    if folder.resolve().parent != DATA:
        raise ValueError("无效任务目录")
    # 只隔离任务缓存目录；数据库删除失败可改名恢复，外部引用的媒体文件不在此目录。
    trash = DATA / ("deleted-" + task_id)
    isolated = False
    try:
        folder.rename(trash)
        isolated = True
    except FileNotFoundError:
        # 缓存目录已被外部清理时，数据库记录仍应可删除
        pass
    try:
        with db.connect(write=True) as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    except BaseException:
        if isolated:
            trash.rename(folder)
        raise
    if isolated:
        shutil.rmtree(trash)
    return {"ok": True}
