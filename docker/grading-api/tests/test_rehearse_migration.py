"""The migration rehearsal tool (scripts/rehearse-migration.py) must itself be trustworthy:
it has to PASS on a correct migration and FAIL on the exact bug class it exists to catch
(issue #96 review: renaming `cases` re-pointed submissions' FOREIGN KEY at a dropped table).
"""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "rehearse-migration.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=120)


def test_rehearsal_passes_on_synthetic_legacy_db():
    proc = _run("--synthetic")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "REHEARSAL PASSED" in proc.stdout
    assert "FAIL" not in proc.stdout


def test_rehearsal_never_modifies_the_source_database(tmp_path):
    import hashlib
    import sqlite3

    src = tmp_path / "src.db"
    sys.path.insert(0, str(SCRIPT.parent))
    import importlib.util

    spec = importlib.util.spec_from_file_location("rehearse", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build_synthetic(str(src))
    sqlite3.connect(src).close()
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    proc = _run("--db", str(src))
    assert proc.returncode == 0, proc.stdout
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("*.bak"))  # backups only ever land next to the throwaway copy
