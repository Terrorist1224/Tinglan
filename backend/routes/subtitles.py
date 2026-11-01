"""人工编辑与单句 AI 操作；保持乐观锁、历史快照和译文有效性语义一致。"""
from fastapi import APIRouter
import json
import time
from fastapi import HTTPException
from .. import db, translation
from ..engine import engine
from ..translation import DEFAULT_POLISH_PROMPT, DEFAULT_TRANSLATE_PROMPT, TranslationError
from ..schemas import Edit, Split, Merge, Restore
from .common import get_task
from ..settings import get_api_key, load_settings

router = APIRouter()


@router.post("/api/tasks/{task_id}/revert-polish")
def revert_polish_endpoint(task_id: str):
    get_task(task_id)
    return db.revert_polish(task_id)


@router.post("/api/tasks/{task_id}/subtitles/{sid}/translate")
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

    try:
        rows = translation.translate_batch(merged_settings, key, [target], context)
        if sid in rows:
            db.apply_translation(task_id, target, rows[sid], "llm")
    except TranslationError as exc:
        if exc.refusal:
            with db.connect(write=True) as conn:
                r_row = conn.execute("SELECT review FROM subtitles WHERE id=?", (sid,)).fetchone()
                if r_row:
                    rev = r_row["review"] or ""
                    if "模型拦截（需人工翻译）" not in rev:
                        new_rev = (rev + "；" if rev else "") + "模型拦截（需人工翻译）"
                        conn.execute("UPDATE subtitles SET review=? WHERE id=?", (new_rev, sid))
        raise
    return next(s for s in db.subtitles(task_id) if s["id"] == sid)


@router.post("/api/tasks/{task_id}/subtitles/{sid}/polish")
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


@router.post("/api/tasks/{task_id}/subtitles/{sid}/polish-and-translate")
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
        with db.connect(write=True) as conn:
            r_row = conn.execute("SELECT review FROM subtitles WHERE id=?", (sid,)).fetchone()
            if r_row:
                rev = r_row["review"] or ""
                if "润色被模型拒绝（原文直译）" not in rev:
                    new_rev = (rev + "；" if rev else "") + "润色被模型拒绝（原文直译）"
                    conn.execute("UPDATE subtitles SET review=? WHERE id=?", (new_rev, sid))
    return next(s for s in db.subtitles(task_id) if s["id"] == sid)


@router.patch("/api/tasks/{task_id}/subtitles/{sid}")
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


@router.post("/api/tasks/{task_id}/subtitles/{sid}/split")
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


@router.post("/api/tasks/{task_id}/merge")
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


@router.get("/api/tasks/{task_id}/history")
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


@router.post("/api/tasks/{task_id}/history/{hid}/restore")
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
