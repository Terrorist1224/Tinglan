import json
import time
import httpx
import pytest
from backend import db
from backend.engine import engine
from backend.translation import (
    TranslationError,
    polish_batch,
    translate_batch,
    validate_response,
)

H = {"X-Tinglan": "1"}


def test_validate_response_id_mismatch_classification():
    # 1. 缺失编号
    with pytest.raises(TranslationError) as exc_missing:
        validate_response('[{"id":"s0","text":"你好"}]', ["s0", "s1"])
    assert exc_missing.value.id_mismatch
    assert "预期 2 条，返回 1 条，缺失 1 个编号（s1）" in str(exc_missing.value)

    # 单条缺失时省略多余样本括号
    with pytest.raises(TranslationError) as exc_single_missing:
        validate_response("[]", ["s0"])
    assert exc_single_missing.value.id_mismatch
    assert str(exc_single_missing.value) == "预期 1 条，返回 0 条，缺失 1 个编号"

    # 2. 额外编号
    with pytest.raises(TranslationError) as exc_extra:
        validate_response(
            '[{"id":"s0","text":"你好"},{"id":"s_extra","text":"多余"}]',
            ["s0"],
        )
    assert exc_extra.value.id_mismatch
    assert "额外 1 个编号（s_extra）" in str(exc_extra.value)

    # 3. 重复编号
    with pytest.raises(TranslationError) as exc_dup:
        validate_response(
            '[{"id":"s0","text":"A"},{"id":"s0","text":"B"}]',
            ["s0", "s1"],
        )
    assert exc_dup.value.id_mismatch
    assert "返回存在重复编号：s0" in str(exc_dup.value)

    # 4. 顺序颠倒但 ID 完整：正常通过
    res = validate_response(
        '[{"id":"s1","text":"二"},{"id":"s0","text":"一"}]',
        ["s0", "s1"],
    )
    assert res == {"s0": "一", "s1": "二"}

    # 5. UUID 连字符格式是同一个编号，不应误判为缺失并额外
    res = validate_response(
        '[{"id":"11-128e65-62d4-4b8c-8d45-b562e85834d5","text":"也可能是压力"}]',
        ["11-128e6562d44b8c8d45b562e85834d5"],
    )
    assert res == {"11-128e6562d44b8c8d45b562e85834d5": "也可能是压力"}

    # 6. 仅大小写和连字符变化仍视为同一个编号
    res = validate_response(
        '[{"id":"DB03BC50-292C-46EA-BA64-4D453D06FB62","text":"样例"}]',
        ["db03bc50292c46eaba644d453d06fb62"],
    )
    assert res == {"db03bc50292c46eaba644d453d06fb62": "样例"}

    # 7. 真正不同的编号仍必须失败
    with pytest.raises(TranslationError) as exc_different:
        validate_response(
            '[{"id":"db03bc50-292c-46ea-ba64-4d453d06fb63","text":"样例"}]',
            ["db03bc50292c46eaba644d453d06fb62"],
        )
    assert exc_different.value.id_mismatch
    assert "请求编号 db03bc50292c46eaba644d453d06fb62，返回编号 db03bc50-292c-46ea-ba64-4d453d06fb63" in str(exc_different.value)


def test_call_api_scheduler_retries_behavior():
    calls = []

    def mock_transport(req):
        calls.append(req)
        # 始终返回缺失编号的 JSON
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '[{"id":"s0","text":"仅一条"}]'}, "finish_reason": "stop"}
                ]
            },
        )

    # 调度器模式 (_scheduler_retries=True)：遇到编号错误立即退出，不重复请求 3 次
    calls.clear()
    with httpx.Client(transport=httpx.MockTransport(mock_transport)) as client:
        with pytest.raises(TranslationError) as exc:
            polish_batch(
                {
                    "base_url": "https://api.example.com/v1",
                    "model": "test-model",
                    "_scheduler_retries": True,
                },
                "key",
                [{"id": "s0", "source": "A"}, {"id": "s1", "source": "B"}],
                [],
                client=client,
            )
        assert exc.value.id_mismatch
        assert len(calls) == 1

    # 独立单句模式 (_scheduler_retries 未设置)：遇到编号错误允许重试最多 3 次
    calls.clear()
    with httpx.Client(transport=httpx.MockTransport(mock_transport)) as client:
        with pytest.raises(TranslationError) as exc:
            polish_batch(
                {
                    "base_url": "https://api.example.com/v1",
                    "model": "test-model",
                },
                "key",
                [{"id": "s0", "source": "A"}, {"id": "s1", "source": "B"}],
                [],
                client=client,
                sleeper=lambda _: None,
            )
        assert exc.value.id_mismatch
        assert len(calls) == 3


def test_engine_batch_split_and_partial_failure_isolation(client, task, monkeypatch):
    # 构造 16 条字幕
    with db.connect() as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(16):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (f"sub_{i:02d}", task, i * 2000, i * 2000 + 1500, f"Source {i}", f"Source {i}", "ja", "test"),
            )

    settings = {
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
        "concurrency": 2,
    }

    # 模拟场景：
    # - 16 条整批请求时，返回缺少 sub_05 的错误编号结果；
    # - 拆分成 <=8 条后，除 sub_05 外所有字幕正常返回；
    # - sub_05 单条在润色阶段持续返回空或错误编号（模拟持续格式异常）。
    def mock_polish(settings, key, batch, context, **kwargs):
        batch_ids = [s["id"] for s in batch]
        if len(batch) > 8:
            # 大批次返回缺少编号
            raise TranslationError("预期 16 条，返回 15 条，缺失 1 个编号（sub_05）", id_mismatch=True)
        if "sub_05" in batch_ids:
            if len(batch) > 1:
                raise TranslationError(f"预期 {len(batch)} 条，返回存在缺失", id_mismatch=True)
            # 单条 sub_05 始终报错
            raise TranslationError("预期 1 条，返回 0 条，缺失 1 个编号", id_mismatch=True)
        return {s["id"]: f"Polished {s['source']}" for s in batch}

    def mock_translate(settings, key, batch, context, **kwargs):
        return {s["id"]: f"Translated {s['source']}" for s in batch}

    monkeypatch.setattr("backend.engine.polish_batch", mock_polish)
    monkeypatch.setattr("backend.engine.translate_batch", mock_translate)

    engine.start(task, "polish_translate", settings, "mock-key")
    # 等待执行完成
    channel = engine.channels["cloud"]
    if channel.thread:
        channel.thread.join(timeout=10)

    cur_task = db.task(task)
    assert cur_task["status"] == "failed"
    # 验证错误信息明确指出失败阶段、字幕起止时间与差异摘要
    assert "部分批次处理失败" in cur_task["error"]
    assert "润色失败｜00:00:10.000—00:00:11.500｜预期 1 条，返回 0 条，缺失 1 个编号" in cur_task["error"]

    subs = {s["id"]: s for s in db.subtitles(task)}
    assert len(subs) == 16
    # 验证其余 15 条字幕均润色并翻译成功
    for i in range(16):
        sid = f"sub_{i:02d}"
        if sid == "sub_05":
            assert subs[sid]["translation"] == ""
            assert subs[sid]["source"] == "Source 5"
        else:
            assert subs[sid]["source"] == f"Polished Source {i}"
            assert subs[sid]["translation"] == f"Translated Polished Source {i}"

    # 验证恢复处理：当模型恢复正常后，只处理未完成的 sub_05，已润色并翻译的 15 条不重复处理
    polished_calls = []

    def mock_polish_recovered(settings, key, batch, context, **kwargs):
        polished_calls.extend([s["id"] for s in batch])
        return {s["id"]: f"Polished {s['source']}" for s in batch}

    monkeypatch.setattr("backend.engine.polish_batch", mock_polish_recovered)

    engine.start(task, "polish_translate", settings, "mock-key", resume=True)
    if channel.thread:
        channel.thread.join(timeout=10)

    resumed_task = db.task(task)
    assert resumed_task["status"] == "complete"
    # 确认恢复时只给 sub_05 发送润色，没有重复润色其他 15 条
    assert polished_calls == ["sub_05"]
    final_sub5 = next(s for s in db.subtitles(task) if s["id"] == "sub_05")
    assert final_sub5["translation"] == "Translated Polished Source 5"


def test_engine_translation_stage_id_mismatch_split_and_isolation(client, task, monkeypatch):
    # 构造 4 条字幕测试纯翻译阶段 (translate) 的编号异常二分与单句重试隔离
    with db.connect() as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(4):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (f"tr_{i}", task, i * 1000, i * 1000 + 800, f"Source {i}", f"Source {i}", "ja", "test"),
            )

    settings = {"base_url": "https://api.example.com/v1", "model": "test-model"}

    def mock_translate(settings, key, batch, context, **kwargs):
        batch_ids = [s["id"] for s in batch]
        if "tr_2" in batch_ids:
            if len(batch) > 1:
                raise TranslationError("预期多条，返回缺失", id_mismatch=True)
            raise TranslationError("预期 1 条，返回 0 条，缺失 1 个编号", id_mismatch=True)
        return {s["id"]: f"Translated {s['source']}" for s in batch}

    monkeypatch.setattr("backend.engine.translate_batch", mock_translate)

    engine.start(task, "translate", settings, "mock-key")
    channel = engine.channels["cloud"]
    if channel.thread:
        channel.thread.join(timeout=10)

    cur_task = db.task(task)
    assert cur_task["status"] == "failed"
    assert "翻译失败｜00:00:02.000—00:00:02.800｜预期 1 条，返回 0 条，缺失 1 个编号" in cur_task["error"]

    subs = {s["id"]: s for s in db.subtitles(task)}
    assert subs["tr_0"]["translation"] == "Translated Source 0"
    assert subs["tr_1"]["translation"] == "Translated Source 1"
    assert subs["tr_2"]["translation"] == ""
    assert subs["tr_3"]["translation"] == "Translated Source 3"


def test_engine_version_conflict_prevents_overwrite_on_split(client, task, monkeypatch):
    # 测试在二分重试期间用户手动编辑字幕，乐观锁阻止覆盖
    with db.connect() as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        conn.execute(
            "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
            ("c_0", task, 0, 1000, "Orig 0", "Orig 0", "ja", "test"),
        )
        conn.execute(
            "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
            ("c_1", task, 1000, 2000, "Orig 1", "Orig 1", "ja", "test"),
        )

    settings = {"base_url": "https://api.example.com/v1", "model": "test-model"}
    attempts = 0

    def mock_polish(settings, key, batch, context, **kwargs):
        nonlocal attempts
        attempts += 1
        if len(batch) > 1:
            # 首次整批失败，触发二分
            raise TranslationError("缺少编号", id_mismatch=True)
        # 单条时：如果是 c_0，在返回前模拟用户在前端保存了修改（增加 version）
        if batch[0]["id"] == "c_0":
            with db.connect(write=True) as conn:
                conn.execute("UPDATE subtitles SET source='User Edited', version=version+1 WHERE id='c_0'")
        return {s["id"]: f"AI Polish {s['source']}" for s in batch}

    monkeypatch.setattr("backend.engine.polish_batch", mock_polish)

    engine.start(task, "polish", settings, "mock-key")
    channel = engine.channels["cloud"]
    if channel.thread:
        channel.thread.join(timeout=10)

    subs = {s["id"]: s for s in db.subtitles(task)}
    # c_0 被用户修改，AI 润色由于乐观锁未覆盖
    assert subs["c_0"]["source"] == "User Edited"
    # c_1 正常润色成功
    assert subs["c_1"]["source"] == "AI Polish Orig 1"
