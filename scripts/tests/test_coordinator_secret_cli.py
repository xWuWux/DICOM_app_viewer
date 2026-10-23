"""File mode must fail before session mutations, including cleanup tools."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from secret_config import coordinator_key


def load(filename):
    spec = importlib.util.spec_from_file_location(
        filename.replace("-", "_"), SCRIPTS / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "filename,args",
    [
        (
            "teardown-guacamole-session.py",
            ["--student-id", "TEST", "--session-id", "1"],
        ),
        ("reap-guacamole-sessions.py", []),
    ],
)
def test_bad_file_stops_cleanup_before_effects(
    filename, args, monkeypatch, tmp_path, capsys
):
    module = load(filename)
    monkeypatch.setattr(sys, "argv", [filename, *args])
    monkeypatch.delenv("GRADING_COORDINATOR_KEY", raising=False)
    monkeypatch.setenv("GRADING_COORDINATOR_KEY_FILE", str(tmp_path / "missing"))
    if filename.startswith("teardown"):
        monkeypatch.setattr(
            module, "teardown_session", lambda *a, **k: pytest.fail("cleanup ran")
        )
    else:
        monkeypatch.setattr(
            module,
            "_load_state",
            lambda *a: pytest.fail("state read before validation"),
        )
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    assert str(tmp_path) not in capsys.readouterr().err


def test_teardown_passes_file_key_to_revocation(monkeypatch, tmp_path, capsys):
    module = load("teardown-guacamole-session.py")
    key = "coordinator-file-sentinel-" + "x" * 32
    path = tmp_path / "key"
    path.write_text(key + "\n")
    path.chmod(0o600)
    monkeypatch.delenv("GRADING_COORDINATOR_KEY", raising=False)
    monkeypatch.setenv("GRADING_COORDINATOR_KEY_FILE", str(path))
    monkeypatch.setattr(
        sys, "argv", ["teardown", "--student-id", "TEST", "--session-id", "1"]
    )
    calls = []

    def teardown(*args, **kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(module, "teardown_session", teardown)
    assert module.main() == 0
    assert calls[0]["coordinator_key"] == key
    assert path.read_text() == key + "\n"
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err


def test_optional_absent_and_explicit_short_file(tmp_path):
    assert coordinator_key({}, required=False) == ""
    path = tmp_path / "key"
    path.write_text("short")
    path.chmod(0o600)
    with pytest.raises(SystemExit) as exc:
        coordinator_key({"GRADING_COORDINATOR_KEY_FILE": str(path)}, required=False)
    assert exc.value.code == 2
