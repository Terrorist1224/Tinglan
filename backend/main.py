# FastAPI 应用程序入口与 HTTP 路由定义：
# 提供系统信息、模型下载/导入、任务增删查改、字幕乐观锁编辑、历史回滚与字幕导出接口。
import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, db, models, translation
from .config import DATA, PYTHON, ROOT
from .engine import ALL_PHASES, CLOUD_PHASES, engine
from .export import ExportError, render
from .media import FLAGS, MEDIA_SUFFIXES, ensure_space, get_media_path, probe
from .models import import_model
from .translation import (
    DEFAULT_POLISH_PROMPT,
    DEFAULT_TRANSLATE_PROMPT,
    TranslationError,
    polish_batch,
    translate_batch,
)

session_key = ""
KEY_FILE = DATA / "api.key"




def get_api_key():
    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return session_key or ""


def vault_key():
    return get_api_key()


@asynccontextmanager
async def lifespan(app):
    db.init()
    yield
    engine.shutdown()


app = FastAPI(title="听澜字幕 Tinglan", version=__version__, lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]
)


@app.middleware("http")
async def local_only(request, call_next):
    # 本机安全访问中间件：
    # 1. 来源校验：检查 sec-fetch-site 与 origin，仅允许同源本机页面访问，防御跨站请求伪造 (CSRF)；
    # 2. 请求头约束：所有数据变更请求 (POST/PUT/PATCH/DELETE) 必须附带 X-Tinglan: 1 自定义头；
    # 3. 安全响应头：强制配置 CSP、frame-ancestors 'none'、nosniff，阻止被第三方恶意网站内嵌调用。
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and origin != str(request.base_url).rstrip("/")
    ):
        return JSONResponse({"detail": "仅允许本机页面访问"}, status_code=403)
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and request.headers.get("x-tinglan") != "1"
    ):
        return JSONResponse({"detail": "缺少本机请求标记"}, status_code=403)
    response = await call_next(request)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        }
    )
    return response


@app.exception_handler(ExportError)
async def export_error(request, exc):
    return JSONResponse(
        {"detail": str(exc), "subtitle_id": exc.subtitle_id, "reason": exc.reason},
        status_code=400,
    )


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(KeyError)
async def missing(request, exc):
    return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)


def get_task(task_id):
    if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
        raise KeyError("任务不存在")
    return db.task(task_id)


def load_settings():
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE id=1").fetchone()
    data = json.loads(row["value"]) if row else {}
    return {
        "base_url": data.get("base_url", ""),
        "model": data.get("model", ""),
        "target_language": data.get("target_language", "简体中文"),
        "topic": data.get("topic", ""),
        "glossary": data.get("glossary", ""),
        "default_diarize": data.get("default_diarize", True),
        "default_auto_polish_translate": data.get("default_auto_polish_translate", False),
    }


@app.get("/api/system")
def system():
    import importlib.metadata
    from .media import FFMPEG, FFPROBE

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


class ModelDownload(BaseModel):
    model: str
    token: str = ""


@app.post("/api/models/download")
def model_download(body: ModelDownload):
    models.download(body.model, body.token)
    return models.state


class ModelImport(BaseModel):
    model: str
    path: str


@app.post("/api/models/import")
def model_import_endpoint(body: ModelImport):
    return import_model(body.model, body.path)


class FetchModelsRequest(BaseModel):
    base_url: str = ""
    key: str = ""


@app.post("/api/settings/models")
def fetch_api_models(body: FetchModelsRequest):
    base_url = (body.base_url.strip() or load_settings().get("base_url", "")).rstrip("/")
    key = body.key.strip() or get_api_key()
    if not base_url:
        raise ValueError("请先填写基础地址")
    if not key:
        raise ValueError("请先填写 API 密钥")
    url = urlsplit(base_url)
    if url.scheme not in ("http", "https") or not url.netloc:
        raise ValueError("基础地址必须为有效 HTTP(S) 地址")
    models_url = f"{base_url}/models"
    try:
        import httpx

        with httpx.Client(timeout=15, follow_redirects=True, trust_env=False) as client:
            resp = client.get(models_url, headers={"Authorization": f"Bearer {key}"})
            if resp.status_code in (401, 403):
                raise ValueError("API 密钥无效或无权限获取模型列表")
            if resp.is_error:
                raise ValueError(f"获取模型列表失败: HTTP {resp.status_code}")
            data = resp.json()
    except httpx.TimeoutException:
        raise ValueError("请求模型列表超时，请检查基础地址或网络")
    except httpx.HTTPError as exc:
        raise ValueError(f"连接失败: {exc}")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"解析模型列表失败: {exc}")

    model_ids = []
    if isinstance(data, dict):
        raw_list = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        raw_list = data
    else:
        raw_list = []

    for item in raw_list:
        if isinstance(item, dict) and "id" in item:
            model_ids.append(str(item["id"]))
        elif isinstance(item, str):
            model_ids.append(item)

    sorted_models = sorted(list(set(model_ids)))
    if not sorted_models:
        raise ValueError("获取成功，但未解析到可用模型 ID")
    return {"models": sorted_models}


@app.get("/api/settings")
def settings():
    return {
        **load_settings(),
        "has_key": bool(get_api_key()),
        "session_only": False,
    }


class Settings(BaseModel):
    base_url: str = Field(max_length=2000)
    model: str = Field(max_length=300)
    target_language: str = Field(default="简体中文", min_length=1, max_length=100)
    topic: str = Field(default="", max_length=2000)
    glossary: str = Field(default="", max_length=10000)
    default_diarize: bool = True
    default_auto_polish_translate: bool = False
    key: str = Field(default="", max_length=3000)
    save_key: bool = False
    clear_key: bool = False


@app.put("/api/settings")
def save_settings(body: Settings):
    global session_key
    url = urlsplit(body.base_url)
    if url.username or url.password:
        raise ValueError("基础地址不可包含用户名或密码")
    if (
        url.scheme not in ("http", "https")
        or not url.netloc
        or any(
            url.netloc.startswith(h)
            for h in (
                "10.",
                "192.168.",
                "172.16.",
                "172.17.",
                "172.18.",
                "172.19.",
                "172.2",
                "172.3",
            )
        )
    ):
        raise ValueError("基础地址必须为有效公网或本机 HTTP(S) 地址")
    clean = {
        "base_url": body.base_url.rstrip("/"),
        "model": body.model.strip(),
        "target_language": body.target_language.strip(),
        "topic": body.topic.strip(),
        "glossary": body.glossary.strip(),
        "default_diarize": body.default_diarize,
        "default_auto_polish_translate": body.default_auto_polish_translate,
    }
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO settings(id,value) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
            (json.dumps(clean, ensure_ascii=False),),
        )
    notice = "设置已保存到本地"
    if body.clear_key:
        session_key = ""
        KEY_FILE.unlink(missing_ok=True)
        notice = "密钥已清除"
    elif body.key:
        session_key = body.key.strip()
        KEY_FILE.write_text(session_key, encoding="utf-8")
        notice = "设置与 API 密钥已保存到本地"
    return {**settings(), "notice": notice}



@app.get("/api/tasks")
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



class PickFile(BaseModel):
    multiple: bool = False


pick_dialog_lock = threading.Lock()


@app.post("/api/tasks/pick-file")
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


class MediaPath(BaseModel):
    path: str


@app.post("/api/tasks/import-path")
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



@app.post("/api/tasks")
@app.post("/api/tasks/upload")
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


@app.get("/api/tasks/{task_id}")
def detail(task_id: str):
    item = get_task(task_id)
    folder = DATA / task_id
    has_preview = (folder / "preview.mp4").exists() or (folder / "preview.m4a").exists()
    return {
        **task_with_queue(item),
        "subtitles": db.subtitles(task_id),
        "has_preview": has_preview,
    }


class Params(BaseModel):
    audio_track: int
    subtitle_track: int | None = None
    language: str = Field(default="", max_length=10)
    device: str = "cuda"
    glossary: str = Field(default="", max_length=10000)
    topic: str = Field(default="", max_length=5000)
    target_language: str = Field(default="简体中文", max_length=50)
    polish_prompt: str = Field(default=DEFAULT_POLISH_PROMPT, max_length=5000)
    translate_prompt: str = Field(default=DEFAULT_TRANSLATE_PROMPT, max_length=5000)
    context_mode: str = "batch"
    diarize: bool = True
    auto_polish_translate: bool = False
    num_speakers: int | None = Field(default=None, ge=1, le=20)
    speakers: dict[str, dict[str, str]] = Field(default_factory=dict)


@app.put("/api/tasks/{task_id}/params")
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


class Action(BaseModel):
    phase: str
    ids: list[str] | None = None
    overwrite: bool = False
    resume: bool = False


@app.post("/api/tasks/{task_id}/run")
def run(task_id: str, body: Action):
    task = get_task(task_id)
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


@app.post("/api/tasks/{task_id}/control/{action}")
def control(task_id: str, action: str):
    get_task(task_id)
    engine.control(task_id, action)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/revert-polish")
def revert_polish_endpoint(task_id: str):
    get_task(task_id)
    return db.revert_polish(task_id)


@app.post("/api/tasks/{task_id}/subtitles/{sid}/translate")
def translate_single_subtitle(task_id: str, sid: str):
    task = get_task(task_id)
    if engine.is_busy(task_id):
        raise ValueError("任务正在运行或排队，请完成或退出队列后再逐句处理")
    key = get_api_key()
    if not key:
        raise ValueError("请先在设置中配置 API 密钥")
    settings_data = load_settings()
    if not settings_data.get("base_url") or not settings_data.get("model"):
        raise ValueError("请先设置 API 基础地址和模型")

    all_subs = db.subtitles(task_id)
    target = next((s for s in all_subs if s["id"] == sid), None)
    if not target:
        raise KeyError("字幕不存在")

    merged_settings = {
        **settings_data,
        "target_language": task["params"].get("target_language", "简体中文"),
        "topic": task["params"].get("topic", ""),
        "glossary": task["params"].get("glossary", ""),
        "translate_prompt": task["params"].get("translate_prompt", DEFAULT_TRANSLATE_PROMPT),
    }

    idx = next(i for i, s in enumerate(all_subs) if s["id"] == sid)
    pre = all_subs[max(0, idx - 4) : idx]
    post = all_subs[idx + 1 : min(len(all_subs), idx + 5)]
    context = [{"id": s["id"], "text": s["source"]} for s in pre + post]

    rows = translation.translate_batch(merged_settings, key, [target], context)
    if sid in rows:
        db.apply_translation(task_id, target, rows[sid], "llm")
    return next(s for s in db.subtitles(task_id) if s["id"] == sid)


@app.post("/api/tasks/{task_id}/subtitles/{sid}/polish")
def polish_single_subtitle(task_id: str, sid: str):
    task = get_task(task_id)
    if engine.is_busy(task_id):
        raise ValueError("任务正在运行或排队，请完成或退出队列后再逐句处理")
    key = get_api_key()
    if not key:
        raise ValueError("请先在设置中配置 API 密钥")
    settings_data = load_settings()
    if not settings_data.get("base_url") or not settings_data.get("model"):
        raise ValueError("请先设置 API 基础地址和模型")

    all_subs = db.subtitles(task_id)
    target = next((s for s in all_subs if s["id"] == sid), None)
    if not target:
        raise KeyError("字幕不存在")

    merged_settings = {
        **settings_data,
        "target_language": task["params"].get("target_language", "简体中文"),
        "topic": task["params"].get("topic", ""),
        "glossary": task["params"].get("glossary", ""),
        "polish_prompt": task["params"].get("polish_prompt", DEFAULT_POLISH_PROMPT),
    }

    idx = next(i for i, s in enumerate(all_subs) if s["id"] == sid)
    pre = all_subs[max(0, idx - 4) : idx]
    post = all_subs[idx + 1 : min(len(all_subs), idx + 5)]
    context = [{"id": s["id"], "text": s["source"]} for s in pre + post]


    p_results = translation.polish_batch(merged_settings, key, [target], context)
    polished_text = p_results.get(sid, target["source"])
    applied, change = db.apply_polish(task_id, target, polished_text)
    if applied and change:
        cur_task = db.task(task_id)
        cur_params = cur_task["params"]
        cur_params["last_polish"] = {
            "time": time.time(),
            "changes": [change],
        }
        db.update_task(task_id, params=cur_params)
    return next(s for s in db.subtitles(task_id) if s["id"] == sid)


@app.post("/api/tasks/{task_id}/subtitles/{sid}/polish-and-translate")
def polish_and_translate_single_subtitle(task_id: str, sid: str):
    task = get_task(task_id)
    if engine.is_busy(task_id):
        raise ValueError("任务正在运行或排队，请完成或退出队列后再逐句处理")
    key = get_api_key()
    if not key:
        raise ValueError("请先在设置中配置 API 密钥")
    settings_data = load_settings()
    if not settings_data.get("base_url") or not settings_data.get("model"):
        raise ValueError("请先设置 API 基础地址和模型")

    all_subs = db.subtitles(task_id)
    target = next((s for s in all_subs if s["id"] == sid), None)
    if not target:
        raise KeyError("字幕不存在")

    merged_settings = {
        **settings_data,
        "target_language": task["params"].get("target_language", "简体中文"),
        "topic": task["params"].get("topic", ""),
        "glossary": task["params"].get("glossary", ""),
        "polish_prompt": task["params"].get("polish_prompt", DEFAULT_POLISH_PROMPT),
        "translate_prompt": task["params"].get("translate_prompt", DEFAULT_TRANSLATE_PROMPT),
    }

    idx = next(i for i, s in enumerate(all_subs) if s["id"] == sid)
    pre = all_subs[max(0, idx - 4) : idx]
    post = all_subs[idx + 1 : min(len(all_subs), idx + 5)]
    context = [{"id": s["id"], "text": s["source"]} for s in pre + post]

    polish_skipped = False

    try:
        p_results = translation.polish_batch(merged_settings, key, [target], context)
        polished_text = p_results.get(sid, target["source"])
        db.apply_polish(task_id, target, polished_text)
    except TranslationError as exc:
        if not exc.refusal:
            raise
        polish_skipped = True

    updated_target = next(s for s in db.subtitles(task_id) if s["id"] == sid)
    rows = translation.translate_batch(merged_settings, key, [updated_target], context)
    if sid in rows:
        db.apply_translation(
            task_id, updated_target, rows[sid], "llm"
        )
    if polish_skipped:
        task = db.task(task_id)
        db.update_task(
            task_id,
            error=f"{task.get('error', '')}；润色被模型拒绝，已使用原文翻译".strip("；"),
        )
    return next(s for s in db.subtitles(task_id) if s["id"] == sid)


@app.get("/api/tasks/{task_id}/media")
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


class Edit(BaseModel):
    version: int
    start: int
    end: int
    source: str
    translation: str = ""
    speaker: str = ""
    confirmed: bool = False


# 单条字幕编辑接口（更新原文、译文、时间轴、说话人等）
# 乐观并发控制与版本演进规则：
# 1. 版本核验：比对 body.version 与数据库当前 version，不一致时返回 409 冲突，由前端拉取最新数据；
# 2. 变更前快照：先将当前字幕整行写入 history 表，支持随时查看历史版本或回滚；
# 3. 原文版本推进：若 source 发生变更，source_version 自增 1；
# 4. 译文状态同步：若本次同时提交了非空译文变更，translated_version 同步为最新 source_version 并标记 manual；
#    若仅修改了 source 而未改译文，translated_version 保持原值，使译文自动进入失效（stale）状态；
# 5. 全局版本推进：字幕整行乐观锁 version 递增 1。
@app.patch("/api/tasks/{task_id}/subtitles/{sid}")
def edit(task_id: str, sid: str, body: Edit):
    task = get_task(task_id)
    if (
        body.end <= body.start
        or body.end > round(task["probe"]["duration"] * 1000) + 500
    ):
        raise ValueError("字幕时间超出媒体范围，且结束须大于开始")
    if not body.source.strip():
        raise ValueError("原文不可为空")
    with db.connect(write=True) as conn:
        row = conn.execute(
            "SELECT * FROM subtitles WHERE id=? AND task_id=?", (sid, task_id)
        ).fetchone()
        if not row:
            raise KeyError("字幕不存在")
        if row["version"] != body.version:
            raise HTTPException(
                409, "字幕已被更新，请刷新重试"
            )
        db.snapshot(conn, row)
        source_changed = body.source != row["source"]
        translation_changed = body.translation != row["translation"]
        sv = row["source_version"] + int(source_changed)
        tv = (
            sv
            if translation_changed and body.translation.strip()
            else row["translated_version"]
        )
        provider = (
            "manual"
            if translation_changed and body.translation.strip()
            else ("" if not body.translation.strip() else row["translation_provider"])
        )
        conn.execute(
            "UPDATE subtitles SET start=?,end=?,source=?,translation=?,translation_provider=?,speaker=?,confirmed=?,source_version=?,translated_version=?,manual_translation=?,alignment=?,review=?,version=version+1 WHERE id=?",
            (
                body.start,
                body.end,
                body.source,
                body.translation,
                provider,
                body.speaker,
                int(body.confirmed),
                sv,
                tv,
                1 if translation_changed else row["manual_translation"],
                "manual",
                "",
                row["id"],
            ),
        )
    return [r for r in db.subtitles(task_id) if r["id"] == sid][0]


class Split(BaseModel):
    version: int
    at: int
    position: int


# 字幕拆分接口：按给定时间点与切分文本将原字幕拆为前后两条，原子写入并更新版本
@app.post("/api/tasks/{task_id}/subtitles/{sid}/split")
def split(task_id: str, sid: str, body: Split):
    get_task(task_id)
    with db.connect(write=True) as conn:
        row = conn.execute(
            "SELECT * FROM subtitles WHERE id=? AND task_id=?", (sid, task_id)
        ).fetchone()
        if not row:
            raise KeyError("字幕不存在")
        if row["version"] != body.version:
            raise HTTPException(409, "字幕版本冲突")
        if (
            body.at <= row["start"]
            or body.at >= row["end"]
            or body.position <= 0
            or body.position >= len(row["source"])
        ):
            raise ValueError("拆分点必须在当前字幕内部")
        db.snapshot(conn, row)
        s1 = row["source"][: body.position].strip()
        s2 = row["source"][body.position :].strip()
        conn.execute(
            "UPDATE subtitles SET end=?,source=?,source_version=source_version+1,translated_version=?,translation_provider=?,version=version+1,confirmed=0,review='拆分后请校时与翻译',alignment='manual' WHERE id=?",
            (
                body.at,
                s1,
                row["translated_version"]
                if row["translation"].strip()
                else -1,
                row["translation_provider"] if row["translation"].strip() else "",
                row["id"],
            ),
        )
        conn.execute(
            "INSERT INTO subtitles(id,task_id,start,end,source,original,language,speaker,model,review,alignment,translation_provider) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                db.ident(),
                task_id,
                body.at,
                row["end"],
                s2,
                s2,
                row["language"],
                row["speaker"],
                row["model"],
                "拆分后请校时与翻译",
                "manual",
                row["translation_provider"],
            ),
        )
    return {"ok": True}


class Merge(BaseModel):
    ids: list[str]
    versions: list[int]


# 字幕合并接口：合并多条相邻字幕的时间轴与文本，保留历史并清理被合并行
@app.post("/api/tasks/{task_id}/merge")
def merge(task_id: str, body: Merge):
    get_task(task_id)
    if len(body.ids) < 2 or len(body.ids) != len(body.versions):
        raise ValueError("请选择至少两条字幕进行合并")
    with db.connect(write=True) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM subtitles WHERE task_id=? ORDER BY start,end,id",
                (task_id,),
            )
        ]
        indexes = [i for i, r in enumerate(rows) if r["id"] in body.ids]
        if len(indexes) != len(body.ids) or indexes != list(
            range(min(indexes), max(indexes) + 1)
        ):
            raise ValueError("只能合并相邻字幕")
        chosen = [rows[i] for i in indexes]
        expected = dict(zip(body.ids, body.versions))
        if any(r["version"] != expected[r["id"]] for r in chosen):
            raise HTTPException(409, "字幕版本冲突")
        for r in chosen:
            db.snapshot(conn, r)
        first = chosen[0]
        translated = [r for r in chosen if r["translation"].strip()]
        merged_provider = (
            translated[0]["translation_provider"]
            if translated
            and len({r["translation_provider"] for r in translated}) == 1
            else ""
        )
        conn.execute(
            "UPDATE subtitles SET end=?,source=?,original=?,translation=?,translation_provider=?,manual_translation=?,source_version=source_version+1,version=version+1,confirmed=0,review=?,alignment='sentence' WHERE id=?",
            (
                max(r["end"] for r in chosen),
                " ".join(r["source"] for r in chosen),
                " ".join(r["original"] for r in chosen if r["original"]),
                " ".join(r["translation"] for r in chosen if r["translation"]),
                merged_provider,
                max(r["manual_translation"] for r in chosen),
                "合并后请校时并更新译文",
                first["id"],
            ),
        )
        conn.executemany(
            "DELETE FROM subtitles WHERE id=?", [(r["id"],) for r in chosen[1:]]
        )
    return {"ok": True}


@app.get("/api/tasks/{task_id}/history")
def history(task_id: str):
    get_task(task_id)
    with db.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id,subtitle_id,snapshot,created FROM history WHERE task_id=? ORDER BY id DESC LIMIT 200",
                (task_id,),
            )
        ]


class Restore(BaseModel):
    version: int


@app.post("/api/tasks/{task_id}/history/{hid}/restore")
def restore(task_id: str, hid: int, body: Restore):
    get_task(task_id)
    with db.connect(write=True) as conn:
        item = conn.execute(
            "SELECT * FROM history WHERE id=? AND task_id=?", (hid, task_id)
        ).fetchone()
        if not item:
            raise KeyError("历史记录不存在")
        old = json.loads(item["snapshot"])
        row = conn.execute(
            "SELECT * FROM subtitles WHERE id=? AND task_id=?", (old["id"], task_id)
        ).fetchone()
        if not row:
            raise ValueError("该字幕已合并删除，可在历史中复制原文")
        if row["version"] != body.version:
            raise HTTPException(409, "字幕版本冲突")
        db.snapshot(conn, row)
        conn.execute(
            "UPDATE subtitles SET start=?,end=?,source=?,translation=?,translation_provider=?,speaker=?,source_version=source_version+1,version=version+1,confirmed=0,review=? WHERE id=?",
            (
                old["start"],
                old["end"],
                old["source"],
                old["translation"],
                old.get("translation_provider", ""),
                old["speaker"],
                "历史已恢复，请确认译文并校时",
                row["id"],
            ),
        )
    return {"ok": True}


@app.get("/api/tasks/{task_id}/export")
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


# 清理任务衍生缓存文件（音频切片、预览音视频等）
# 边界约束：仅清理应用内部生成的临时文件，绝不影响用户原始外置媒体文件
@app.delete("/api/tasks/{task_id}/cache")
def clear_cache(task_id: str):
    get_task(task_id)
    if engine.active == task_id:
        raise ValueError("请先停止任务")
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


# 删除任务及关联数据
# 目录改名与数据库事务回滚设计：
# 1. 先将任务目录 DATA / task_id 重命名为 DATA / deleted-task_id 进行隔离；
# 2. 在数据库事务中执行级联删除；
# 3. 若数据库操作异常，触发回滚重命名，将目录还原回原路径，避免“库删失败但目录已丢失”的不一致状态；
# 4. 数据库确认删除成功后，物理删除隔离目录；直接导入的外置源文件位于任务目录外，不受任何影响。
@app.delete("/api/tasks/{task_id}")
def delete(task_id: str):
    get_task(task_id)
    if engine.active == task_id or engine.is_busy(task_id):
        raise ValueError("请先停止任务")
    engine.remove_from_queue(task_id)
    folder = DATA / task_id
    if folder.resolve().parent != DATA:
        raise ValueError("无效任务目录")
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


@app.get("/")
def index():
    path = ROOT / "frontend" / "dist" / "index.html"
    if not path.exists():
        return Response("前端尚未构建，请运行 安装.cmd", status_code=503)
    return FileResponse(path)


assets = ROOT / "frontend" / "dist" / "assets"
if assets.exists():
    app.mount("/assets", StaticFiles(directory=assets), name="assets")



