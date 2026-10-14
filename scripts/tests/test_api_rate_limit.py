"""API_RATE_LIMIT wiring (issue #226): pinned by inspection, no Docker needed.

The /api/ edge limit must stay a PRODUCTION constant (10r/s) everywhere it can
be reached accidentally, while the visual-regression harness may raise it on
its own throwaway stack only. These assertions fail if any of the four links
in that chain is edited away.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "docker" / "viewer" / "default.conf.template"
ENVSH = ROOT / "docker" / "viewer" / "15-compute-orthanc-auth.envsh"
COMPOSES = [ROOT / "docker-compose.yml", ROOT / "docker-compose.remote-host.yml"]
VR = ROOT / "scripts" / "test-visual-regression.sh"


def test_template_uses_the_variable_not_a_hardcoded_rate():
    line = next(l for l in TEMPLATE.read_text().splitlines() if l.startswith("limit_req_zone"))
    assert "rate=${API_RATE_LIMIT};" in line
    assert "rate=10r/s" not in line


def test_envsh_defaults_the_variable_so_it_can_never_be_unset_in_the_container():
    body = ENVSH.read_text()
    assert ': "${API_RATE_LIMIT:=10r/s}"' in body
    assert "export API_RATE_LIMIT" in body


def test_both_deployment_composes_pass_it_through_with_the_production_default():
    for compose in COMPOSES:
        text = compose.read_text()
        assert "- API_RATE_LIMIT=${API_RATE_LIMIT:-10r/s}" in text, compose.name
        # exactly ONE assignment line per file: a second, different default
        # could silently weaken or invalidate the deployment limit;
        assigns = [l.strip() for l in text.splitlines() if "API_RATE_LIMIT=" in l]
        assert assigns == ["- API_RATE_LIMIT=${API_RATE_LIMIT:-10r/s}"], (compose.name, assigns)


def test_envsh_refuses_a_malformed_rate_before_nginx_would_fail():
    # nginx would refuse to start on a bad rate anyway (an opaque config
    # error in the logs); the sourced entrypoint hook must name the
    # variable and the accepted shape instead. bash: the nginx image runs
    # docker-entrypoint.d/*.envsh files in bash, unlike plain .sh parts.
    def source_envsh(rate):
        return subprocess.run(
            ["bash", "-c", f'source "{ENVSH}"'],
            env={**os.environ, "ORTHANC_PASSWORD": "test", "API_RATE_LIMIT": rate},
            capture_output=True,
            text=True,
        )

    bad = source_envsh("banana")
    assert bad.returncode != 0
    assert "API_RATE_LIMIT" in bad.stderr and "r/s" in bad.stderr
    # the accepted shapes keep passing silently
    for good in ("10r/s", "50r/m"):
        ok = source_envsh(good)
        assert ok.returncode == 0, (good, ok.stderr)


def test_visual_regression_harness_raises_it_for_its_own_stack_only():
    body = VR.read_text()
    assert 'export API_RATE_LIMIT="${API_RATE_LIMIT:-50r/s}"' in body
    # and the run reports the MEASURED peak load instead of an estimate (#226)
    assert "peak /api requests in one second" in body
