"""
Root conftest.py, not tests/conftest.py -- pytest always inserts the
directory containing a discovered conftest.py into sys.path, which is what
makes `from app import db` / `from app.main import app` importable below
without any manual sys.path hacking (this file lives next to app/, which
is a real package -- see app/__init__.py).
"""
import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh, isolated SQLite file per test.

    Monkeypatches db.DB_PATH directly rather than the GRADING_DB_PATH env
    var it's read from in production: get_connection() reads the module
    global at call time (not baked into a closure at import time), so
    reassigning it here is enough -- no import-order or module-reload
    gymnastics needed, and it means every test starts from a genuinely
    empty database (fresh tables, freshly re-seeded cases), never sharing
    state with another test or a real deployment's data.
    """
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "grading-test.db"))
    # TestClient as a context manager runs FastAPI's startup event (which
    # calls db.init_db()) against the now-patched DB_PATH, then its
    # shutdown event on exit -- the standard FastAPI testing pattern,
    # not a hand-rolled substitute for it.
    with TestClient(app) as test_client:
        yield test_client
