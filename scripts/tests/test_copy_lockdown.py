"""
Regression tests for every copying mechanism this project claims to have
locked down (README.md's "DLP settings" section, issues #6/#7): native
Weasis export/import/send/Q-R, and the KasmVNC rich/binary clipboard-DLP
gap (screenshots/images copied to the clipboard from inside a session).

Deliberately scoped to what's actually *code in this repo* -- runs with no
Docker/real infrastructure, so it's cheap enough to run on every PR (see
scripts/test-copy-lockdown.sh, wired into .github/workflows/ci.yml).

Explicitly NOT covered here, and can't be from this repo's CI: the Kasm
admin-console Group-level DLP toggles (`allow_kasm_clipboard_down/up/
seamless`, `allow_kasm_downloads`, `allow_kasm_uploads`,
`allow_kasm_printing`, `allow_kasm_sharing`, etc.) -- those are configured
by hand in a real, running Kasm Workspaces instance's own Postgres-backed
admin UI, not represented in any file here at all. README.md's DLP section
documents how those were verified (queried Kasm's own Postgres directly,
then confirmed KASM_SVC_DOWNLOADS/UPLOADS/PRINTER are 0 inside a real
session) -- that verification is a one-time, manual act against a live
instance, not something a GitHub Actions runner (which has no Kasm install
at all) can re-check on every PR. If that ever needs to be re-verified,
it has to be run again by hand against the real instance; nothing here
claims otherwise.
"""
import json
import os
import subprocess
import sys

import pytest
import yaml

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_PATCH_SCRIPT = os.path.join(
    _REPO_ROOT, "docker", "kasm-workspace-weasis", "patch-weasis-config.py"
)
_FIXTURE_BASE_JSON = os.path.join(os.path.dirname(__file__), "fixtures", "weasis_base.json")

# Independently declared here, not imported from patch-weasis-config.py's
# own FALSE_KEYS -- the point of this test is to catch someone silently
# shrinking that set, which importing it would make invisible.
EXPECTED_DISABLED_KEYS = {
    "weasis.show.disclaimer",
    "weasis.export.dicom",
    "weasis.export.dicom.send",
    "weasis.import.dicom",
    "weasis.import.images",
    "weasis.import.dicom.qr",
}
EXPECTED_BLANKED_BUNDLE_KEY = "felix.auto.start.110"

# Present in the fixture but deliberately NOT in EXPECTED_DISABLED_KEYS --
# the patcher must leave these alone (a regression check against the
# patcher over-reaching into unrelated preferences).
UNTOUCHED_CONTROL_KEYS = {
    "weasis.export.image": "true",
    "weasis.language": "en",
}


def _run_patcher(tmp_path):
    target = tmp_path / "base.json"
    target.write_text(open(_FIXTURE_BASE_JSON).read())
    subprocess.run(
        [sys.executable, _PATCH_SCRIPT],
        env={**os.environ, "WEASIS_BASE_JSON_PATH": str(target)},
        check=True,
    )
    with open(target) as f:
        return json.load(f)


def test_patch_weasis_config_source_declares_the_full_expected_key_set(tmp_path, monkeypatch):
    """Catches a silent shrink/typo in patch-weasis-config.py's own
    FALSE_KEYS set, independent of what running it actually produces.

    The script has no `if __name__ == "__main__":` guard (it's a
    build-time, run-once patch) -- exec'ing its source to inspect FALSE_KEYS
    also runs its file read/write, so this points WEASIS_BASE_JSON_PATH at
    a throwaway copy of the fixture rather than the real container path.
    """
    target = tmp_path / "base.json"
    target.write_text(open(_FIXTURE_BASE_JSON).read())
    monkeypatch.setenv("WEASIS_BASE_JSON_PATH", str(target))

    with open(_PATCH_SCRIPT) as f:
        source = f.read()
    namespace = {}
    exec(compile(source, _PATCH_SCRIPT, "exec"), namespace)  # noqa: S102 (test-only, own repo file)
    assert namespace["FALSE_KEYS"] == EXPECTED_DISABLED_KEYS


def test_patch_weasis_config_disables_native_export_import_send_qr(tmp_path):
    patched = _run_patcher(tmp_path)
    by_code = {pref["code"]: pref["value"] for pref in patched["weasisPreferences"]}

    for key in EXPECTED_DISABLED_KEYS:
        assert by_code[key] == "false", f"{key} was not disabled by the patcher"

    assert by_code[EXPECTED_BLANKED_BUNDLE_KEY] == "", (
        "felix.auto.start.110 (DICOM send / Q-R / ISO-writer bundles) "
        "was not blanked out -- these bundles would still load at runtime"
    )


def test_patch_weasis_config_does_not_touch_unrelated_preferences(tmp_path):
    patched = _run_patcher(tmp_path)
    by_code = {pref["code"]: pref["value"] for pref in patched["weasisPreferences"]}

    for key, expected_value in UNTOUCHED_CONTROL_KEYS.items():
        assert by_code[key] == expected_value, f"patcher unexpectedly touched {key}"


@pytest.mark.parametrize(
    "kasmvnc_yaml_path",
    [
        os.path.join(_REPO_ROOT, "docker", "kasm-workspace", "kasmvnc.yaml"),
        os.path.join(_REPO_ROOT, "docker", "kasm-workspace-weasis", "kasmvnc.yaml"),
    ],
)
def test_kasmvnc_yaml_blocks_rich_binary_clipboard_mimetypes(kasmvnc_yaml_path):
    """The gap this closes (README.md's DLP section): Kasm's admin-UI
    clipboard toggles only govern plain-TEXT sync. KasmVNC's own,
    independent data_loss_prevention.clipboard.allow_mimetypes key is what
    stops an *image* (e.g. Weasis's "Export -> Clipboard", or any
    screenshot copied to the clipboard) from reaching the real local
    clipboard. An empty list is what makes that block unconditional --
    anything non-empty reopens exactly that path."""
    with open(kasmvnc_yaml_path) as f:
        config = yaml.safe_load(f)

    allow_mimetypes = config["data_loss_prevention"]["clipboard"]["allow_mimetypes"]
    assert allow_mimetypes == [], (
        f"{kasmvnc_yaml_path}: allow_mimetypes is {allow_mimetypes!r}, "
        "expected an empty list -- a non-empty list lets rich/binary "
        "clipboard content (e.g. a copied screenshot) reach the real "
        "local clipboard outside the session"
    )
