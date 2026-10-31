# 任务调度与后台执行引擎：管理本地模型与云端 LLM 两条独立 FIFO 通道。
# 本地通道一次执行一个转录/分人/预览任务；云端通道一次执行一个润色/翻译任务；
# 云端任务内部继续使用自适应批次并发。队列只存在于进程内，重启后全部中断。
import json
import math
import subprocess
import threading
import time
from collections import deque
from . import db
from .config import DATA, MODELS, PYTHON, ROOT
from .media import FLAGS
from .models import ready as model_ready
from .export import timestamp
from .translation import (
    DEFAULT_POLISH_PROMPT,
    DEFAULT_TRANSLATE_PROMPT,
    TranslationError,
    create_batches,
    polish_batch,
    translate_batch,
)


AUTO_CONCURRENCY_CEILING = 16
LOCAL_PHASES = {"transcribe", "diarize", "preview", "embedded"}
CLOUD_PHASES = {"polish", "translate", "polish_translate"}
ALL_PHASES = LOCAL_PHASES | CLOUD_PHASES


def recommended_concurrency(batches):
    return (
        min(batches, 3)
        if batches <= 3
        else 4
        if batches <= 10
        else 6
        if batches <= 30
        else 8
        if batches <= 60
        else 12
        if batches <= 120
        else AUTO_CONCURRENCY_CEILING
    )


def phase_channel(phase):
    if phase in LOCAL_PHASES:
        return "local"
    if phase in CLOUD_PHASES:
        return "cloud"
    raise ValueError("无效处理阶段")


class Channel:
    def __init__(self, name):
        self.name = name
        self.active = None
        self.thread = None
        self.process = None
        self.queue = deque()
        self.sequence = 0


class _BusyLock:
    def __init__(self, engine):
        self.engine = engine

    def locked(self):
        return not self.engine.wait_idle(timeout=0)

    def acquire(self, blocking=False):
        if self.locked():
            return False
        return True

    def release(self):
        return None


class Engine:
    def __init__(self):
        self.schedule_lock = threading.RLock()
        self.channels = {"local": Channel("local"), "cloud": Channel("cloud")}
        self._pending_auto_cloud = set()
        self._task_order = []
        self.stopping = False

    def _active_or_queued_ids(self):
        busy = set()
        for channel in self.channels.values():
            if channel.active:
                busy.add(channel.active)
            for item in channel.queue:
                busy.add(item["task_id"])
        busy.update(self._pending_auto_cloud)
        return busy

    def remove_from_queue(self, task_id):
        with self.schedule_lock:
            if task_id in self._task_order:
                self._task_order.remove(task_id)

    # 兼容旧测试与旧调用方：读取时返回任一通道活动任务，不要作为调度真相。
    @property
    def active(self):
        with self.schedule_lock:
            return self.channels["local"].active or self.channels["cloud"].active

    @active.setter
    def active(self, value):
        with self.schedule_lock:
            self.channels["local"].active = value

    @property
    def process(self):
        with self.schedule_lock:
            return self.channels["local"].process

    @process.setter
    def process(self, value):
        with self.schedule_lock:
            self.channels["local"].process = value

    @property
    def lock(self):
        return _BusyLock(self)


    def is_busy(self, task_id):
        with self.schedule_lock:
            for channel in self.channels.values():
                if channel.active == task_id:
                    return True
                if any(item["task_id"] == task_id for item in channel.queue):
                    return True
        return False

    def queue_state(self, task_id):
        with self.schedule_lock:
            busy = self._active_or_queued_ids()
            self._task_order = [tid for tid in self._task_order if tid in busy]
            rank = (self._task_order.index(task_id) + 1) if task_id in self._task_order else None
            for channel in self.channels.values():
                if channel.active == task_id:
                    return {
                        "queue_channel": channel.name,
                        "queue_position": 0,
                        "queue_ahead": 0,
                        "queue_sequence": 0,
                        "queue_rank": rank,
                    }
                for index, item in enumerate(channel.queue, start=1):
                    if item["task_id"] == task_id:
                        return {
                            "queue_channel": channel.name,
                            "queue_position": index,
                            "queue_ahead": index - 1,
                            "queue_sequence": item["sequence"],
                            "queue_rank": rank,
                        }
            if task_id in self._pending_auto_cloud:
                return {
                    "queue_channel": "cloud",
                    "queue_position": len(self.channels["cloud"].queue) + 1,
                    "queue_ahead": len(self.channels["cloud"].queue),
                    "queue_sequence": 0,
                    "queue_rank": rank,
                }
        return {"queue_channel": "", "queue_position": None, "queue_ahead": None, "queue_sequence": None, "queue_rank": rank}

    def wait_idle(self, channel=None, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        names = [channel] if channel else ["local", "cloud"]
        while True:
            with self.schedule_lock:
                busy = any(self.channels[name].active or self.channels[name].queue for name in names)
            if not busy:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def shutdown(self):
        self.stopping = True
        with self.schedule_lock:
            self._task_order.clear()
            local = self.channels["local"]
            queued = []
            for channel in self.channels.values():
                queued.extend(channel.queue)
                channel.queue.clear()
            process = local.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        for item in queued:
            db.update_task(item["task_id"], status="interrupted", error="程序已停止，可恢复", control="")
        self.wait_idle(timeout=15)
        with self.schedule_lock:
            active = [channel.active for channel in self.channels.values() if channel.active]
        for task_id in active:
            db.update_task(task_id, status="interrupted", error="程序已停止，可恢复", control="")

    def start(self, task_id, phase, settings=None, key="", ids=None, overwrite=False, resume=False, defaults=None, auto_cloud=False):
        if phase not in ALL_PHASES:
            raise ValueError("无效处理阶段")
        db.task(task_id)
        channel_name = phase_channel(phase)
        with self.schedule_lock:
            self.stopping = False
            for name, channel in self.channels.items():
                if channel.active == task_id:
                    return self._result(task_id, name, "started")
                for item in channel.queue:
                    if item["task_id"] == task_id:
                        return self._result(task_id, name, "queued", channel.queue.index(item) + 1)
                if name != channel_name and (channel.active == task_id or any(item["task_id"] == task_id for item in channel.queue)):
                    raise ValueError("该任务已在另一通道处理或排队")

            if task_id not in self._task_order:
                self._task_order.append(task_id)
            item = {"task_id": task_id, "phase": phase, "settings": settings, "key": key, "ids": ids, "overwrite": overwrite, "resume": resume, "defaults": defaults or {}, "auto_cloud": auto_cloud}
            channel = self.channels[channel_name]
            if channel.active or channel.queue:
                channel.sequence += 1
                item["sequence"] = channel.sequence
                channel.queue.append(item)
                db.update_task(task_id, status="queued", phase=phase, error="", control="", progress=0)
                return self._result(task_id, channel_name, "queued", len(channel.queue))
            return self._launch_locked(channel, item)

    def _queue_auto_cloud(self, task_id, settings, key):
        with self.schedule_lock:
            if task_id not in self._task_order:
                self._task_order.append(task_id)
            channel = self.channels["cloud"]
            if channel.active == task_id or any(item["task_id"] == task_id for item in channel.queue):
                return
            task = db.task(task_id)
            item = {"task_id": task_id, "phase": "polish_translate", "settings": settings, "key": key, "ids": None, "overwrite": False, "resume": False, "defaults": {**task["params"], "default_auto_polish_translate": True}, "auto_cloud": True}
            if channel.active or channel.queue:
                channel.sequence += 1
                item["sequence"] = channel.sequence
                channel.queue.append(item)
                db.update_task(task_id, status="queued", phase="polish_translate", error="", control="", progress=0)
            else:
                self._launch_locked(channel, item)

    def _launch_locked(self, channel, item):
        item.setdefault("sequence", 0)
        channel.active = item["task_id"]
        db.update_task(item["task_id"], status="running", phase=item["phase"], error="", control="", progress=0)
        channel.thread = threading.Thread(target=self._run_channel, args=(channel, item), daemon=True)
        channel.thread.start()
        return self._result(item["task_id"], channel.name, "started")

    def _result(self, task_id, channel, status, position=0):
        return {"status": status, "task_id": task_id, "channel": channel, "position": position, "ahead": max(0, position - 1) if position else 0}

    def control(self, task_id, action):
        if action not in ("pause", "cancel"):
            raise ValueError("无效控制操作")
        with self.schedule_lock:
            if task_id in self._task_order:
                self._task_order.remove(task_id)
            for channel in self.channels.values():
                for item in list(channel.queue):
                    if item["task_id"] == task_id:
                        channel.queue.remove(item)
                        db.update_task(task_id, status="paused" if action == "pause" else "cancelled", control="", error="已退出队列" if action == "pause" else "已取消排队")
                        return {"status": "queued_removed", "channel": channel.name}
                if channel.active == task_id:
                    db.update_task(task_id, control=action)
                    (DATA / task_id / "stop").touch()
                    return {"status": "stopping", "channel": channel.name}
        raise ValueError("任务当前未在处理或排队")

    def _run_channel(self, channel, item):
        task_id = item["task_id"]
        try:
            self.run(task_id, item["phase"], item["settings"], item["key"], item["ids"], item["overwrite"], item["resume"], item["defaults"], item["auto_cloud"])
        except Exception as exc:
            db.update_task(task_id, status="failed", error=str(exc)[:600], control="")
            with self.schedule_lock:
                if task_id in self._task_order:
                    self._task_order.remove(task_id)
        finally:
            (DATA / task_id / "stop").unlink(missing_ok=True)
            with self.schedule_lock:
                channel.active = None
                channel.thread = None
                if not self.stopping and channel.queue and channel.active is None:
                    self._launch_locked(channel, channel.queue.popleft())
                pending_auto = list(self._pending_auto_cloud) if channel.name == "local" else []
                if pending_auto:
                    self._pending_auto_cloud.clear()
            for pending_task in pending_auto:
                self._auto_cloud(pending_task)

    def run(self, task_id, phase, settings, key, ids, overwrite, resume=False, defaults=None, auto_cloud=False):
        task = db.task(task_id)
        settings = {**(settings or {}), **{k: v for k, v in (defaults or {}).items() if v is not None}}
        if phase in CLOUD_PHASES:
            if not settings.get("base_url") or not settings.get("model") or not key:
                db.update_task(task_id, status="failed", error="请先设置 API 地址、模型和密钥", control="")
                raise ValueError("请先设置 API 地址、模型和密钥")
            if not db.subtitles(task_id):
                db.update_task(task_id, status="failed", error="请先识别或导入字幕", control="")
                raise ValueError("请先识别或导入字幕")
        elif phase == "diarize" and not model_ready("community-1"):
            db.update_task(task_id, status="failed", error="缺少分人模型 community-1，请先在设置中下载或导入", control="")
            raise ValueError("缺少分人模型 community-1，请先在设置中下载或导入")


        previous = task["params"].get("operation", {})
        def _restore_snapshot(snap, curr_settings):
            s = dict(snap)
            for legacy in ("concurrency", "azure_fallback_on_refusal", "azure_region", "google_fallback_on_refusal"):
                s.pop(legacy, None)
            return {**(curr_settings or {}), **s}

        if resume and previous.get("phase") == phase and phase == "translate":
            pending = [r["id"] for r in db.subtitles(task_id) if r["id"] not in previous.get("restricted_ids", []) and ("target_ids" not in previous or r["id"] in previous["target_ids"]) and not r["manual_translation"] and (not r["translation"] or r["translated_version"] != r["source_version"])]
            operation = {**previous, "ids": pending, "completed_ids": [sid for sid in previous.get("completed_ids", []) if sid not in pending], "overwrite": False}
            ids, overwrite = pending, False
            if previous.get("config_snapshot"):
                settings = _restore_snapshot(previous["config_snapshot"], settings)
        elif resume and previous.get("phase") == phase:
            operation = previous
            ids, overwrite = previous.get("ids"), previous.get("overwrite", False)
            if previous.get("config_snapshot"):
                settings = _restore_snapshot(previous["config_snapshot"], settings)
        else:
            operation = {"phase": phase, "ids": ids, "overwrite": overwrite}
            if phase in CLOUD_PHASES:
                operation["config_snapshot"] = {
                    "base_url": settings.get("base_url", ""),
                    "model": settings.get("model", ""),
                    "target_language": task["params"].get("target_language", "简体中文"),
                    "topic": task["params"].get("topic", ""),
                    "glossary": task["params"].get("glossary", ""),
                    "context_mode": task["params"].get("context_mode", "batch"),
                    "translate_prompt": task["params"].get("translate_prompt", DEFAULT_TRANSLATE_PROMPT),
                    "polish_prompt": task["params"].get("polish_prompt", DEFAULT_POLISH_PROMPT),
                }
            if phase == "translate":
                requested_ids = ids
                selected = [r for r in db.subtitles(task_id) if (requested_ids is None or r["id"] in requested_ids) and (overwrite or (not r["manual_translation"] and (not r["translation"] or r["translated_version"] != r["source_version"])))]
                operation["ids"] = [r["id"] for r in selected]
                operation["target_ids"] = [r["id"] for r in db.subtitles(task_id) if requested_ids is None or r["id"] in requested_ids]
                ids = operation["ids"]
            elif phase in ("polish", "polish_translate"):
                selected = [r for r in db.subtitles(task_id) if ids is None or r["id"] in ids]
                operation["ids"] = [r["id"] for r in selected]
                ids = operation["ids"]
        if phase in CLOUD_PHASES:
            operation.setdefault("target_ids", [r["id"] for r in db.subtitles(task_id) if ids is None or r["id"] in ids])
            operation["restricted_ids"] = [sid for sid in operation.get("restricted_ids", []) if sid in operation["target_ids"]]
            operation["completed_ids"] = [sid for sid in operation.get("completed_ids", []) if sid in operation["target_ids"]]


        current = db.task(task_id)
        db.update_task(task_id, params={**current["params"], "operation": operation})
        try:
            if phase in CLOUD_PHASES:
                self.process_llm(task, phase, settings, key, ids, overwrite)
            elif phase == "preview":
                from .media import preview
                db.update_task(task_id, progress=5)
                preview(task)
                db.update_task(task_id, progress=100)
            elif phase == "embedded":
                db.update_task(task_id, progress=5)
                self.embedded(task)
                db.update_task(task_id, progress=100)
            elif phase == "transcribe":
                if not resume or overwrite:
                    with db.connect(write=True) as conn:
                        conn.execute("DELETE FROM checkpoints WHERE task_id=?", (task_id,))
                        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task_id,))
                        conn.execute("DELETE FROM history WHERE task_id=?", (task_id,))
                    (DATA / task_id / "chunk.wav").unlink(missing_ok=True)
                    task = db.task(task_id)
                    params = dict(task.get("params") or {})
                    params["speakers"] = {}
                    db.update_task(task_id, params=params, progress=0, control="", error="")
                    task = db.task(task_id)
                self.worker(task, "transcribe", ids)
                task = db.task(task_id)
                if task["params"].get("diarize", True):
                    if model_ready("community-1"):
                        db.update_task(task_id, phase="diarize", progress=0)
                        self.worker(task, "diarize", None)
                    else:
                        db.update_task(task_id, error="未检测到分人模型 community-1，已跳过自动分人；可在设置中下载或导入后手动分人")
            elif phase == "diarize":
                self.worker(task, "diarize", ids)

            current = db.task(task_id)
            if (DATA / task_id / "stop").exists():
                action = current["control"]
                db.update_task(task_id, status="paused" if action == "pause" else "cancelled", control="", error="任务已暂停" if action == "pause" else "任务已取消")
                with self.schedule_lock:
                    if task_id in self._task_order:
                        self._task_order.remove(task_id)
            else:
                db.update_task(task_id, status="complete", progress=100, control="")
                final_task = db.task(task_id)
                if phase == "transcribe" and final_task["params"].get("auto_polish_translate"):
                    self._schedule_auto_cloud(task_id)
                else:
                    with self.schedule_lock:
                        if task_id in self._task_order:
                            self._task_order.remove(task_id)
        except Exception as exc:
            raise


    def _schedule_auto_cloud(self, task_id):
        with self.schedule_lock:
            self._pending_auto_cloud.add(task_id)

    def _auto_cloud(self, task_id):
        task = db.task(task_id)
        if not task["params"].get("auto_polish_translate"):
            with self.schedule_lock:
                if task_id in self._task_order:
                    self._task_order.remove(task_id)
            return
        if not db.subtitles(task_id):
            db.update_task(task_id, error="转录完成，但没有字幕，已跳过自动润色翻译")
            with self.schedule_lock:
                if task_id in self._task_order:
                    self._task_order.remove(task_id)
            return
        settings, key = self.resolve_settings()
        if not settings.get("base_url") or not settings.get("model") or not key:
            db.update_task(task_id, error="转录完成；API 配置缺失，已跳过自动润色翻译，请补齐后手动执行")
            with self.schedule_lock:
                if task_id in self._task_order:
                    self._task_order.remove(task_id)
            return
        try:
            self._queue_auto_cloud(task_id, settings, key)
        except Exception as exc:
            db.update_task(task_id, error="转录完成，但自动润色翻译入队失败: " + str(exc)[:400])
            with self.schedule_lock:
                if task_id in self._task_order:
                    self._task_order.remove(task_id)


    def resolve_settings(self):
        from .main import get_api_key, load_settings
        return load_settings(), get_api_key()

    def worker(self, task, phase, ids):
        channel = self.channels["local"]
        with db.connect() as conn:
            completed = [r[0] for r in conn.execute("SELECT unit FROM checkpoints WHERE task_id=? AND phase=?", (task["id"], phase)).fetchall()]
        if phase == "transcribe":
            total_units = max(1, math.ceil(task["probe"]["duration"] / 180))
            self.set_progress_detail(task["id"], len(completed), total_units, "片段", progress=len(completed) / total_units * 100)
        req_path = DATA / task["id"] / "worker-input.json"
        log_path = DATA / task["id"] / f"{phase}.log"
        req_path.write_text(json.dumps({"task": task, "phase": phase, "completed": completed, "ids": ids}, ensure_ascii=False), encoding="utf-8")
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen([str(PYTHON), "-X", "faulthandler", "-u", "-m", "backend.worker", str(req_path)], stdout=subprocess.PIPE, stderr=log_file, cwd=ROOT, text=True, encoding="utf-8", errors="replace", creationflags=FLAGS)
            channel.process = process
            try:
                for line in process.stdout:
                    if line.startswith("TINGLAN:"):
                        self.consume(task["id"], phase, json.loads(line[8:]))
                code = process.wait()
                if code:
                    log_file.flush()
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                    raise ValueError(f"{phase} 子进程异常退出，退出码 {code} (0x{code & 0xFFFFFFFF:08X})；日志：{log_path}\n" + (text[-1600:] or "日志为空"))
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)
                raise
            finally:
                channel.process = None

    # LLM 批量处理流水线（负责 AI 润色、一键翻译或润色并翻译）
    # 架构流程：
    # 1. 待处理行筛选：区分全量目标、已完成（completed_ids）、审查受限（restricted_ids）与剩余待处理；
    # 2. 批次划分：调用 create_batches 按 16 条/2400 字符分块，并关联前后相邻上下文；
    # 3. 动态并发派发：ThreadPoolExecutor 派发请求，严格维护 active_requests <= concurrency；
    # 4. 拒译细分与退避：捕获 refusal 异常并对大批次二分拆解，单个受限句计入 restricted_ids 留待人工处理；
    # 5. 乐观锁写回：调用 db.apply_polish / db.apply_translation，比对 version 防止覆盖用户编辑。
    def process_llm(self, task, phase, settings, key, ids, overwrite):
        from collections import deque
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

        task = db.task(task["id"])
        all_subs = db.subtitles(task["id"])
        operation = task["params"].get("operation", {})
        target_ids = set(operation.get("target_ids", [s["id"] for s in all_subs] if ids is None else ids))
        restricted_ids = set(operation.get("restricted_ids", [])) & target_ids
        completed_ids = set(operation.get("completed_ids", [])) & target_ids
        stale_ids = set()
        if phase == "translate" and operation.get("completed_ids"):
            stale_ids = {r["id"] for r in all_subs if r["id"] in target_ids and not r["manual_translation"] and r["translation"] and r["translated_version"] != r["source_version"]}
            completed_ids -= stale_ids
        selected_ids = ((set(ids) if ids is not None else target_ids) | stale_ids) - restricted_ids - completed_ids
        if phase == "translate" and operation.get("target_ids"):
            completed_ids |= target_ids - selected_ids - restricted_ids
        polished_ids = set(operation.get("polished_ids", []))
        resuming_polish = bool(polished_ids)
        mode = task["params"].get("context_mode", "batch")
        batches = create_batches(all_subs, selected_ids, mode)
        concurrency = recommended_concurrency(len(batches)) or 1
        target_concurrency = concurrency
        pending = deque()
        for batch, context in batches:
            stage = "polish" if phase in ("polish", "polish_translate") else "translate"
            if phase == "polish_translate":
                ready = [s for s in batch if s["id"] in polished_ids]
                batch = [s for s in batch if s["id"] not in polished_ids]
                if ready:
                    pending.append(dict(batch=ready, context=context, stage="translate", retries=0))
            if batch:
                pending.append(dict(batch=batch, context=context, stage=stage, retries=0))

        remaining_ids = target_ids - completed_ids - restricted_ids
        translated_ids = set(completed_ids) if phase != "polish" else set()
        polish_changes = []
        polish_skips = []
        conflicts = 0
        batch_errors = []
        fatal_error = ""
        fatal = threading.Event()
        state_lock = threading.RLock()
        active_requests = 0
        stable_successes = 0
        backoff_until = 0.0
        stop_path = DATA / task["id"] / "stop"
        active_futures = {}

        def is_stopping():
            return stop_path.exists() or self.stopping

        def is_cancelled():
            return stop_path.exists() and db.task(task["id"]).get("control") == "cancel"

        def update_progress():
            with state_lock:
                detail = {
                    "completed": len(completed_ids | restricted_ids),
                    "translated": len(translated_ids),
                    "restricted": len(restricted_ids),
                    "total": len(target_ids),
                    "remaining": len(remaining_ids),
                    "active_requests": active_requests,
                    "concurrency": concurrency,
                    "concurrency_ceiling": AUTO_CONCURRENCY_CEILING,
                    "backoff_seconds": math.ceil(max(0, backoff_until - time.monotonic())),
                    "unit": "句",
                }
                cur_params = db.task(task["id"])["params"]
                cur_params["progress_detail"] = detail
                if "operation" in cur_params:
                    cur_params["operation"].update(
                        ids=sorted(remaining_ids),
                        completed_ids=sorted(completed_ids),
                        restricted_ids=sorted(restricted_ids),
                        polished_ids=sorted(polished_ids),
                    )
                db.update_task(
                    task["id"], params=cur_params,
                    progress=100 * detail["completed"] / max(1, detail["total"]),
                )

        def request_activity(delta):
            nonlocal active_requests
            if delta > 0:
                while True:
                    before_request()
                    with state_lock:
                        if active_requests < concurrency:
                            active_requests += 1
                            update_progress()
                            return
                    time.sleep(0.05)
            else:
                with state_lock:
                    active_requests -= 1
                    update_progress()

        # 发送请求前的退避检查与取消拦截
        def before_request():
            while backoff_until > time.monotonic() and not is_stopping() and not fatal.is_set():
                time.sleep(min(0.1, max(0, backoff_until - time.monotonic())))
            if fatal.is_set():
                raise PermissionError(fatal_error)
            if is_stopping():
                raise InterruptedError("任务已停止派发请求")

        merged_settings = {
            **{k: v for k, v in settings.items() if k != "concurrency"},
            "target_language": task["params"].get("target_language", "简体中文"),
            "topic": task["params"].get("topic", ""),
            "glossary": task["params"].get("glossary", ""),
            "translate_prompt": task["params"].get("translate_prompt", DEFAULT_TRANSLATE_PROMPT),
            "polish_prompt": task["params"].get("polish_prompt", DEFAULT_POLISH_PROMPT),
            "_request_activity": request_activity,
            "_before_request": before_request,
            "_scheduler_retries": True,
        }

        def request(info):
            nonlocal fatal_error
            try:
                before_request()
                fn = polish_batch if info["stage"] == "polish" else translate_batch
                return fn(merged_settings, key, info["batch"], info["context"])
            except PermissionError as exc:
                fatal_error = str(exc)
                fatal.set()
                raise

        update_progress()
        with ThreadPoolExecutor(max_workers=AUTO_CONCURRENCY_CEILING) as executor:
            while pending or active_futures:
                if is_stopping() or fatal.is_set():
                    pending.clear()
                if is_cancelled() or fatal.is_set():
                    for future in active_futures:
                        future.cancel()

                now = time.monotonic()
                if backoff_until and now >= backoff_until:
                    backoff_until = 0.0
                    update_progress()
                while pending and len(active_futures) < concurrency and not is_stopping() and not fatal.is_set() and not backoff_until:
                    info = pending.popleft()
                    active_futures[executor.submit(request, info)] = info
                if not active_futures:
                    if pending and backoff_until:
                        update_progress()
                        time.sleep(min(0.1, max(0, backoff_until - time.monotonic())))
                        continue
                    break

                done, _ = wait(active_futures, timeout=0.1, return_when=FIRST_COMPLETED)
                if not done:
                    if backoff_until:
                        update_progress()
                    continue
                for future in done:
                    info = active_futures.pop(future)
                    if future.cancelled() or is_cancelled():
                        continue
                    batch, context, stage = info["batch"], info["context"], info["stage"]
                    batch_ids = {s["id"] for s in batch}
                    try:
                        result = future.result()
                    except PermissionError:
                        continue
                    except Exception as exc:
                        if isinstance(exc, InterruptedError) and is_stopping():
                            continue
                        # 模型内容审查拦截（refusal）处理：
                        # - 润色阶段被拒：跳过润色，直接保留原文字幕转入翻译阶段；
                        # - 翻译阶段大批次被拒：按原顺序二分拆成两个子批次重试，排查定位敏感句，挽救无辜字幕；
                        # - 单句重试仍被拒：记录到 restricted_ids 并从剩余队列移除，任务正常进行并在最后提示人工校对。
                        if isinstance(exc, TranslationError) and exc.refusal:
                            if stage == "polish" and phase == "polish_translate":
                                polish_skips.append(str(exc))
                                polished_ids.update(batch_ids)
                                pending.appendleft(dict(batch=batch, context=context, stage="translate", retries=0))
                            elif stage == "translate":
                                if len(batch) > 1:
                                    half = len(batch) // 2
                                    for child in (batch[half:], batch[:half]):
                                        pending.appendleft(dict(batch=child, context=context, stage=stage, retries=0))
                                elif not info.get("refusal_retry"):
                                    pending.appendleft({**info, "refusal_retry": True, "retries": 0})
                                else:
                                    restricted_ids.update(batch_ids)
                                    remaining_ids.difference_update(batch_ids)
                            else:
                                batch_errors.append("润色失败：" + str(exc))
                        # 编号错误处理：多条批次二分拆解，单条重试 1 次后作为未完成项报错
                        elif isinstance(exc, TranslationError) and getattr(exc, "id_mismatch", False):
                            stable_successes = 0
                            if len(batch) > 1:
                                half = len(batch) // 2
                                for child in (batch[half:], batch[:half]):
                                    pending.appendleft(dict(batch=child, context=context, stage=stage, retries=0))
                            elif not info.get("id_retry"):
                                pending.appendleft({**info, "id_retry": True, "retries": 0})
                            else:
                                stage_label = "润色" if stage == "polish" else "翻译"
                                seg = batch[0]
                                time_range = f"{timestamp(seg.get('start', 0), vtt=True)}—{timestamp(seg.get('end', 0), vtt=True)}"
                                batch_errors.append(f"{stage_label}失败｜{time_range}｜{exc}")
                        # 触发 HTTP 429 限流或 5xx 服务端错误：
                        # 429 降并发 2，5xx 降并发 1；重置稳定成功计数，计算指数退避时间并进入全局等待
                        elif isinstance(exc, TranslationError) and exc.status_code in (429, 500, 502, 503, 504):
                            if exc.status_code == 429:
                                concurrency = max(1, concurrency - 2)
                            elif exc.status_code in (502, 503, 504):
                                concurrency = max(1, concurrency - 1)
                            stable_successes = 0
                            delay = max(exc.retry_after, 2 ** info["retries"])
                            backoff_until = max(backoff_until, time.monotonic() + delay)
                            if info["retries"] < 2:
                                pending.append({**info, "retries": info["retries"] + 1})
                            else:
                                batch_errors.append(str(exc))
                        else:
                            stable_successes = 0
                            batch_errors.append(str(exc))
                        update_progress()
                        continue

                    # 请求成功：将结果通过乐观锁写回数据库，版本冲突（期间被人工编辑）时放弃写回并记录警告
                    if stage == "polish":
                        for seg in batch:
                            if seg["id"] not in result:
                                batch_errors.append(f'{seg["id"]}：未获得有效润色结果')
                                continue
                            applied, change = db.apply_polish(task["id"], seg, result[seg["id"]])
                            if applied and change:
                                polish_changes.append(change)
                                seg.update(source=result[seg["id"]], version=seg["version"] + 1, source_version=seg["source_version"] + 1)
                            else:
                                conflicts += 1
                        polished_ids.update(batch_ids)
                        if phase == "polish_translate":
                            pending.appendleft(dict(batch=batch, context=context, stage="translate", retries=0))
                        else:
                            completed_ids.update(batch_ids)
                            remaining_ids.difference_update(batch_ids)
                    else:
                        for seg in batch:
                            sid = seg["id"]
                            if not result or sid not in result:
                                batch_errors.append(f"{sid}：未获得有效译文")
                            elif db.apply_translation(task["id"], seg, result[sid], "llm"):
                                translated_ids.add(sid)
                                completed_ids.add(sid)
                                remaining_ids.discard(sid)
                            else:
                                conflicts += 1
                    if not backoff_until:
                        stable_successes += 1
                        if stable_successes >= 3:
                            concurrency = min(target_concurrency, concurrency + 1)
                            stable_successes = 0
                    update_progress()

        backoff_until = 0.0
        update_progress()
        if polish_changes:
            params = db.task(task["id"])["params"]
            previous_changes = params.get("last_polish", {}).get("changes", []) if resuming_polish else []
            params["last_polish"] = {"time": time.time(), "changes": previous_changes + polish_changes}
            db.update_task(task["id"], params=params)
        if fatal_error:
            raise PermissionError(fatal_error)
        if batch_errors and not is_stopping():
            raise TranslationError(f"部分批次处理失败（共 {len(batch_errors)} 处错误，已保留未完成项）：{batch_errors[0]}")
        warnings = []
        if restricted_ids:
            warnings.append(f"有 {len(restricted_ids)} 句被上游内容策略拒绝，需要人工翻译")
        if polish_skips:
            warnings.append(f"有 {len(polish_skips)} 个批次润色被模型拒绝，已使用原文继续翻译")
        if conflicts:
            warnings.append(f"有 {conflicts} 条字幕在请求期间被人工修改，已跳过覆盖")
        if warnings and not is_stopping():
            db.update_task(task["id"], error="；".join(warnings))

    # 调用本地模型运行器（Whisper 转录或 pyannote 说话人分离）
    # 架构设计：将计算开销大且依赖 C++/CUDA 原生库的模型放到独立子进程 (backend.worker) 中运行，
    # 避免潜在的 PyTorch 底层崩溃（如 0xc0000005 访问违规）导致 FastAPI 主服务退出；
    # 子进程通过 stdout 按行输出 JSON 事件 (TINGLAN:...)，主进程通过 consume 解析并落库。
    def worker(self, task, phase, ids):
        with db.connect() as conn:
            completed = [
                r[0]
                for r in conn.execute(
                    "SELECT unit FROM checkpoints WHERE task_id=? AND phase=?",
                    (task["id"], phase),
                ).fetchall()
            ]

        if phase == "transcribe":
            total_units = max(1, math.ceil(task["probe"]["duration"] / 180))
            self.set_progress_detail(
                task["id"],
                len(completed),
                total_units,
                "片段",
                progress=(len(completed) / total_units) * 100,
            )
        req_path = DATA / task["id"] / "worker-input.json"
        log_path = DATA / task["id"] / f"{phase}.log"
        req_path.write_text(
            json.dumps(
                {"task": task, "phase": phase, "completed": completed, "ids": ids},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                [str(PYTHON), "-X", "faulthandler", "-u", "-m", "backend.worker", str(req_path)],
                stdout=subprocess.PIPE,
                stderr=log_file,
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=FLAGS,
            )
            self.process = process
            try:
                for line in process.stdout:
                    if line.startswith("TINGLAN:"):
                        self.consume(task["id"], phase, json.loads(line[8:]))
                code = process.wait()
                if code:
                    log_file.flush()
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                    raise ValueError(
                        f"{phase} 子进程异常退出，退出码 {code} (0x{code & 0xFFFFFFFF:08X})；"
                        f"日志：{log_path}\n" + (text[-1600:] or "日志为空")
                    )
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)
                raise
            finally:
                self.process = None

    def set_progress_detail(self, task_id, completed, total, unit, progress=None):
        task = db.task(task_id)
        params = task["params"]
        params["progress_detail"] = {
            "completed": completed,
            "total": total,
            "remaining": max(0, total - completed),
            "unit": unit,
        }
        fields = {"params": params}
        if progress is not None:
            fields["progress"] = progress
        db.update_task(task_id, **fields)

    # 消费模型子进程输出的协议事件
    # unit 事件落库时写入 checkpoints 表；若发生中断，下次启动可直接跳过已完成 unit
    def consume(self, task_id, phase, event):
        kind = event["type"]
        if kind == "unit":
            with db.connect(write=True) as conn:
                if conn.execute(
                    "SELECT 1 FROM checkpoints WHERE task_id=? AND phase=? AND unit=?",
                    (task_id, phase, event["unit"]),
                ).fetchone():
                    return
                previous = conn.execute(
                    "SELECT source,end FROM subtitles WHERE task_id=? ORDER BY end DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                for r in event["rows"]:
                    if (
                        previous
                        and r["source"].strip() == previous["source"].strip()
                        and abs(r["start"] - previous["end"]) < 1800
                    ):
                        r["review"] = "分块边界可能重复"
                    words_json = json.dumps(r.get("words", []), ensure_ascii=False)
                    conn.execute(
                        "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model,review,words) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            db.ident(),
                            task_id,
                            r["start"],
                            r["end"],
                            r["source"],
                            r["source"],
                            r["language"],
                            "Whisper large-v3 / faster-whisper",
                            r.get("review", ""),
                            words_json,
                        ),
                    )
                conn.execute(
                    "INSERT INTO checkpoints VALUES(?,?,?)",
                    (task_id, phase, event["unit"]),
                )
        elif kind == "diarization":
            self.apply_diarization(task_id, event["segments"])
        elif kind == "notice":
            db.update_task(task_id, error=event["text"])
        elif kind == "metrics":
            target = DATA / task_id / "metrics.jsonl"
            with target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        if "progress" in event:
            fields = {"progress": event["progress"]}
            if phase == "transcribe":
                task = db.task(task_id)
                with db.connect() as conn:
                    completed = conn.execute(
                        "SELECT COUNT(*) FROM checkpoints WHERE task_id=? AND phase=?",
                        (task_id, phase),
                    ).fetchone()[0]
                total = max(1, math.ceil(task["probe"]["duration"] / 180))
                params = task["params"]
                params["progress_detail"] = {
                    "completed": completed,
                    "total": total,
                    "remaining": max(0, total - completed),
                    "unit": "片段",
                }
                fields["params"] = params
            db.update_task(task_id, **fields)

    # 将说话人识别结果应用到字幕表：
    # - 若存在词级时间戳 (words)：计算每个词的时间中点匹配说话人，在说话人切换处精确拆分字幕；
    # - 若无词级时间戳：根据字幕时间区间与各说话人发声区间的重合时长比例判定归属；重叠严重时打上待校对标记。
    def apply_diarization(self, task_id, segments):
        if not segments:
            return
        task = db.task(task_id)
        params = task["params"]
        speakers = params.get("speakers") or {}
        speaker_map = {}
        for seg in segments:
            raw = seg["speaker"]
            if raw not in speaker_map:
                spk_id = str(len(speaker_map) + 1)
                speaker_map[raw] = spk_id
                if spk_id not in speakers:
                    speakers[spk_id] = {"name": f"说话人 {spk_id}", "note": ""}
        params["speakers"] = speakers
        db.update_task(task_id, params=params)

        subs = db.subtitles(task_id)
        with db.connect(write=True) as conn:
            for sub in subs:
                words = sub.get("words") or []
                dur = max(1, sub["end"] - sub["start"])
                if len(words) >= 2:
                    for w in words:
                        c = (w["start"] + w["end"]) / 2
                        m = [s for s in segments if s["start"] <= c <= s["end"]]
                        w["speaker"] = m[0]["speaker"] if m else min(
                            segments,
                            key=lambda s: min(abs(s["start"] - c), abs(s["end"] - c)),
                        )["speaker"]

                    runs = []
                    cur_run = [words[0]]
                    for w in words[1:]:
                        if w["speaker"] == cur_run[-1]["speaker"]:
                            cur_run.append(w)
                        else:
                            runs.append(cur_run)
                            cur_run = [w]
                    if cur_run:
                        runs.append(cur_run)

                    if len(runs) == 1:
                        spk_id = speaker_map[runs[0][0]["speaker"]]
                        conn.execute(
                            "UPDATE subtitles SET speaker=? WHERE id=?",
                            (spk_id, sub["id"]),
                        )
                    else:
                        # Split across speaker boundaries
                        conn.execute("DELETE FROM subtitles WHERE id=?", (sub["id"],))
                        for i, run in enumerate(runs):
                            spk_id = speaker_map[run[0]["speaker"]]
                            run_text = "".join(w["word"] for w in run).strip()
                            conn.execute(
                                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model,review,words,speaker) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    db.ident(),
                                    task_id,
                                    run[0]["start"],
                                    run[-1]["end"],
                                    run_text,
                                    run_text,
                                    sub["language"],
                                    sub["model"],
                                    sub.get("review", ""),
                                    json.dumps(run, ensure_ascii=False),
                                    spk_id,
                                ),
                            )
                else:
                    overlaps = {spk: 0 for spk in speaker_map}
                    for seg in segments:
                        ov = max(0, min(sub["end"], seg["end"]) - max(sub["start"], seg["start"]))
                        overlaps[seg["speaker"]] += ov
                    best_spk, best_ov = max(overlaps.items(), key=lambda x: x[1])
                    sorted_ovs = sorted(overlaps.values(), reverse=True)
                    second_ov = sorted_ovs[1] if len(sorted_ovs) > 1 else 0
                    review = sub.get("review", "")
                    if best_ov > 0:
                        spk_id = speaker_map[best_spk]
                        if second_ov > 0.25 * dur:
                            review = (review + "；" if review else "") + "待校对：存在跨说话人或重叠"
                        conn.execute(
                            "UPDATE subtitles SET speaker=?,review=? WHERE id=?",
                            (spk_id, review, sub["id"]),
                        )
                    else:
                        review = (review + "；" if review else "") + "待校对：未匹配到说话人"
                        conn.execute(
                            "UPDATE subtitles SET review=? WHERE id=?",
                            (review, sub["id"]),
                        )

    # 提取视频内嵌字幕轨，直接封装为 SRT 格式写入字幕库
    def embedded(self, task):
        import srt
        from .media import FFMPEG, get_media_path, run

        track = task["params"].get("subtitle_track")
        options = [s for s in task["probe"]["subtitles"] if s["index"] == track]
        if not options or options[0]["codec"] not in (
            "subrip",
            "ass",
            "ssa",
            "webvtt",
            "mov_text",
            "text",
        ):
            raise ValueError("请选择受支持的内嵌文本字幕轨道；图片字幕需要 OCR")
        raw = run(
            [
                FFMPEG,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(get_media_path(task)),
                "-map",
                f"0:{track}",
                "-f",
                "srt",
                "-",
            ]
        )
        parsed = list(srt.parse(raw.decode("utf-8-sig")))
        with db.connect(write=True) as conn:
            if conn.execute(
                "SELECT 1 FROM subtitles WHERE task_id=?", (task["id"],)
            ).fetchone():
                raise ValueError("已有字幕，请新建任务使用内嵌字幕")
            for item in parsed:
                text = item.content.strip()
                start = round(item.start.total_seconds() * 1000)
                end = round(item.end.total_seconds() * 1000)
                if not text or start < 0 or end <= start:
                    continue
                conn.execute(
                    "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        db.ident(),
                        task["id"],
                        start,
                        end,
                        text,
                        text,
                        task["params"].get("language") or options[0]["language"],
                        "embedded",
                    ),
                )


engine = Engine()

