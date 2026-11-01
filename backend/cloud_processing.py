"""云端任务内的批次并发、退避与写回；跨任务排队和生命周期仍由 Engine 管理。"""
import math
import threading
import time
from collections import deque
from . import db
from .config import DATA
from .export import timestamp
from .translation import DEFAULT_POLISH_PROMPT, DEFAULT_TRANSLATE_PROMPT, TranslationError, create_batches, polish_batch, translate_batch


AUTO_CONCURRENCY_CEILING = 16


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



def process_llm(task, phase, settings, key, ids, overwrite, should_stop):
    """执行一个云端任务；should_stop 由调度器提供，暂停和恢复进度写入 params.operation。"""
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

    task = db.task(task["id"])
    all_subs = db.subtitles(task["id"])
    operation = task["params"].get("operation", {})
    # target_ids 固定本轮范围；completed/restricted 记录结果，剩余项才能在恢复时继续。
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
    # 请求线程会同时修改活动请求数与进度；锁只保护任务内状态，不占用调度锁。
    state_lock = threading.RLock()
    active_requests = 0
    stable_successes = 0
    # 共享退避截止时间，防止各批次各自重试而继续冲击被限流的上游。
    backoff_until = 0.0
    stop_path = DATA / task["id"] / "stop"
    active_futures = {}

    def is_stopping():
        return stop_path.exists() or should_stop()

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
                            for seg in batch:
                                rev = seg.get("review") or ""
                                if "润色被模型拒绝（原文直译）" not in rev:
                                    new_rev = (rev + "；" if rev else "") + "润色被模型拒绝（原文直译）"
                                    seg["review"] = new_rev
                                    with db.connect(write=True) as conn:
                                        conn.execute("UPDATE subtitles SET review=? WHERE id=?", (new_rev, seg["id"]))
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
                                for seg in batch:
                                    rev = seg.get("review") or ""
                                    if "模型拦截（需人工翻译）" not in rev:
                                        new_rev = (rev + "；" if rev else "") + "模型拦截（需人工翻译）"
                                        seg["review"] = new_rev
                                        with db.connect(write=True) as conn:
                                            conn.execute("UPDATE subtitles SET review=? WHERE id=?", (new_rev, seg["id"]))
                        else:
                            batch_errors.append("润色失败：" + str(exc))
                    # 编号错误与数据校验异常处理：多条批次二分拆解，单条重试 1 次后作为未完成项报错
                    elif isinstance(exc, TranslationError) and (getattr(exc, "id_mismatch", False) or "空文本" in str(exc) or "无效编号" in str(exc)):
                        stable_successes = 0
                        if stage == "polish" and phase == "polish_translate" and ("空文本" in str(exc) or not getattr(exc, "id_mismatch", False)):
                            polish_skips.append(str(exc))
                            polished_ids.update(batch_ids)
                            for seg in batch:
                                rev = seg.get("review") or ""
                                if "润色被模型拒绝（原文直译）" not in rev:
                                    new_rev = (rev + "；" if rev else "") + "润色被模型拒绝（原文直译）"
                                    seg["review"] = new_rev
                                    with db.connect(write=True) as conn:
                                        conn.execute("UPDATE subtitles SET review=? WHERE id=?", (new_rev, seg["id"]))
                            pending.appendleft(dict(batch=batch, context=context, stage="translate", retries=0))
                        elif len(batch) > 1:
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
