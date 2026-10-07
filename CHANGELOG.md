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

### Added
- `POST /session/revoke` (issue #104) — coordinator-gated like `POST /session`.
  Takes exactly one of `token`/`student_id` and answers
  `200 {"revoked": bool, "count": n}`; an unknown or already-revoked selector is
  `revoked: false`, deliberately **not** 404, because the caller uses this as an
  idempotent compensation step and must not have to distinguish "someone got
  there first" from "never existed". Rejections never echo the submitted token.
  Every revocation logs `session_revoked` server-side with the selector kind and
  the row count — never the credential, never the `student_id` either — because a
  coordinator-keyed security action that leaves no trace cannot be audited
  (DoD 40).
  Additive endpoint, nothing existing changed → advertised API version
  `1.0.0` → `1.1.0`.
- `scripts/create-session.py --ca-bundle` / `TLS_CA_BUNDLE` (issue #104) —
  appends the named CA to the system trust store; verification stays on. The
  flag is added to the store rather than replacing it, because Kasm's gateway
  and `viewer`'s `/api/` proxy routinely have two different issuers.
  `--insecure` still works for a self-signed lab instance, now warns on every
  run, and is refused together with `--ca-bundle` instead of silently winning.
  **No change for invocations that never passed `--insecure`**: verifying was
  already their effective behavior.

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

### Fixed
- Errors from `scripts/create-session.py`'s Kasm calls name Kasm again. When
  `api_call` grew a `label` for the two services it talks to, its default became
  the generic `"API"` and neither `request_kasm` nor `get_kasm_status` passed
  one, so a Kasm failure degraded from "Kasm API error calling
  /api/public/request_kasm" to "API error calling ..." (nit 1 of PR #134's
  review). `label` is now required and keyword-only, so a call site that omits
  it fails as a `TypeError` in the suite instead of printing a vaguer line in
  front of an operator. No `API_VERSION` change: this is operator-facing text in
  a launcher script, not the REST contract.
- A failed `scripts/create-session.py` run no longer leaves a live grading token
  behind (issue #104). The token has to be minted before `request_kasm` — its
  value is injected into that container's environment by that very call — so any
  failure in the Kasm step used to orphan it, usable until its TTL expired. It
  now revokes through `POST /api/session/revoke` on every failure before Kasm
  answers (HTTP error, refused connection, a 200 without `kasm_id`, Ctrl-C,
  SIGTERM, SIGHUP), and prints the undo instructions by `student_id`/`session_id`
  rather than the credential. Once Kasm has answered it does **not** revoke: the
  token is then a live session's credential, and killing it would break a
  student's session to tidy up a bookkeeping row. A signal inherited as ignored
  (`nohup`, `trap '' HUP`) is left ignored, so backgrounded runs keep surviving
  their terminal.
  Review record (DoD 19/45): `docs/history/issue-104-review.md` — the CR on
  PR #134 verbatim plus the two pre-PR review rounds, including their mutation
  tables (which of these guarantees are actually pinned by a test, and which
  tests were shown to fail when the guard was removed).

### Security hardening (no contract change for compliant clients)
- API access logs are structured JSON with tokens/credentials scrubbed,
  and validation/500 responses are generic bodies that never echo
  request payloads (issues #93, #95).
- Startup refuses weak/missing coordinator keys and out-of-range token
  TTLs instead of falling back to defaults (issue #97).
