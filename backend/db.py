# 数据持久化层：管理 tasks、subtitles、history、checkpoints、settings 表的读写，
# 并提供基于乐观锁版本控制的字幕写回与回滚函数。
import contextlib
import json
import sqlite3
import time
import uuid
from .config import DATA


def ident():
    return uuid.uuid4().hex


# 数据库连接上下文管理器
# write=True 时使用 BEGIN IMMEDIATE 获取写锁，防止并发写入死锁；默认 WAL 模式支持多读单写
@contextlib.contextmanager
def connect(write=False):
    db = sqlite3.connect(DATA / "tinglan.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
    finally:
        db.close()


# 初始化数据库表结构与中断状态恢复
def init():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        # 表结构约束：
        # - tasks: 任务主表，params 与 probe 存 JSON；status 维护生命周期；revision 记录版本
        # - subtitles: 字幕行。start/end 单位毫秒（ms）；original 为识别基准；source 为当前有效原文；translation 为译文
        #   version 为整行乐观锁；source_version 为原文版本；translated_version 为译文所依据的原文版本
        #   当 source_version != translated_version 时表示译文已失效（stale 状态）
        #   manual_translation 区分人工编辑(1)与机翻(0)；words 存词级时间戳用于分块去重与说话人切分
        # - history: 变更前快照，用于历史版本查看与回滚
        # - checkpoints: 转录/识别已完成单元检查点，用于异常崩溃或中断后断点续传
        # - settings: 全局配置单例
        db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, media TEXT NOT NULL,
            probe TEXT NOT NULL DEFAULT '{}', params TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'ready', phase TEXT NOT NULL DEFAULT '',
            progress REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
            control TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
            updated REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS subtitles (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            start INTEGER NOT NULL, end INTEGER NOT NULL, source TEXT NOT NULL,
            original TEXT NOT NULL, translation TEXT NOT NULL DEFAULT '',
            translation_provider TEXT NOT NULL DEFAULT '',
            speaker TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 0, source_version INTEGER NOT NULL DEFAULT 0,
            translated_version INTEGER NOT NULL DEFAULT -1, manual_translation INTEGER NOT NULL DEFAULT 0,
            confirmed INTEGER NOT NULL DEFAULT 0, review TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '', alignment TEXT NOT NULL DEFAULT 'sentence');
        CREATE INDEX IF NOT EXISTS subtitle_task ON subtitles(task_id,start);
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            subtitle_id TEXT NOT NULL, snapshot TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS checkpoints (
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            phase TEXT NOT NULL, unit INTEGER NOT NULL, PRIMARY KEY(task_id,phase,unit));
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(subtitles)")}
        if "words" not in columns:
            db.execute("ALTER TABLE subtitles ADD COLUMN words TEXT NOT NULL DEFAULT '[]'")
        if "translation_provider" not in columns:
            db.execute(
                "ALTER TABLE subtitles ADD COLUMN translation_provider TEXT NOT NULL DEFAULT ''"
            )
        db.execute(
            "UPDATE subtitles SET translation_provider='' WHERE translation_provider IS NULL OR translation_provider NOT IN ('google','azure','llm','manual','')"
        )
        # 服务启动时将上次未正常结束的运行中任务置为中断状态，用户可选择恢复
        db.execute(
            "UPDATE tasks SET status='interrupted',error='上次处理意外中断，可恢复',control='' WHERE status IN ('running','queued')"
        )


def task(task_id):
    with connect() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise KeyError("任务不存在")
    item = dict(row)
    for key in ("params", "probe"):
        item[key] = json.loads(item[key])
    return item


def update_task(task_id, **fields):
    allowed = {
        "params",
        "probe",
        "status",
        "phase",
        "progress",
        "error",
        "control",
        "revision",
    }
    if not fields or not set(fields) <= allowed:
        raise ValueError("无效任务字段")
    fields["updated"] = time.time()
    with connect(write=True) as db:
        db.execute(
            "UPDATE tasks SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
            (
                *[
                    json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v
                    for v in fields.values()
                ],
                task_id,
            ),
        )


def subtitles(task_id):
    with connect() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM subtitles WHERE task_id=? ORDER BY start,end,id",
                (task_id,),
            )
        ]
        for r in rows:
            if "words" in r and isinstance(r["words"], str):
                try:
                    r["words"] = json.loads(r["words"])
                except Exception:
                    r["words"] = []
        return rows


# 在修改字幕前将当前行完整快照写入 history 表，供历史记录查看与撤回回滚
def snapshot(db, row):
    db.execute(
        "INSERT INTO history(task_id,subtitle_id,snapshot,created) VALUES(?,?,?,?)",
        (
            row["task_id"],
            row["id"],
            json.dumps(dict(row), ensure_ascii=False),
            time.time(),
        ),
    )


# 将 AI 翻译结果写回数据库
# 校验请求发起时的 segment['version']：若期间字幕被修改过（版本不符），放弃写回以防覆盖用户改动
# 写回时将 translated_version 同步为当前 source_version，并将 manual_translation 置 0，整行版本 version 递增
def apply_translation(task_id, segment, text, provider="llm"):
    with connect(write=True) as db:
        row = db.execute(
            "SELECT * FROM subtitles WHERE id=? AND task_id=?", (segment["id"], task_id)
        ).fetchone()
        if not row or row["version"] != segment["version"]:
            return False
        snapshot(db, row)
        db.execute(
            "UPDATE subtitles SET translation=?,translation_provider=?,translated_version=source_version,manual_translation=0,version=version+1 WHERE id=?",
            (text, provider, row["id"]),
        )
        return True


# 将 AI 润色结果写回数据库
# 校验请求发起时的 segment['version']：版本不符则放弃写回
# 润色修改 source 文本，自增 source_version；translated_version 保持不变，使既有旧译文进入失效（stale）状态
def apply_polish(task_id, segment, text):
    with connect(write=True) as db:
        row = db.execute(
            "SELECT * FROM subtitles WHERE id=? AND task_id=?", (segment["id"], task_id)
        ).fetchone()
        if not row or row["version"] != segment["version"]:
            return False, None
        snapshot(db, row)
        db.execute(
            "UPDATE subtitles SET source=?,source_version=source_version+1,manual_translation=?,version=version+1 WHERE id=?",
            (text, row["manual_translation"], row["id"]),
        )
        change = {
            "id": row["id"],
            "before_source": row["source"],
            "before_version": row["version"],
            "after_version": row["version"] + 1,
            "before_source_version": row["source_version"],
        }
        return True, change


# 撤回最近一次 AI 润色
# 比对当前字幕版本与润色写回后的 after_version：
# - 若版本相等，说明润色后用户未做新编辑，安全还原到 before_source 与 before_source_version；
# - 若版本不相等，说明用户在润色之后做过人工修改，跳过撤回以防冲掉用户后来的编辑。
def revert_polish(task_id):
    with connect(write=True) as db:
        task_row = db.execute("SELECT params FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task_row:
            raise KeyError("任务不存在")
        params = json.loads(task_row["params"])
        last_polish = params.get("last_polish")
        if not last_polish or not last_polish.get("changes"):
            raise ValueError("没有可撤回的润色记录")
        reverted = 0
        skipped = 0
        for item in last_polish["changes"]:
            sub_id = item["id"]
            row = db.execute(
                "SELECT * FROM subtitles WHERE id=? AND task_id=?", (sub_id, task_id)
            ).fetchone()
            if not row:
                skipped += 1
                continue
            if row["version"] == item["after_version"]:
                snapshot(db, row)
                db.execute(
                    "UPDATE subtitles SET source=?,source_version=?,version=version+1 WHERE id=?",
                    (item["before_source"], item["before_source_version"], sub_id),
                )
                reverted += 1
            else:
                skipped += 1
        params.pop("last_polish", None)
        db.execute(
            "UPDATE tasks SET params=?,updated=? WHERE id=?",
            (json.dumps(params, ensure_ascii=False), time.time(), task_id),
        )
        return {"ok": True, "reverted": reverted, "skipped": skipped}
