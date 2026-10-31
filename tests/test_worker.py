from types import SimpleNamespace as NS
from backend.worker import (
    segment_row,
    split_segment_rows,
    display_width,
    MAX_DISPLAY_WIDTH,
    MAX_DURATION_MS,
)


def segment(start, end, words, text="PMC生产仓储"):
    return NS(
        start=start, end=end, text=text, words=words, avg_logprob=-0.2, no_speech_prob=0
    )


def test_cross_boundary_context_is_not_emitted_twice():
    before = segment(
        178,
        182,
        [
            NS(start=178, end=179, word="PMC"),
            NS(start=179, end=180, word="生产"),
            NS(start=180, end=182, word="仓储"),
        ],
    )
    after = segment(
        0,
        4,
        [
            NS(start=0, end=1, word="PMC"),
            NS(start=1, end=2, word="生产"),
            NS(start=2, end=4, word="仓储"),
        ],
    )
    left = segment_row(before, 0, 400, 0)
    right = segment_row(after, 178, 400, 1)
    assert left["source"] == "PMC生产"
    assert right["source"] == "仓储"
    assert left["end"] <= right["start"]
    assert left["source"] + right["source"] == before.text


def test_untrimmed_sentence_and_last_boundary_remain_valid():
    row = segment_row(segment(2, 4, [], "正常句子。"), 178, 183, 1)
    assert row["source"] == "正常句子。"
    assert (row["start"], row["end"]) == (180000, 182000)
    assert segment_row(segment(0, 1, [], "上下文"), 178, 183, 1) is None


def test_split_long_japanese_segment_with_word_timestamps():
    raw_words = [
        ("心の病を抱えて、", 0.0, 1.8),
        ("助けを求めてきた患者の弱みにつけ込むなんて、", 1.8, 5.2),
        ("言語道断ですが、", 5.3, 7.5),
        ("精神科医と患者には関係性に力の差がありまして、", 7.6, 11.0),
        ("主治医の言いなりにならないと治療してもらえない、", 11.2, 14.0),
        ("見捨てられたくないという不安や恐怖が働くんです。", 14.2, 17.2),
    ]
    words = [NS(start=s, end=e, word=w) for w, s, e in raw_words]
    full_text = "".join(w for w, _, _ in raw_words)
    seg = segment(0.0, 17.2, words, text=full_text)

    rows = split_segment_rows(seg, 0, 200, 0)
    assert len(rows) >= 3
    reconstructed = "".join(r["source"] for r in rows)
    assert reconstructed == full_text
    for r in rows:
        dur = r["end"] - r["start"]
        width = display_width(r["source"])
        assert dur <= MAX_DURATION_MS
        assert width <= MAX_DISPLAY_WIDTH
        assert "字幕较长，请手动拆分" not in r["review"]
        assert len(r["words"]) > 0
        assert r["start"] == r["words"][0]["start"]
        assert r["end"] == r["words"][-1]["end"]


def test_split_long_english_segment_with_words():
    raw_words = [
        (" Artificial", 0.0, 0.6),
        (" intelligence", 0.6, 1.3),
        (" has", 1.3, 1.6),
        (" revolutionized", 1.6, 2.4),
        (" modern", 2.4, 2.9),
        (" software", 2.9, 3.5),
        (" development,", 3.5, 4.3),
        (" but", 4.5, 4.8),
        (" careful", 4.8, 5.3),
        (" engineering", 5.3, 6.0),
        (" and", 6.0, 6.2),
        (" boundary", 6.2, 6.7),
        (" validation", 6.7, 7.4),
        (" remain", 7.4, 7.9),
        (" essential", 7.9, 8.5),
        (" for", 8.5, 8.8),
        (" robust", 8.8, 9.3),
        (" production", 9.3, 9.9),
        (" systems.", 9.9, 10.6),
    ]
    words = [NS(start=s, end=e, word=w) for w, s, e in raw_words]
    full_text = "".join(w for w, _, _ in raw_words).strip()
    seg = segment(0.0, 10.6, words, text=full_text)

    rows = split_segment_rows(seg, 0, 200, 0)
    assert len(rows) >= 2
    for r in rows:
        dur = r["end"] - r["start"]
        width = display_width(r["source"])
        assert dur <= MAX_DURATION_MS
        assert width <= MAX_DISPLAY_WIDTH
        assert "字幕较长，请手动拆分" not in r["review"]


def test_short_segment_not_split():
    words = [
        NS(start=1.0, end=2.0, word="短句"),
        NS(start=2.0, end=3.0, word="测试。"),
    ]
    seg = segment(1.0, 3.0, words, text="短句测试。")
    rows = split_segment_rows(seg, 0, 200, 0)
    assert len(rows) == 1
    assert rows[0]["source"] == "短句测试。"
    assert (rows[0]["start"], rows[0]["end"]) == (1000, 3000)


def test_fallback_split_without_words():
    long_text = "这是一个较长的备用拆分测试文本，在没有词级时间戳时能够安全按照标点进行线性插值切分，从而避免单行字幕过长的问题。"
    seg = segment(0.0, 16.0, [], text=long_text)
    rows = split_segment_rows(seg, 0, 200, 0)
    assert len(rows) >= 2
    for r in rows:
        assert display_width(r["source"]) <= MAX_DISPLAY_WIDTH
        assert r["start"] < r["end"]
