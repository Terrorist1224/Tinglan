import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from datetime import datetime

import pytest

from backend import db
from backend.main import app
from scripts import launch

ROOT = Path(__file__).resolve().parents[1]


def test_http_contract_is_unchanged():
    expected = json.loads((ROOT / "tests/fixtures/api-contract.json").read_text(encoding="utf-8"))
    assert app.openapi() == expected


def test_old_database_columns_upgrade_without_losing_rows(task):
    before_task = db.task(task)
    before_rows = db.subtitles(task)
    with db.connect() as conn:
        conn.execute("ALTER TABLE subtitles DROP COLUMN words")
        conn.execute("ALTER TABLE subtitles DROP COLUMN translation_provider")
    db.init()
    db.init()
    assert db.task(task) == before_task
    assert db.subtitles(task) == before_rows


def test_database_initialization_does_not_hide_sql_errors(monkeypatch):
    import contextlib

    class BrokenConnection:
        def execute(self, statement):
            if statement.startswith("PRAGMA table_info"):
                return []
            if statement.startswith("ALTER TABLE"):
                raise sqlite3.OperationalError("disk is full")
            return self

        def executescript(self, statement):
            pass

    @contextlib.contextmanager
    def connect():
        yield BrokenConnection()

    monkeypatch.setattr(db, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        db.init()


def test_settings_and_engine_do_not_import_the_http_app(tmp_path):
    data = tmp_path / "data"
    script = """
import sys
from pathlib import Path
from backend import config
assert not config.DATA.exists()
assert config.PYTHON == Path(sys.executable).resolve()
from backend import db
db.init()
from backend.engine import Engine
settings, key = Engine().resolve_settings()
assert settings['default_diarize'] is True
assert 'backend.main' not in sys.modules
assert not key
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        env={**os.environ, "TINGLAN_DATA": str(data)},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_diagnostics_directories_are_unique_when_clock_is_frozen(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 1, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(launch, "ROOT", tmp_path)
    monkeypatch.setattr(launch, "datetime", FrozenDateTime)
    monkeypatch.setattr(launch.faulthandler, "enable", lambda **kw: None)
    monkeypatch.setattr(sys, "excepthook", lambda *args: None)
    monkeypatch.setattr(launch.threading, "excepthook", lambda *args: None)
    first = launch.init_diagnostics()
    second = launch.init_diagnostics()
    try:
        assert first is not None and second is not None
        assert first.name != second.name
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
