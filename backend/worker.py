# 独立模型工作进程：在单独进程中执行 Whisper 语音转录与 pyannote 说话人分离，
# 隔离计算负载与 PyTorch 原生底层崩溃，通过标准输出流式发送 JSON-Lines 协议事件。
"""Run one model at a time in a separate process; emit durable unit results."""

import json
import math
import os
import sys
import time
import wave
import unicodedata
from pathlib import Path
from .config import MODELS, DATA
from .media import audio


def emit(kind, **values):
    print(
        "TINGLAN:" + json.dumps({"type": kind, **values}, ensure_ascii=False),
        flush=True,
    )


def stopped(task):
    return (DATA / task["id"] / "stop").exists()


def device(task):
    import torch

    chosen = task["params"].get("device", "cuda")
    if chosen == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA 不可用；请修复环境或在任务中明确切换 CPU")
    return chosen


# 将 Whisper 识别的片段转换为标准化字幕行并进行分块交界去重
# 分块去重与边界处理：
# - 任务音频按 180 秒划分核心单元 [core_start, core_end)，并带有前后重叠外延；
# - 存在词级时间戳 (segment.words) 时：计算每个词的时间中点，仅保留中点落在 [core_start, core_end) 核心区内的词；
#   若交界处词被截断，按保留词重新计算起止时间与文本，防止相邻分块重复识别相同词汇；
# - 无词级时间戳时：按整句时间中点裁决是否归属于当前单元，不在核心区则直接丢弃。
def segment_row(segment, offset, duration, unit):
    core_start = unit * 180
    core_end = min(duration, (unit + 1) * 180)
    start, end = offset + segment.start, min(duration, offset + segment.end)
    text = segment.text.strip()
    boundary = start < core_start or end > core_end
    words = []
    if segment.words:
        all_words = [
            {
                "word": w.word,
                "start": max(0, round((offset + w.start) * 1000)),
                "end": max(
                    max(0, round((offset + w.start) * 1000)) + 100,
                    round(min(duration, offset + w.end) * 1000),
                ),
                "prob": round(float(getattr(w, "probability", 1.0)), 3),
            }
            for w in segment.words
        ]
        if boundary:
            kept_words = [
                all_words[i]
                for i, w in enumerate(segment.words)
                if core_start <= offset + (w.start + w.end) / 2 < core_end
            ]
            if not kept_words:
                return None
            words = kept_words
            text = "".join(w["word"] for w in words).strip()
            start = max(core_start, words[0]["start"] / 1000.0)
            end = min(core_end, words[-1]["end"] / 1000.0)
        else:
            words = all_words
            if words:
                start = max(core_start, min(words[0]["start"] / 1000.0, end - 0.2))
                end = min(
                    core_end,
                    max(
                        words[-1]["end"] / 1000.0,
                        min(offset + segment.end, words[-1]["end"] / 1000.0 + 0.3),
                    ),
                )
    elif not core_start <= (start + end) / 2 < core_end:
        return None

    if not text or end <= start:
        return None
    reasons = []
    if segment.avg_logprob < -1 or segment.no_speech_prob > 0.6:
        reasons.append("语音或文字需检查")
    if boundary:
        reasons.append("分块交界，请回放确认")
    s_ms = max(0, round(start * 1000))
    e_ms = max(s_ms + 300, round(end * 1000))
    return {
        "start": s_ms,
        "end": e_ms,
        "source": text,
        "words": words,
        "review": "；".join(reasons),
    }


MAX_DISPLAY_WIDTH = 72
MAX_DURATION_MS = 7000
SOFT_DISPLAY_WIDTH = 28
SOFT_DURATION_MS = 3200
MIN_PAUSE_GAP_MS = 350
BREAK_CHARS = set("、，,；;。！？!?.…:：—–،؛؟¿¡")


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _split_fallback_text(base):
    source = base.get("source", "").strip()
    if not source:
        return [base]

    total_w = display_width(source)
    total_d = base["end"] - base["start"]
    if total_w <= MAX_DISPLAY_WIDTH and total_d <= MAX_DURATION_MS:
        reasons = [r for r in base.get("review", "").split("；") if r and r != "字幕较长，请手动拆分"]
        return [{**base, "review": "；".join(reasons)}]

    words_list = source.split(" ") if " " in source else list(source)
    joiner = " " if " " in source else ""

    pieces = []
    cur_tokens = []
    for token in words_list:
        cur_tokens.append(token)
        cur_text = joiner.join(cur_tokens)
        cur_w = display_width(cur_text)
        has_break = any(p in token for p in BREAK_CHARS)

        if (cur_w >= SOFT_DISPLAY_WIDTH and has_break) or cur_w >= MAX_DISPLAY_WIDTH:
            pieces.append(cur_text)
            cur_tokens = []

    if cur_tokens:
        remainder = joiner.join(cur_tokens)
        if pieces and display_width(remainder) <= 8:
            pieces[-1] = pieces[-1] + joiner + remainder
        else:
            pieces.append(remainder)

    if len(pieces) <= 1:
        reasons = [r for r in base.get("review", "").split("；") if r and r != "字幕较长，请手动拆分"]
        return [{**base, "review": "；".join(reasons)}]

    total_duration = max(1, base["end"] - base["start"])
    base_start = base["start"]
    char_count = sum(len(p) for p in pieces)

    results = []
    accum_chars = 0
    reasons = [r for r in base.get("review", "").split("；") if r and r != "字幕较长，请手动拆分"]
    clean_review = "；".join(reasons)

    for i, piece in enumerate(pieces):
        p_len = len(piece)
        p_start = base_start + round((accum_chars / char_count) * total_duration)
        accum_chars += p_len
        p_end = base_start + round((accum_chars / char_count) * total_duration)
        if i == len(pieces) - 1:
            p_end = base["end"]
        results.append({
            "start": p_start,
            "end": max(p_start + 300, p_end),
            "source": piece.strip(),
            "words": [],
            "review": clean_review,
        })
    return results


def split_row_if_long(base):
    words = base.get("words") or []
    total_w = display_width(base.get("source", ""))
    total_d = base["end"] - base["start"]

    if total_w <= MAX_DISPLAY_WIDTH and total_d <= MAX_DURATION_MS:
        reasons = [r for r in base.get("review", "").split("；") if r and r != "字幕较长，请手动拆分"]
        return [{**base, "review": "；".join(reasons)}]

    if len(words) >= 2:
        chunks = []
        cur = []
        for i, w in enumerate(words):
            if cur:
                cand_text = "".join(x["word"] for x in cur) + w["word"]
                cand_w = display_width(cand_text)
                cand_d = w["end"] - cur[0]["start"]
                has_break = any(p in w["word"] for p in BREAK_CHARS)

                if (cand_w > MAX_DISPLAY_WIDTH or cand_d > MAX_DURATION_MS) and not has_break:
                    chunks.append(cur)
                    cur = []

            cur.append(w)
            if i + 1 == len(words):
                break

            w_text = w["word"].strip()
            has_break = any(p in w_text for p in BREAK_CHARS)
            gap = words[i + 1]["start"] - w["end"]
            has_pause = gap >= MIN_PAUSE_GAP_MS

            cur_w = display_width("".join(x["word"] for x in cur))
            cur_d = cur[-1]["end"] - cur[0]["start"]

            soft_cond = (cur_w >= SOFT_DISPLAY_WIDTH or cur_d >= SOFT_DURATION_MS) and (has_break or has_pause)
            hard_cond = cur_w >= MAX_DISPLAY_WIDTH or cur_d >= MAX_DURATION_MS

            if soft_cond or hard_cond:
                chunks.append(cur)
                cur = []

        if cur:
            chunks.append(cur)

        if len(chunks) >= 2:
            last_w = display_width("".join(x["word"] for x in chunks[-1]))
            last_d = chunks[-1][-1]["end"] - chunks[-1][0]["start"]
            if last_w <= 8 or last_d < 1000:
                chunks[-2].extend(chunks.pop())

        reasons = [r for r in base.get("review", "").split("；") if r and r != "字幕较长，请手动拆分"]
        clean_review = "；".join(reasons)

        results = []
        for chunk in chunks:
            chunk_text = "".join(w["word"] for w in chunk).strip()
            if not chunk_text:
                continue
            c_start = chunk[0]["start"]
            c_end = max(chunk[-1]["end"], c_start + 300)
            results.append({
                "start": c_start,
                "end": c_end,
                "source": chunk_text,
                "words": chunk,
                "review": clean_review,
            })
        if results:
            return results

    return _split_fallback_text(base)


def split_segment_rows(segment, offset, duration, unit):
    base = segment_row(segment, offset, duration, unit)
    if not base:
        return []
    return split_row_if_long(base)


def normalize_audio_samples(samples):
    """自适应峰值放大弱音音频，避免轻声与微弱语音被静音门槛误切。"""
    # shortcut: 线性峰值归一化未引入多段动态压限，高底噪环境需配合降噪或后续动态降噪升级
    if samples is None or len(samples) == 0:
        return samples
    import numpy as np

    max_val = float(np.max(np.abs(samples)))
    if 1e-4 < max_val < 0.7:
        return samples * (0.95 / max_val)
    return samples


# 执行 Whisper 语音识别，支持断点恢复跳过已完成单元，逐分块输出识别结果与检查点
def transcribe(request):
    import numpy as np
    import torch

    dll = Path(torch.__file__).parent / "lib"
    os.environ["PATH"] = str(dll) + os.pathsep + os.environ["PATH"]
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(dll))
    from faster_whisper import WhisperModel

    task = request["task"]
    chosen = device(task)
    path = MODELS / "whisper-large-v3"
    if not (path / "model.bin").exists():
        raise ValueError("缺少 Whisper large-v3，请先在设置中下载模型")
    precision = "float16" if chosen == "cuda" else "int8"
    emit(
        "notice",
        text="运行设备："
        + chosen
        + "，Whisper large-v3，精度 "
        + precision
        + "，batch 1，beam 5",
    )
    model = WhisperModel(
        str(path),
        device=chosen,
        compute_type=precision,
        local_files_only=True,
        num_workers=1,
    )
    duration = task["probe"]["duration"]
    total = math.ceil(duration / 180)
    language = task["params"].get("language") or None
    for unit in range(total):
        if stopped(task):
            break
        if unit in request["completed"]:
            emit("progress", progress=(unit + 1) / total * 100)
            continue
        start = max(0, unit * 180 - 2)
        end = min(duration, (unit + 1) * 180 + 2)
        clip = DATA / task["id"] / "chunk.wav"
        audio(task, clip, start, end - start)
        with wave.open(str(clip), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (
                1,
                2,
                16000,
            ):
                raise ValueError("识别音频必须为 16kHz 单声道 PCM16")
            samples = (
                np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(
                    np.float32
                )
                / 32768.0
            )
        samples = normalize_audio_samples(samples)
        segments, info = model.transcribe(
            samples,
            language=language,
            task="transcribe",
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                threshold=0.35,
                min_silence_duration_ms=800,
                speech_pad_ms=250,
            ),
            no_speech_threshold=0.8,
            word_timestamps=True,
            initial_prompt=task["params"].get("glossary") or None,
            condition_on_previous_text=True,
        )
        detected_language = info.language
        rows = []
        for segment in segments:
            for row in split_segment_rows(segment, start, duration, unit):
                rows.append({**row, "language": detected_language})
        emit("unit", unit=unit, rows=rows, progress=(unit + 1) / total * 100)
        clip.unlink(missing_ok=True)


# 执行 pyannote 说话人分割与聚类，输出说话人发声时间区间段
def diarize(request):
    import soundfile as sf
    import torch
    from pyannote.audio import Pipeline

    task = request["task"]
    chosen = device(task)
    path = MODELS / "community-1"
    if not (path / "config.yaml").exists():
        raise ValueError("缺少分人模型 community-1，请先在设置中下载或导入")

    emit("notice", text="运行设备：" + chosen + "，pyannote community-1 说话人识别")
    wav_path = DATA / task["id"] / "diarize.wav"
    audio(task, wav_path, 0, task["probe"]["duration"])
    wav_data, sr = sf.read(str(wav_path), dtype="float32")
    if wav_data.ndim > 1:
        wav_data = wav_data.mean(axis=1)
    waveform = torch.from_numpy(wav_data).unsqueeze(0)
    audio_input = {"waveform": waveform, "sample_rate": sr}

    pipeline = Pipeline.from_pretrained(str(path))
    pipeline.to(torch.device(chosen))

    num_speakers = task["params"].get("num_speakers")
    kwargs = {}
    if isinstance(num_speakers, int) and num_speakers > 0:
        kwargs["num_speakers"] = num_speakers

    result = pipeline(audio_input, **kwargs)
    segments = []
    for turn, _, speaker in result.itertracks(yield_label=True):
        segments.append({
            "start": round(turn.start * 1000),
            "end": round(turn.end * 1000),
            "speaker": str(speaker),
        })
    wav_path.unlink(missing_ok=True)
    emit("diarization", segments=segments)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    begin = time.perf_counter()
    import threading
    import torch

    samples = []
    monitor_stop = threading.Event()

    def monitor():
        while not monitor_stop.is_set():
            try:
                free, total = torch.cuda.mem_get_info()
                samples.append((total - free) / 1024**2)
            except Exception:
                break
            monitor_stop.wait(0.5)

    if request["task"]["params"].get("device") == "cuda" and torch.cuda.is_available():
        threading.Thread(target=monitor, daemon=True).start()
    try:
        if request["phase"] == "transcribe":
            transcribe(request)
        elif request["phase"] == "diarize":
            diarize(request)
        else:
            raise ValueError("不支持的处理阶段: " + request["phase"])
    finally:
        monitor_stop.set()

    import torch

    emit(
        "metrics",
        phase=request["phase"],
        elapsed_seconds=round(time.perf_counter() - begin, 2),
        peak_torch_mb=round(torch.cuda.max_memory_allocated() / 1024**2, 1)
        if torch.cuda.is_available()
        else 0,
        device_peak_mb=round(max(samples), 1) if samples else 0,
        device_baseline_mb=round(samples[0], 1) if samples else 0,
    )
