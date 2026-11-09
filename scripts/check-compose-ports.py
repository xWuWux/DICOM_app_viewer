#!/usr/bin/env python3
"""Required, daemon-free check of resolved Compose host mappings (#103).

Run from any directory. No developer .env, COMPOSE_* or BIND_ADDR is inherited.
Docker/Compose absence, rendering errors and unknown publications fail closed.
This proves configuration, not firewall behavior or container/user isolation.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_PORTS = {"orthanc": {(8042, 8042)}, "viewer": {(8080, 8080), (8043, 8043)}}
MONITORING_PORTS = {
    "otel-collector": {(4317, 4317), (4318, 4318)},
    "tempo": {(3200, 3200)},
    "prometheus": {(9091, 9090)},
    "alertmanager": {(9093, 9093)},
    "grafana": {(3000, 3000)},
}
COMBINATIONS = (
    ("docker-compose.yml",),
    ("docker-compose.remote-host.yml",),
    ("docker-compose.yml", "docker-compose.guacamole.yml"),
    ("docker-compose.remote-host.yml", "docker-compose.guacamole.yml"),
)


def check_model(model: dict[str, Any], files: tuple[str, ...]) -> list[str]:
    """Return policy violations for resolved config and its explicit file tuple."""
    allowed = dict(CORE_PORTS)
    if files[0] == "docker-compose.yml":
        allowed.update(MONITORING_PORTS)
    if "docker-compose.guacamole.yml" in files:
        allowed["guacamole"] = {(8090, 8080)}
    errors: list[str] = []
    services = model.get("services", {})
    if not isinstance(services, dict) or not services:
        return ["missing services"]
    for name, service in services.items():
        if service.get("network_mode") not in (None, "none"):
            errors.append(f"{name}: network_mode bypasses managed bridge policy")
        actual: set[tuple[int, int]] = set()
        for port in service.get("ports", []):
            if not isinstance(port, dict):
                errors.append(f"{name}: ports must be resolved objects")
                continue
            if port.get("host_ip") != "127.0.0.1":
                errors.append(f"{name}: non-loopback host mapping")
            if port.get("protocol", "tcp") != "tcp":
                errors.append(f"{name}: unexpected protocol")
            try:
                pair = (int(port["published"]), int(port["target"]))
            except (KeyError, TypeError, ValueError):
                errors.append(f"{name}: invalid or dynamic port mapping")
                continue
            if pair in actual:
                errors.append(f"{name}: duplicate mapping")
            actual.add(pair)
        if actual != allowed.get(name, set()):
            errors.append(f"{name}: unexpected or missing host port")
    for name in allowed.keys() - services.keys():
        errors.append(f"{name}: required service missing")
    if "docker-compose.guacamole.yml" in files:
        init = services.get("guacamole-admin-init", {})
        dependency = (
            services.get("guacamole", {})
            .get("depends_on", {})
            .get("guacamole-admin-init", {})
        )
        if dependency.get("condition") != "service_completed_successfully":
            errors.append("guacamole: administrator migration must gate startup")
        if init.get("restart") != "no":
            errors.append("guacamole-admin-init: must be a one-shot")
    return errors


def render(files: tuple[str, ...], bind_addr: str) -> dict[str, Any]:
    """Resolve config with sentinel secrets and an obsolete bind override probe."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "ORTHANC_PASSWORD": "compose-test-orthanc-not-a-secret",
        "GRADING_COORDINATOR_KEY": "compose-test-coordinator-not-a-secret",
        "GUACAMOLE_DB_PASSWORD": "compose-test-database-not-a-secret",
        "GUACAMOLE_ADMIN_PASSWORD": "compose-test-administrator-not-a-secret",
        "BIND_ADDR": bind_addr,
    }
    cmd = [
        "docker",
        "compose",
        "--project-name",
        "port-policy",
        "--env-file",
        os.devnull,
        "--profile",
        "*",
    ]
    for file in files:
        cmd += ["-f", str(ROOT / file)]
    result = subprocess.run(
        cmd + ["config", "--format", "json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError("Compose rendering failed (no configuration output logged)")
    return json.loads(result.stdout)


def main() -> int:
    try:
        for files in COMBINATIONS:
            # Empty = defaults; hostile values must not change any mapping.
            for bind in ("", "0.0.0.0", "::", "192.0.2.10"):
                errors = check_model(render(files, bind), files)
                if errors:
                    print(
                        f"FAIL {' + '.join(files)}: {'; '.join(errors)}",
                        file=sys.stderr,
                    )
                    return 1
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print(
            "FAIL: Docker Compose unavailable, invalid output or rendering failed",
            file=sys.stderr,
        )
        return 1
    print("PASS: all Compose combinations/profiles enforce fixed loopback publications")
    return 0


if __name__ == "__main__":
    sys.exit(main())
