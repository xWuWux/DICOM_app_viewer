"""Log rotation is pinned in compose, not left to the daemon default (issue #113).

docs/observability.md makes stdout JSON the primary telemetry channel, which
puts a new demand on the deployment: something must bound how big those logs
get. Docker's default `json-file` driver has NO size cap, so a long-running
cohort writes access lines until the volume fills -- the same failure class
#66 was about (an unbounded log), and now it would also trip /readyz's
free-space check and take grading down with it.

Pinned by inspection rather than `docker compose config`, so it runs in the
fast unit suite with no Docker (test_compose_healthchecks.py is the
Docker-requiring sibling). Three things are checked, because dropping any one
of them silently restores the unbounded default:

  1. the `x-logging` anchor exists and caps size (a `driver:` alone is not a cap);
  2. EVERY service references it -- one service left out is one unbounded log,
     and adding a service later without thinking about it is exactly how that
     happens (hence: iterate the services, do not hardcode a list);
  3. the cap stays a cap, with enough history to review after an exam session
     (a `max-file: 1` "rotation" that keeps nothing would pass a naive check).
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSES = [
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.remote-host.yml",
    ROOT / "docker-compose.guacamole.yml",
]

# Byte ceiling per file, and a floor on total retained history per service.
MAX_FILE_BYTES = 50 * 1024 * 1024
MIN_RETAINED_FILES = 2
_SIZE = re.compile(r"^(\d+)([kmgt]?)b?$")


def _unit(char: str) -> int:
    return {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}[char.lower()]


def _services(compose: Path) -> dict:
    # Loader rather than safe_load: the compose files use an anchor/alias for
    # the logging block, which safe_load would leave unresolved.
    return yaml.load(compose.read_text(encoding="utf-8"), Loader=yaml.Loader)["services"]


def _parsed_size(raw) -> int:
    match = _SIZE.match(str(raw).strip().lower())
    assert match, f"unparseable log size limit {raw!r}"
    return int(match.group(1)) * _unit(match.group(2))


def test_every_compose_file_declares_the_logging_anchor():
    for compose in COMPOSES:
        anchor = yaml.load(compose.read_text(encoding="utf-8"), Loader=yaml.Loader).get("x-logging")
        assert anchor, f"{compose.name}: no x-logging block"
        assert anchor["driver"] == "json-file", compose.name
        options = anchor["options"]
        assert _parsed_size(options["max-size"]) <= MAX_FILE_BYTES, compose.name
        assert int(options["max-file"]) >= MIN_RETAINED_FILES, compose.name


def test_every_service_in_every_compose_file_uses_the_anchor():
    for compose in COMPOSES:
        for name, service in _services(compose).items():
            logging_config = service.get("logging")
            assert logging_config, f"{compose.name}:{name} has no logging -- an unbounded json-file log"
            assert logging_config["options"]["max-size"], f"{compose.name}:{name} caps nothing"


def test_the_anchor_is_declared_before_it_is_used():
    """YAML resolves aliases at parse time, so an anchor declared after its
    first use is a hard parse error -- i.e. compose would refuse to start, not
    quietly drop rotation. Asserted anyway because moving the block to the
    bottom of the file is the kind of 'cleanup' that would cause it."""
    for compose in COMPOSES:
        text = compose.read_text(encoding="utf-8")
        assert text.index("x-logging: &default-logging") < text.index("logging: *default-logging")


def test_grading_api_and_viewer_are_covered_by_name():
    """The two services whose logs the contract actually depends on. Redundant
    with the loop above on purpose: it fails with the service name in the
    message if a rename ever moves them out of the checked set."""
    for compose in COMPOSES[:2]:
        services = _services(compose)
        for name in ("grading-api", "viewer"):
            assert services[name]["logging"]["driver"] == "json-file", f"{compose.name}:{name}"
