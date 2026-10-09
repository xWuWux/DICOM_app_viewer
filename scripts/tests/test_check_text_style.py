"""scripts/check-text-style.py on synthetic repositories (a throwaway `git init` tree)."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "check-text-style.py"
spec = importlib.util.spec_from_file_location("check_text_style", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PICTO = "\u2705"  # white heavy check mark
POLISH = "B\u0142\u0105d \u0142adowania"
EM = "\u2014"
CJK = "\u9762\u677f"


def repo(tmp_path: Path, files: dict) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def run(tmp_path, files, capsys):
    root = repo(tmp_path, files)
    code = mod.main(["--root", str(root)])
    return code, capsys.readouterr().out


def test_clean_repository_passes(tmp_path, capsys):
    code, out = run(tmp_path, {"README.md": "Plain English text.\n", "a.py": "# a comment\nx = 1\n"}, capsys)
    assert code == 0 and "passed" in out


@pytest.mark.parametrize("name", ["README.md", "scripts/x.sh", "docs/history/old.md"])
def test_pictographs_are_rejected_everywhere(tmp_path, capsys, name):
    code, out = run(tmp_path, {name: f"status {PICTO} done\n"}, capsys)
    assert code == 1 and "PICTOGRAPH" in out and f"{name}:1:" in out


def test_cjk_is_rejected(tmp_path, capsys):
    code, out = run(tmp_path, {"docs/a.md": f"text {CJK} text\n"}, capsys)
    assert code == 1 and "CJK" in out


def test_em_dash_is_allowed_in_prose_but_not_in_code(tmp_path, capsys):
    code, out = run(tmp_path, {"docs/a.md": f"a {EM} b\n", "scripts/x.sh": f"# a {EM} b\n", "c.py": "x = 'a \u2013 b'\n"}, capsys)
    assert code == 1
    assert "scripts/x.sh:1: DASH" in out and "c.py:1: DASH" in out and "a.md" not in out


def test_polish_product_strings_in_code_may_keep_their_dash(tmp_path, capsys):
    files = {"docker/grading-api/app/db.py": f'("0", "Kategoria 0 {EM} badanie niekompletne"),\n"PLACEHOLDER {EM} nie jest to prawdziwy opis kliniczny. "\n'}
    code, out = run(tmp_path, files, capsys)
    assert code == 0, out


def test_polish_prose_outside_allowed_paths_is_rejected(tmp_path, capsys):
    code, out = run(tmp_path, {"docs/guide.md": "To jest opis w j\u0119zyku polskim.\n"}, capsys)
    assert code == 1 and "POLISH" in out


@pytest.mark.parametrize("name", ["docs/history/review.md", "docker/viewer/watermark.html", "docker/viewer/tests/test_x.py", "docker/grading-api/app/db.py"])
def test_polish_is_allowed_in_product_and_history_paths(tmp_path, capsys, name):
    code, out = run(tmp_path, {name: f"{POLISH} oraz wi\u0119cej tekstu\n"}, capsys)
    assert code == 0, out


def test_a_quoted_polish_ui_message_is_allowed_anywhere(tmp_path, capsys):
    code, out = run(tmp_path, {"README.md": f'The panel shows "{POLISH}" and `Wy\u015blij ocen\u0119` to the user.\n'}, capsys)
    assert code == 0, out


def test_a_quote_that_wraps_in_markdown_is_allowed(tmp_path, capsys):
    code, out = run(tmp_path, {"docs/g.md": f'It shows "{POLISH}:\nHTTP 401 Unauthorized" instead of a case.\n'}, capsys)
    assert code == 0, out


def test_a_wrapped_quote_in_a_shell_comment_does_not_poison_later_lines(tmp_path, capsys):
    text = f'# the panel\'s own "{POLISH} ...\n# next" line\n# {POLISH} in plain comment text\n'
    code, out = run(tmp_path, {"scripts/x.sh": text}, capsys)
    assert code == 1 and "x.sh:3: POLISH" in out and "x.sh:1:" not in out


def test_quote_state_resets_at_a_blank_line_in_markdown(tmp_path, capsys):
    code, out = run(tmp_path, {"docs/g.md": 'An unbalanced " quote.\n\nPolish prose: B\u0142\u0105d tutaj.\n'}, capsys)
    assert code == 1 and "g.md:3: POLISH" in out


def test_binary_files_are_skipped(tmp_path, capsys):
    root = repo(tmp_path, {"a.md": "ok\n"})
    (root / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + "B\u0142\u0105d".encode())
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    assert mod.main(["--root", str(root)]) == 0
