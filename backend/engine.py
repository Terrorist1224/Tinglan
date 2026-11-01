# 任务调度与后台执行引擎：管理本地模型与云端 LLM 两条独立 FIFO 通道。
# 本地通道一次执行一个转录/分人/预览任务；云端通道一次执行一个润色/翻译任务；
# 云端任务内部继续使用自适应批次并发。队列只存在于进程内，重启后全部中断。
import subprocess
import threading
import time
from collections import deque
from . import db, local_processing, cloud_processing
from .cloud_processing import AUTO_CONCURRENCY_CEILING, recommended_concurrency
from .config import DATA
from .models import ready as model_ready
from .translation import DEFAULT_POLISH_PROMPT, DEFAULT_TRANSLATE_PROMPT


LOCAL_PHASES = {"transcribe", "diarize", "embedded"}
CLOUD_PHASES = {"polish", "translate", "polish_translate"}
ALL_PHASES = LOCAL_PHASES | CLOUD_PHASES




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
        # 启动、出队和自动衔接会重入调度；通道状态的读写统一使用同一把 RLock。
        self.schedule_lock = threading.RLock()
        self.channels = {"local": Channel("local"), "cloud": Channel("cloud")}
        # 本地结束到云端入队之间仍保留排名，避免界面出现任务短暂消失。
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

    # 调用方必须持有 schedule_lock，确保活动任务占位和启动线程不可被其他请求插入。
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
                # Keep queue rank visible until the local-to-cloud handoff finishes.
                for pending_task in pending_auto:
                    try:
                        self._auto_cloud(pending_task)
                    finally:
                        self._pending_auto_cloud.discard(pending_task)

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

        # 恢复时重查译文是否仍匹配原文版本，已完成清单可能因人工修改而失效。
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
        from .settings import get_api_key, load_settings
        return load_settings(), get_api_key()


    # LLM 批量处理流水线（负责 AI 润色、一键翻译或润色并翻译）
    # 架构流程：
    # 1. 待处理行筛选：区分全量目标、已完成（completed_ids）、审查受限（restricted_ids）与剩余待处理；
    # 2. 批次划分：调用 create_batches 按 16 条/2400 字符分块，并关联前后相邻上下文；
    # 3. 动态并发派发：ThreadPoolExecutor 派发请求，严格维护 active_requests <= concurrency；
    # 4. 拒译细分与退避：捕获 refusal 异常并对大批次二分拆解，单个受限句计入 restricted_ids 留待人工处理；
    # 5. 乐观锁写回：调用 db.apply_polish / db.apply_translation，比对 version 防止覆盖用户编辑。
    def process_llm(self, task, phase, settings, key, ids, overwrite):
        return cloud_processing.process_llm(
            task, phase, settings, key, ids, overwrite, lambda: self.stopping,
        )

    # 调用本地模型运行器（Whisper 转录或 pyannote 说话人分离）
    # 架构设计：将计算开销大且依赖 C++/CUDA 原生库的模型放到独立子进程 (backend.worker) 中运行，
    # 避免潜在的 PyTorch 底层崩溃（如 0xc0000005 访问违规）导致 FastAPI 主服务退出；
    # 子进程通过 stdout 按行输出 JSON 事件 (TINGLAN:...)，主进程通过 consume 解析并落库。
    def worker(self, task, phase, ids):
        return local_processing.run_worker(
            task, phase, ids,
            lambda process: setattr(self, "process", process),
            self.consume, self.set_progress_detail,
        )

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
        return local_processing.consume(task_id, phase, event, self.apply_diarization)

    # 将说话人识别结果应用到字幕表：
    # - 若存在词级时间戳 (words)：计算每个词的时间中点匹配说话人，在说话人切换处精确拆分字幕；
    # - 若无词级时间戳：根据字幕时间区间与各说话人发声区间的重合时长比例判定归属；重叠严重时打上待校对标记。
    def apply_diarization(self, task_id, segments):
        return local_processing.apply_diarization(task_id, segments)

    # 提取视频内嵌字幕轨，直接封装为 SRT 格式写入字幕库
    def embedded(self, task):
        return local_processing.embedded(task)


engine = Engine()

