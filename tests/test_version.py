import json
from pathlib import Path

from backend import __version__
from backend.main import app


def test_release_versions_match():
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / 'frontend/package.json').read_text(encoding='utf-8'))
    lock = json.loads((root / 'frontend/package-lock.json').read_text(encoding='utf-8'))
    assert __version__ == '1.0.1'
    assert app.version == package['version'] == lock['version'] == lock['packages']['']['version'] == __version__
