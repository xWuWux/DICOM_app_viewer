"""
Contract-surface regressions (issue #100): /docs, /redoc and /openapi.json
must 404 on the app itself -- nginx proxies /api/ straight to uvicorn, so
an app-level default would be re-exposed by any edge-config regression;
and the advertised version must be the explicit constant, not FastAPI's
stock "0.1.0".
"""

from app.main import API_VERSION, app


def test_docs_redoc_openapi_are_closed(client):
    for path in ("/docs", "/docs/", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_healthz_still_serves_so_404s_are_intentional(client):
    # non-vacuity: the app is up, the 404s above are deliberate closures
    # (the health route is /healthz -- the one the containers' healthchecks
    # and smoke-test.sh actually use).
    assert client.get("/healthz").status_code == 200


def test_advertised_version_is_the_explicit_constant_not_fastapi_default():
    assert app.version == API_VERSION
    assert API_VERSION != "0.1.0"
    # openapi_url=None disables ROUTES only; the schema object itself must
    # remain buildable in-process (that's the documented offline workflow).
    assert app.openapi()["info"]["version"] == API_VERSION
