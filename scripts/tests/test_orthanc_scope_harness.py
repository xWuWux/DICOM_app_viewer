"""Exercise the real shell harness with fake transport, including negative controls.

These tests prove the gate fails correctly; the compose CI job proves the real
nginx/auth_request/Orthanc integration. No network or Docker daemon needed here.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "test-orthanc-proxy-scope.sh"


@pytest.fixture
def harness(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    (tmp_path / "sample-data").mkdir()
    # Transport is fake: no new DICOM corpus is generated.
    for name in ("CT_small", "MR_small"):
        (tmp_path / "sample-data" / f"{name}.dcm").touch()
    bins = tmp_path / "bin"
    bins.mkdir()
    docker = bins / "docker"
    docker.write_text("""#!/usr/bin/env python3
import os, sys
with open(os.environ["CALLS"], "a") as f:
    f.write("docker " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["ps", "-a"]:
    if os.environ.get("INVENTORY_FAIL"): sys.exit(1)
    print(os.environ.get("CONTAINERS", ""))
if sys.argv[1:3] == ["volume", "ls"]:
    if os.environ.get("VOLUME_FAIL"): sys.exit(1)
    print(os.environ.get("VOLUMES", ""))
if "down" in sys.argv and os.environ.get("CLEANUP_FAIL"): sys.exit(1)
""")
    (bins / "git").write_text("#!/usr/bin/env bash\nexit 0\n")
    (bins / "curl").write_text("""#!/usr/bin/env python3
import hashlib, json, os, sys
from urllib.parse import urlsplit
a = sys.argv[1:]
url = next(x for x in a if x.startswith("http://"))
p = urlsplit(url).path
method = a[a.index("-X")+1] if "-X" in a else "GET"
ka = hashlib.sha256(b"fake-a").hexdigest()
kb = hashlib.sha256(b"fake-b").hexdigest()
q = p.replace(ka, "A").replace(kb, "B")
with open(os.environ["CALLS"], "a") as f:
    f.write("curl " + method + " " + q + "\\n")
if "-w" in a:
    if os.environ.get("TRANSPORT_FAIL"): sys.exit(7)
    code = 200
    if p.startswith("/s/"):
        if method != "GET" or (q == "/s/A/dicom-web/studies" and not urlsplit(url).query) or q.startswith("/s/A/dicom-web/studies/2.2"):
            code = 403
        if os.path.exists(os.environ["REVOKED"]) and q.startswith("/s/A/"):
            code = 401
    elif method == "DELETE" or (method == "POST" and p == "/instances"):
        code = 403
    if os.environ.get("MUTATE_PATH") == q:
        code = int(os.environ["MUTATE_CODE"])
    print(str(code) + " 10", end="")
elif p == "/api/session":
    data = json.loads(a[a.index("-d")+1])
    print(json.dumps({"token": "fake-a" if data["student_id"].endswith("A") else "fake-b"}))
elif p == "/api/case":
    print(json.dumps({"case_id": 1, "orthanc_study_uid": "1.1" if "X-Grading-Token: fake-a" in a else "2.2"}))
elif p == "/api/session/revoke":
    open(os.environ["REVOKED"], "w").close()
elif p == "/studies": print('["study-id"]')
elif p == "/studies/study-id": print('{"Series":["series-id"]}')
elif p == "/series/series-id": print('{"Instances":["instance-id"]}')
else: print('{}')
""")
    for path in bins.iterdir():
        path.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bins}:{os.environ['PATH']}",
        CALLS=str(tmp_path / "calls"),
        REVOKED=str(tmp_path / "revoked"),
    )

    def run(*args, **extra):
        result = subprocess.run(
            ["bash", str(tmp_path / "scripts" / SCRIPT.name), *args],
            env=dict(env, **extra),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        calls = Path(env["CALLS"])
        return result, calls.read_text() if calls.exists() else ""

    return run, tmp_path


def test_ci_runs_all_tasks_without_fetching_samples(harness):
    run, root = harness
    result, calls = run("--ci-scoped")
    assert result.returncode == 0, result.stdout + result.stderr
    for task in ("RT-T-01", "RT-T-02", "RT-T-03", "RT-T-04", "REVOKE"):
        assert task in result.stdout
    assert "KNOWN-EXPOSURE(#195)" in result.stdout
    assert "/instances/instance-id/file" in calls
    assert "docker compose -p bluet-scope -f docker-compose.yml down -v" in calls
    assert not (root / ".env").exists()
    assert "fake-a" not in result.stdout + result.stderr
    assert "/s/A/" not in result.stdout
    assert "1.1" not in result.stdout


@pytest.mark.parametrize(
    "path,code",
    [
        ("/s/A/dicom-web/studies/2.2/metadata", "200"),
        ("/s/A/dicom-web/studies/2.2/metadata", "206"),
        ("/s/A/dicom-web/studies/2.2/metadata", "302"),
        ("/s/A/dicom-web/studies/2.2/metadata", "404"),
        ("/s/A/dicom-web/studies/2.2/metadata", "429"),
        ("/s/A/dicom-web/studies/2.2/metadata", "500"),
        ("/s/A/dicom-web/studies/1.1/metadata", "403"),
        ("/s/A/dicom-web/studies/1.1/metadata", "200"),  # ignored revocation
        ("/s/B/dicom-web/studies/2.2/metadata", "503"),
        ("/studies/study-id", "200"),  # a broken legacy DELETE guard
        ("/instances", "200"),  # a broken upload guard
        ("/instances/instance-id/file", "500"),  # legacy outage is not a denial
    ],
)
def test_ci_rejects_broken_boundaries_and_outages(harness, path, code):
    run, _ = harness
    result, calls = run("--ci-scoped", MUTATE_PATH=path, MUTATE_CODE=code)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "docker compose -p bluet-scope -f docker-compose.yml down -v" in calls


def test_full_isolation_gate_stays_red_on_known_legacy_exposure(harness):
    run, _ = harness
    result, _ = run("--assert-scoped")
    assert result.returncode == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"INVENTORY_FAIL": "1"},
        {"VOLUME_FAIL": "1"},
        {"CONTAINERS": "ipcmc-orthanc"},
        {"VOLUMES": "old-data"},
    ],
)
def test_refusal_never_tears_down_or_writes_env(harness, extra):
    run, root = harness
    result, calls = run("--ci-scoped", **extra)
    assert result.returncode == 2
    assert "down" not in calls and " up " not in calls
    assert not (root / ".env").exists()


def test_existing_env_is_untouched(harness):
    run, root = harness
    (root / ".env").write_text("do not change\n")
    result, calls = run("--ci-scoped")
    assert result.returncode == 2
    assert (root / ".env").read_text() == "do not change\n"
    assert "down" not in calls and " up " not in calls


@pytest.mark.parametrize("extra", [{"TRANSPORT_FAIL": "1"}, {"CLEANUP_FAIL": "1"}])
def test_transport_and_cleanup_failure_cannot_pass(harness, extra):
    run, root = harness
    result, calls = run("--ci-scoped", **extra)
    assert result.returncode != 0
    assert "docker compose -p bluet-scope -f docker-compose.yml down -v" in calls
    assert not (root / ".env").exists()


def test_closed_legacy_listing_does_not_skip_download_probes(harness):
    run, _ = harness
    result, calls = run("--ci-scoped", MUTATE_PATH="/studies", MUTATE_CODE="403")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "/studies/study-id/archive" in calls
    assert "/instances/instance-id/file" in calls
