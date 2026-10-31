# 字幕导出模块：支持 SRT、VTT、TXT 和 JSON 格式，包含时间重叠切分算法与失效译文安全降级。
import json


class ExportError(ValueError):
    def __init__(self, message, subtitle_id, reason):
        super().__init__(message)
        self.subtitle_id = subtitle_id
        self.reason = reason


def timestamp(ms, vtt=False):
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02}:{m:02}:{s:02}{'.' if vtt else ','}{ms:03}"


def format_txt_timestamp(ms):
    s = ms // 1000
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"[{h:02d}:{m:02d}:{sec:02d}]"


# 将时间重叠的字幕行切分为互不重叠的时间区间（用于 SRT / VTT 渲染）
# 业务逻辑与约束：
# 1. 重叠切分：提取所有字幕的 start 和 end 作为切分点，将连续时间轴离散为 [t0, t1] 区间；
# 2. 内容合并：收集每个区间内所有活跃的字幕文本进行堆叠合并；相邻内容完全一致的区间自动合并；
# 3. 过期译文降级：严格校验 translated_version == source_version，若原文修改导致译文失效（stale），
#    在翻译模式或双语模式下安全回退使用当前原文补位，杜绝过期错误译文导出到最终成品中；
# 4. 双语显示顺序：在 SRT/VTT 切片渲染中，译文在上一行，原文在下一行。
def slice_intervals(rows, mode):
    points = sorted(set(p for r in rows for p in (r["start"], r["end"])))
    intervals = []
    for t0, t1 in zip(points[:-1], points[1:]):
        if t1 <= t0:
            continue
        active = [r for r in rows if r["start"] <= t0 and r["end"] >= t1]
        if not active:
            continue
        active.sort(key=lambda r: (r["start"], r["id"]))
        blocks = []
        for r in active:
            src = r["source"].strip()
            trans = (r.get("translation") or "").strip()
            is_valid = bool(trans and r.get("translated_version") == r.get("source_version"))
            valid_trans = trans if is_valid else ""
            if mode == "source":
                b = src
            elif mode == "translation":
                b = valid_trans if valid_trans else src
            else:  # bilingual
                b = f"{valid_trans}\n{src}" if valid_trans else src
            b = b.replace("\r", "").replace("-->", "→")
            blocks.append(b)
        text = "\n".join(blocks)
        if intervals and intervals[-1]["text"] == text and intervals[-1]["end"] == t0:
            intervals[-1]["end"] = t1
        else:
            intervals.append({"start": t0, "end": t1, "text": text})
    return intervals


# 字幕格式渲染主函数
# 格式差异说明：
# - json: 导出包含起止时间（ms）、版本号、词级时间戳、说话人、校对标记等全部元数据的完整对象；
# - txt: 纯文本导出，附带秒级时间戳 [hh:mm:ss] 与说话人名称，双语模式分别输出原文与译文行；
# - srt / vtt: 通过 slice_intervals 处理为标准带时分秒毫秒轴且无重叠的字幕块。
def render(rows, fmt, mode, speakers=None, allow_incomplete=True):
    if fmt not in ("srt", "vtt", "txt", "json") or mode not in (
        "source",
        "translation",
        "bilingual",
    ):
        raise ValueError("无效导出格式")
    if not rows:
        raise ValueError("没有可导出的字幕")
    speakers = speakers or {}
    for row in rows:
        if row["start"] < 0 or row["end"] <= row["start"] or not row["source"].strip():
            raise ExportError(
                "存在无效时间或空原文，请校对后导出", row["id"], "invalid_subtitle"
            )
    if fmt == "json":
        return json.dumps(
            {"schema": 1, "subtitles": rows}, ensure_ascii=False, indent=2
        )
    if fmt == "txt":
        lines = []
        for r in rows:
            ts = format_txt_timestamp(r["start"])
            spk_id = str(r.get("speaker") or "").strip()
            spk_label = ""
            if spk_id:
                name = speakers.get(spk_id, {}).get("name") if isinstance(speakers, dict) else None
                spk_label = name or f"说话人 {spk_id}"
            prefix = f"{ts} {spk_label}:" if spk_label else ts
            src = r["source"].strip()
            trans = (r.get("translation") or "").strip()
            is_valid = bool(trans and r.get("translated_version") == r.get("source_version"))
            valid_trans = trans if is_valid else ""
            if mode == "source":
                lines.append(f"{prefix} {src}")
            elif mode == "translation":
                lines.append(f"{prefix} {valid_trans if valid_trans else src}")
            else:  # bilingual
                lines.append(f"{prefix} 原文: {src}")
                if valid_trans:
                    lines.append(f"{prefix} 译文: {valid_trans}")
        return "\n".join(lines)

    intervals = slice_intervals(rows, mode)
    blocks = ["WEBVTT\n"] if fmt == "vtt" else []
    for i, it in enumerate(intervals, 1):
        blocks.append(
            f"{i}\n{timestamp(it['start'], fmt == 'vtt')} --> {timestamp(it['end'], fmt == 'vtt')}\n{it['text']}\n"
        )
    return "\n".join(blocks)
