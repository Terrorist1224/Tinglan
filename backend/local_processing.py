"""消费模型子进程事件并保存结果；通道占用、任务状态与启动顺序由 Engine 管理。"""
import json
import math
import subprocess
from . import db
from .config import DATA, PYTHON, ROOT
from .media import FLAGS


def run_worker(task, phase, ids, set_process, on_event, on_progress):
    """通过回调登记子进程与上报事件，避免反向导入 Engine 或 HTTP 入口。"""
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
        on_progress(
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
        set_process(process)
        try:
            # stdout 只消费带前缀的 JSON 协议；原生库诊断写入独立 stderr 日志。
            for line in process.stdout:
                if line.startswith("TINGLAN:"):
                    on_event(task["id"], phase, json.loads(line[8:]))
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
            set_process(None)


def consume(task_id, phase, event, apply_diarization):
    kind = event["type"]
    if kind == "unit":
        # 字幕与检查点在同一事务提交；崩溃恢复时一个 unit 要么全部重做，要么全部跳过。
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
                    # 这里只标记疑似重复，不删除可能是正常重复说话的字幕。
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
        apply_diarization(task_id, event["segments"])
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


def apply_diarization(task_id, segments):
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
                        r_start = run[0]["start"]
                        r_end = max(run[-1]["end"], r_start + 300)
                        conn.execute(
                            "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model,review,words,speaker) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                db.ident(),
                                task_id,
                                r_start,
                                r_end,
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


def embedded(task):
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
