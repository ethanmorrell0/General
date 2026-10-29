import os
import sys
import tempfile
from pathlib import Path

import pytest

# Isolate the DB and force demo mode before the app package is imported.
_TMP = tempfile.mkdtemp(prefix="edgefinder-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["DEMO_MODE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from app import config, db
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.reset_connection()
    yield
    db.reset_connection()
