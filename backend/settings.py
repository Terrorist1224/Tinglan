"""本地设置与密钥持久化；独立于 HTTP 入口，供路由和 Engine 共用。"""
import json
from urllib.parse import urlsplit
from . import db
from .config import DATA
from .schemas import Settings

session_key = ""
# 密钥以本地明文文件保存；公开设置只返回 has_key，备份 DATA 时也会包含此文件。
KEY_FILE = DATA / "api.key"


def get_api_key():
    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return session_key or ""


def load_settings():
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE id=1").fetchone()
    data = json.loads(row["value"]) if row else {}
    return {
        "base_url": data.get("base_url", ""),
        "model": data.get("model", ""),
        "target_language": data.get("target_language", "简体中文"),
        "topic": data.get("topic", ""),
        "glossary": data.get("glossary", ""),
        "default_diarize": data.get("default_diarize", True),
        "default_auto_polish_translate": data.get("default_auto_polish_translate", False),
    }


def public_settings():
    return {
        **load_settings(),
        "has_key": bool(get_api_key()),
        "session_only": False,
    }


def update_settings(body: Settings):
    global session_key
    url = urlsplit(body.base_url)
    if url.username or url.password:
        raise ValueError("基础地址不可包含用户名或密码")
    if (
        url.scheme not in ("http", "https")
        or not url.netloc
        or any(
            url.netloc.startswith(h)
            for h in (
                "10.",
                "192.168.",
                "172.16.",
                "172.17.",
                "172.18.",
                "172.19.",
                "172.2",
                "172.3",
            )
        )
    ):
        raise ValueError("基础地址必须为有效公网或本机 HTTP(S) 地址")
    clean = {
        "base_url": body.base_url.rstrip("/"),
        "model": body.model.strip(),
        "target_language": body.target_language.strip(),
        "topic": body.topic.strip(),
        "glossary": body.glossary.strip(),
        "default_diarize": body.default_diarize,
        "default_auto_polish_translate": body.default_auto_polish_translate,
    }
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO settings(id,value) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
            (json.dumps(clean, ensure_ascii=False),),
        )
    notice = "设置已保存到本地"
    if body.clear_key:
        session_key = ""
        KEY_FILE.unlink(missing_ok=True)
        notice = "密钥已清除"
    elif body.key:
        session_key = body.key.strip()
        KEY_FILE.write_text(session_key, encoding="utf-8")
        notice = "设置与 API 密钥已保存到本地"
    return {**public_settings(), "notice": notice}
