# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project adheres to [Semantic Versioning](https://semver.org/spec/v2-0-0/).

## Versioning policy (issue #100)

- The API's advertised version is `API_VERSION` in
  `docker/grading-api/app/main.py` — the single source of truth, reported
  through FastAPI. It is bumped in the **same commit** that adds the
  matching entry below; a matching git tag is cut at release time.
- The REST surface intentionally has **no `/v1` path prefix**: it is an
  internal contract consumed only by our own Kasm/Guacamole launchers and
  viewer pages (never a public API), every endpoint is covered by the
  integration suite, and breaking changes ship paired with those clients
  in the same deployment. Revisit path-versioning before ever exposing
  this API to third-party consumers.
- `/docs`, `/redoc` and `/openapi.json` are disabled at the application
  (issue #100): nginx proxies `/api/` straight to the app, so anything
  enabled app-side is exposed at the public edge. Need the schema offline?
  `app.openapi()` still builds it in-process.

## [Unreleased]

### Breaking (client contract)
- **Session tokens no longer travel in URLs** (issue #94). `GET /case`
  and `GET /results` authenticate via the `X-Grading-Token` header; the
  viewer pages read the token from the URL `#token=` fragment and strip
  it from the address bar immediately; the Kasm/Guacamole launchers pass
  tokens to curl over stdin so they never appear in process listings.
  Any query string on `/api/*` is now rejected with `422` at the nginx
  edge. Minted links from before this change **do not work**.
  - **Deployment runbook**: roll out with active sessions torn down, not
    mid-sitting. Before deploying this build, end every in-flight Kasm
    session (admin console) and re-mint links via
    `scripts/create-session.py`; students reopening an old link will get
    a hard `422` at the edge, and any session whose page still holds the
    old `?token=` link will fail its next `/case` call. For the
    Guacamole PoC, `scripts/provision-guacamole-session.py` re-mints
    against a freshly provisioned connection the same way.

### Breaking (integrator-visible validation)
- `student_id`/`session_id` must match `^[A-Za-z0-9][A-Za-z0-9._@-]*$`
  with a 128-character bound, and `stage`/`category` are closed
  vocabularies (Lung-RADS `0,1,2,3,4A,4B,4X`) at the schema boundary
  (issue #99). Payloads that previously trickled through as `409`s (or
  worse, were stored) are now `422 VALIDATION_ERROR` before any state
  is consulted. Error responses and log lines never echo the rejected
  value.

### Changed
- Submitting against a stage that does not match the student's assigned
  case answers `409 VALIDATION_CASE_MISMATCH` — previously `400`. The
  grading panel reacts by auto-resyncing to the current case (issue #83;
  this is the entry the Tier-4 audit asked for).
- A case's ground truth is frozen once any submission exists; content
  changes ship as a new `cases.version` row instead of an `UPDATE`
  (issue #96). Fresh databases also carry SQLite `CHECK` constraints on
  the stage/category vocabularies (issue #99); existing databases are
  not rebuilt for them.
- When the grading-api is unreachable at session start, the viewer
  launches on the plain study and shows a retryable error — it no longer
  silently substitutes a fake "complete" payload (issue #102).
- `/docs`, `/redoc`, `/openapi.json` disabled app-side; API version made
  explicit (`1.0.0`) instead of FastAPI's stock default (issue #100).

### Security hardening (no contract change for compliant clients)
- API access logs are structured JSON with tokens/credentials scrubbed,
  and validation/500 responses are generic bodies that never echo
  request payloads (issues #93, #95).
- Startup refuses weak/missing coordinator keys and out-of-range token
  TTLs instead of falling back to defaults (issue #97).
