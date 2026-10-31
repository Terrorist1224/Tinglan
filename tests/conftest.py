import os
from pathlib import Path

os.environ["TINGLAN_DATA"] = str(
    Path(__file__).resolve().parents[1] / ".tmp" / "test-data"
)

import pytest
from fastapi.testclient import TestClient
from backend import db
from backend.main import app


@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost") as c:
        with db.connect() as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM settings")
        yield c


@pytest.fixture
def task(client):
    import json, time
    from backend.config import DATA

    task_id = db.ident()
    folder = DATA / task_id
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"fixture")
    now = time.time()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO tasks(id,name,media,probe,params,created,updated) VALUES(?,?,?,?,?,?,?)",
            (
                task_id,
                "测试视频.mp4",
                "source.mp4",
                json.dumps(
                    {
                        "duration": 60,
                        "audio": [{"index": 0, "default": True}],
                        "subtitles": [],
                        "video": None,
                    }
                ),
                json.dumps(
                    {
                        "audio_track": 0,
                        "device": "cpu",
                        "language": "en",
                    }
                ),
                now,
                now,
            ),
        )
        for i, text in enumerate(
            ["First sentence.", "Second sentence.", "Third sentence."]
        ):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"s{i}",
                    task_id,
                    i * 3000,
                    i * 3000 + 2500,
                    text,
                    text,
                    "en",
                    "fixture",
                ),
            )
    return task_id
