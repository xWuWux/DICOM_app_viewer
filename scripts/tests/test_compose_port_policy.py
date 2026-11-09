"""Negative controls for the exact resolved-Compose publication policy (#103)."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "port_policy", ROOT / "scripts/check-compose-ports.py"
)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def model(files):
    allowed = dict(policy.CORE_PORTS)
    if files[0] == "docker-compose.yml":
        allowed.update(policy.MONITORING_PORTS)
    if len(files) == 2:
        allowed["guacamole"] = {(8090, 8080)}
    services = {
        name: {
            "ports": [
                {
                    "host_ip": "127.0.0.1",
                    "published": str(host),
                    "target": target,
                    "protocol": "tcp",
                }
                for host, target in ports
            ]
        }
        for name, ports in allowed.items()
    }
    services["grading-api"] = {}
    if len(files) == 2:
        services["guacamole"]["depends_on"] = {
            "guacamole-admin-init": {"condition": "service_completed_successfully"}
        }
        services["guacamole-admin-init"] = {"restart": "no"}
    return {"services": services}


@pytest.mark.parametrize("files", policy.COMBINATIONS)
def test_expected_contract(files):
    assert policy.check_model(model(files), files) == []


@pytest.mark.parametrize(
    "host_ip", [None, "", "0.0.0.0", "::", "::1", "192.0.2.2", "localhost"]
)
def test_wildcard_ipv6_and_nonliteral_hosts_fail(host_ip):
    files = policy.COMBINATIONS[0]
    data = model(files)
    data["services"]["viewer"]["ports"][0]["host_ip"] = host_ip
    assert policy.check_model(data, files)


@pytest.mark.parametrize(
    "mutation",
    [
        "host",
        "container",
        "extra",
        "missing",
        "dynamic",
        "udp",
        "duplicate",
        "missing_service",
        "auth_bypass",
    ],
)
def test_policy_mutations_fail(mutation):
    files = policy.COMBINATIONS[2]
    data = model(files)
    services = data["services"]
    ports = services["viewer"]["ports"]
    if mutation in ("host", "container"):
        services["grading-api"]["network_mode"] = (
            "host" if mutation == "host" else "container:viewer"
        )
    elif mutation == "extra":
        services["grading-api"]["ports"] = [
            {"host_ip": "127.0.0.1", "published": "8000", "target": 8000}
        ]
    elif mutation == "missing":
        ports.clear()
    elif mutation == "dynamic":
        ports[0].pop("published")
    elif mutation == "udp":
        ports[0]["protocol"] = "udp"
    elif mutation == "duplicate":
        ports.append(copy.deepcopy(ports[0]))
    elif mutation == "missing_service":
        services.pop("orthanc")
    elif mutation == "auth_bypass":
        services["guacamole"]["depends_on"].clear()
    assert policy.check_model(data, files)


def test_empty_model_cannot_pass_vacuously():
    assert policy.check_model({}, policy.COMBINATIONS[0])


def test_render_is_hermetic_and_bounded(monkeypatch):
    monkeypatch.setenv("COMPOSE_FILE", "untrusted.yml")
    monkeypatch.setenv("COMPOSE_PROFILES", "unexpected")
    monkeypatch.setenv("GUACAMOLE_ADMIN_PASSWORD", "must-not-enter-render")
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps(model(policy.COMBINATIONS[0])), ""
        )

    monkeypatch.setattr(policy.subprocess, "run", run)
    policy.render(policy.COMBINATIONS[0], "::")
    cmd, kwargs = calls[0]
    assert cmd[cmd.index("--env-file") + 1] == policy.os.devnull
    assert cmd[cmd.index("--profile") + 1] == "*"
    assert "COMPOSE_FILE" not in kwargs["env"]
    assert "COMPOSE_PROFILES" not in kwargs["env"]
    assert "must-not-enter-render" not in str(kwargs)
    assert kwargs["timeout"] == 30


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError(), ValueError(), subprocess.TimeoutExpired("docker", 30)],
)
def test_missing_tool_or_invalid_output_fails_required_gate(monkeypatch, error):
    def broken(*args):
        raise error

    monkeypatch.setattr(policy, "render", broken)
    assert policy.main() == 1


def test_cli_checks_all_combinations_and_overrides(monkeypatch):
    calls = []

    def render(files, bind):
        calls.append((files, bind))
        return model(files)

    monkeypatch.setattr(policy, "render", render)
    assert policy.main() == 0
    assert len(calls) == 16
    assert {bind for _, bind in calls} == {"", "0.0.0.0", "::", "192.0.2.10"}
