from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clue_web import create_app


@pytest.fixture
def app(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "CLUE_SECRET_KEY": "test-secret-key",
            "DB_PATH": str(tmp_path / "clue.db"),
        }
    )
    yield app


@pytest.fixture
def client(app):
    return app.test_client()
