"""The internal telemetry surface stays off the public edge (issue #113 review).

nginx's `location /api/` proxies to `http://grading-api:8000/` WITH a trailing
slash, which strips the prefix -- so `/api/anything` arrives at grading-api as
`/anything`. That makes "this path is not under /api/, so nginx cannot proxy
it" false, and it is the exact reasoning that let `/api/internal/signals`
serve the whole signal snapshot at the public edge in the first PR revision.

Pinned here without Docker, the way test_api_rate_limit.py pins the edge rate
limit: a template edit that drops or reorders the block fails in the fast unit
suite instead of in a review.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "docker" / "viewer" / "default.conf.template"
SMOKE = ROOT / "scripts" / "smoke-test.sh"

BLOCK = re.compile(r"location\s+\^~\s+/api/internal/\s*\{[^}]*return\s+404\s*;[^}]*\}")


def test_the_template_blocks_the_internal_prefix_at_the_edge():
    """`^~` and 404 both matter: `^~` stops a regex location added later from
    outranking the block, and 404 (not 403) does not confirm the path exists."""
    body = TEMPLATE.read_text(encoding="utf-8")
    assert BLOCK.search(body), "no `location ^~ /api/internal/ { return 404; }` in the viewer template"


def test_the_block_is_declared_before_the_api_proxy_location():
    """Documented intent. nginx picks the longest matching prefix either way,
    so this ordering is belt-and-braces -- asserted because reordering the file
    while "tidying" is exactly what would make it stop being belt-and-braces."""
    body = TEMPLATE.read_text(encoding="utf-8")
    assert body.index("location ^~ /api/internal/") < body.index("location /api/ {")


def test_only_the_8080_server_block_is_affected():
    """The Orthanc auth proxy (:8043) has its own locations; the internal block
    must not have been inserted into that server by accident."""
    body = TEMPLATE.read_text(encoding="utf-8")
    first_server = body[: body.index("listen 8043")]
    assert BLOCK.search(first_server)


def test_the_smoke_test_checks_the_proxied_path():
    """The edge rule and the integration check have to move together: asserting
    only /internal/signals (never proxied) is what let the original bug pass."""
    smoke = SMOKE.read_text(encoding="utf-8")
    assert "http://localhost:8080/api/internal/signals" in smoke
    assert '"summary"' in smoke, "the smoke test must distinguish nginx's 404 from a proxied JSON answer"
