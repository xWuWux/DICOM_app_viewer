# Degraded modes and behaviour under load (issue #115)

What happens when each critical dependency fails, what the student sees,
what happens to their data, and which of those behaviours is tested. The
load numbers below are **measured** against the real compose stack
(docker compose, nginx 1.29.8, 2026-10-13), not estimated; the harness is
`scripts/test-degraded-modes.sh`, runnable identically locally and in CI.

DoD anchors: 9 (documented/tested failure behaviour of critical
dependencies), 13 (behaviour under overload understood), 75 (documented
degraded mode for "full functionality unavailable"). Related issues:
#102 (fail-open fixed), #70 (SQLite busy_timeout), #4 (rate limiting),
#88 (runbooks), #89 (SLO/NFR), HOSTING_SIZING.md (capacity).

## Table

| Dependency | Symptom (how you notice) | System behaviour | Student-visible message | Data behaviour |
| --- | --- | --- | --- | --- |
| grading-api down | `:8080/api/*` fails fast | nginx answers 502 (occasionally 504 with a stale upstream address); static viewer and Orthanc proxies keep working | grading panel shows its own `"Błąd ładowania ... Spróbuj ponownie"`; Kasm/Weasis startup retries 3x (2 s, 4 s backoff), then opens a study-less session and logs `custom_startup: ERROR grading-api unreachable ...` | No loss. The DB is down *with* the API, so no partial writes are possible; nothing is ever reported as complete on failure (#102 - a failed fetch can no longer produce `{"complete": true}`) |
| SQLite locked > busy_timeout | write requests hang ~5 s, then fail | WAL mode: readers (`GET /case`, `/healthz`) are **not** blocked - measured 200 in 4 ms under a held `BEGIN EXCLUSIVE`. Writers wait `PRAGMA busy_timeout = 5000` (#70), then get the standard error envelope: HTTP 500 `{"error_code":"SERVER_ERROR",...}` (measured: 5.05 s to failure) | panel's generic retry screen; the submit was not accepted, so the answer text is still in the form | No loss. The write never landed (SQLite rejects it whole), the student retries the same submit; progress already stored is untouched |
| Orthanc down (:8042) | direct connections refused | `:8042` -> connection refused instantly (container's DNS name gone) | - | - |
| Orthanc down (:8043 proxy) | viewer iframe stays empty | proxy answers 502 after ~14 s (measured; DNS-gone path) - if Docker DNS still hands nginx a dead address, nginx's 60 s `proxy_connect_timeout` answers 504 instead. **Known bad UX**: the student sees a blank frame with no in-page signal; fixing it (viewer-side error page) is deliberately out of scope here and needs its own issue | none today - blank frame (this is the documented degraded mode, not a fix) | No data change; images unreadable only |
| Kasm / Guacamole down | no session can be opened at all | session containers never start; nothing to proxy - this layer failing means "full functionality unavailable" | nothing in-platform (the browser only reaches the streaming layer's own error) | No loss - nothing this session did was ever local; grading state lives only in the DB |
| Kasm CE licence cap | >5 concurrent sessions refused/queued | Kasm CE permits 5 concurrent sessions (EULA 2.2, non-commercial); the 6th cannot be served (see HOSTING_SIZING.md - 50 sessions require paid Kasm or Guacamole) | streaming layer's own capacity error | n/a |
| Redis | - | **Does not exist**: there is no Redis dependency anywhere in the stack, so there is no Redis failure mode. Conscious choice, not an oversight - the only stateful services are Orthanc (volume) and grading-api (SQLite volume) | n/a | n/a |
| Container unhealthy but still running (#206 class) | `docker compose ps` shows `(unhealthy)` next to `Up` | `restart: unless-stopped` is the current policy and does **not** act on healthcheck failures (compose has no autoheal) - the half-dead container keeps running and serving nothing until someone restarts it; the health checks (#69) exist precisely so this is visible in `ps` | depends on the service: grading-api unhealthy = panel error screen, viewer unhealthy = the page itself does not load | No data risk in itself; recovery is a manual `docker compose restart <svc>` (runbook, #88). Conscious gap: no autoheal agent runs |
| Start-up refused on unwritable grading-db (#209) | `docker compose up` aborts with grading-api's single `CONFIGURATION ERROR: ...` line | `db._check_data_writable()` refuses to serve at all: exit code 2, the same clean-fatal contract as config.py; the repository compose normally never reaches it because the one-shot `grading-db-init` service fixes ownership first - the refusal is the belt for every other way of running the image | platform fully unavailable (fail-closed) - a doctor never gets a half-working screen | Data protected, which is the whole point: no green `/healthz` lying while every first write 500s (the pre-#209 bug this replaced) |
| nginx rate limit (intended degradation) | clients above 10 r/s sustained | `limit_req zone=api_limit rate=10r/s burst=20 nodelay` on `/api/` answers 429 **at the edge** - the request never reaches grading-api; measured zero 5xx under overload. 429 body is nginx's own HTML (`text/html`), and **no `Retry-After` header is sent** (documented gap below) | panel sees 429 on its next poll/submit; nothing is marked complete | No loss. Rejected requests never touched the app or the DB |

## Measured overload characteristics

All measurements: single-node docker compose, hitting the real nginx
`:8080/api/healthz` (the rate-limited edge).

* **60 concurrent requests, one burst:** 21x 200 + 39x 429, zero 5xx.
  (200 = burst allowance 20 + ~1 request served while the burst
  refills at 10 r/s.)
* **~25 r/s sustained for 4 s:** accepted throughput converges to
  ~10-11 r/s; every request above that answers 429 within milliseconds
  (nodelay - rejected immediately, no queueing at nginx either). Zero 5xx.
* **`Retry-After` is NOT sent today.** A well-behaved client cannot know
  how long to back off, so it will keep hammering and keep eating 429s.
  Deliberate gap, not missed: adding `add_header Retry-After` on the 429
  path needs the panel's retry logic reviewed alongside it (it already
  backs off on its own), and the test suite pins the current header set,
  so adding it later fails CI loudly instead of drifting silently.
* **Who is rate-limited is per source IP** (`$binary_remote_addr`). In
  the real deployment each Kasm session container has its own address on
  `kasm_default_network`, so the 10 r/s budget is *per student*, not
  shared across the cohort - a cohort of 300 cannot exhaust each other's
  allowance. Only a future shared-NAT egress would change that.

## What saturates first as the cohort grows

Ordered by which limit bites first on the current architecture
(HOSTING_SIZING.md owns the hardware numbers; this is the failure-order
view):

1. **Kasm session slots.** With Kasm CE the cap is 5 concurrent sessions
   - at 50 scheduled users the *first five* get in and nobody else gets
   an error path worth documenting, because "not able to start a session"
   is the streaming layer's failure, not this stack's.
2. **Session-host RAM.** Sessions are long-lived and RAM cannot be
   overcommitted (HOSTING_SIZING.md); the first host failure without the
   N+1 buffer drops exactly the sessions on it.
3. **Orthanc disk I/O under bulk image fetch** (whole-study loads during
   case open) - degrades as slowness first, not errors.
4. **SQLite single writer.** Writes are tiny and serialised; at 50
   students submitting within the same second the queue stays far below
   the 5 s timeout. A lock *timeout* therefore almost always means a
   stuck writer or external process holding the file (backup tool,
   manual session), not organic load - that is the diagnostic to record
   in the runbook (#88).
5. **nginx rate limit** never bites a real *human* (a human cannot click
   10 times per second at the API for long); it fires on any faster-than-
   human client pacing. **Observed in the wild, 2026-10-13 (PR #219 CI),
   measured for this doc:** the Playwright visual-regression suite
   (92 tests today) drives every test from ONE source IP. One full local
   run made 188 `/api/` requests across its ~3.5 min window - mean
   0.9 r/s, 4 r/s over active seconds, **peak second: 18 req/s** (the
   per-second histogram also shows 12 and 14) - pressed right against the
   20-request burst allowance, so an unlucky CI second crossing it fails
   a test on the panel's own "Błąd ładowania ... 429 ..." screen (flake
   runs listed in #227). Same class as a buggy retry loop -- a real
   client that is not a human -- so the limiter is doing its job; the
   test suite's pacing is what must adapt: #222 (client-visible pacing)
   and #227 (test-side `API_RATE_LIMIT` parameter, production default
   untouched).

## Test coverage

`scripts/test-degraded-modes.sh` (compose E2E, same style as
`smoke-test.sh`, CI job `degraded-test`) pins one test per table row that
is testable without a Kasm/Guacamole install:

| Table row | Assertion |
| --- | --- |
| grading-api down | `/api/*` answers 502/504 and never 200; static viewer still answers 200 (isolation); recovery after restart |
| SQLite locked | a write under a held exclusive lock waits >= 4 s then answers 500 with the `{"error_code": ...}` envelope; a later identical write succeeds (no lost state) |
| Orthanc down | `:8043` proxy answers 502/504 (never a hang beyond nginx's own 60 s + margin, never a 200); `/api/healthz` still 200 through it |
| rate limit | 60-request burst produces >= 1 x 429 and **zero 5xx** (no 5xx cascading); 429 carries no `Retry-After` today (pin, see gap above) |
| Kasm/Weasis startup fail-open (#102) | covered by `docker/kasm-workspace-weasis/custom_startup.bats`: unreachable grading-api retries 3x, logs loudly, never fakes `complete` |
| unhealthy-but-running container (#206 class) | not tested by design - the test would only prove compose has no autoheal; documented above, recovery is the runbook's (#88) |
| grading-api restart mid-exam (#238 G) | a doctor's learning answer recorded, `compose restart grading-api`: the same token still works, `/api/case` is at the same stage and case, re-sending the answered case gets 409 (no duplicate, no rollback) |
| Orthanc restart (#238 G) | instance count identical before and after `compose restart orthanc`; the `:8043` proxy serves `/system` again |
| whole stack recreated (#238 G) | `compose up --force-recreate` of every service (a restart would keep the container layer, so it could not prove volume persistence): the grading-api container id changes, the scenario-5 doctor's token and progress and Orthanc's instance count are unchanged, and grading-api's `/data` is this project's `grading-db` volume (type and name) |
| refused start on unwritable grading-db (#209) | pinned by `docker/grading-api/tests/test_config.py` (SystemExit code 2), not by this compose suite |

Untested by design: Kasm/Guacamole availability and the CE cap need
real infrastructure CI runners do not have (same boundary as
`smoke-test.sh`'s own header comment).

## Consciously undocumented-as-features today (candidate follow-ups)

* No viewer-side error page for a dead Orthanc iframe (blank frame).
* No `Retry-After` on 429 and no distinct 503 `DB_BUSY` for lock timeouts -
  tracked as #222 (pinned in `scripts/test-degraded-modes.sh` until then).
* No autoheal for unhealthy-but-running containers (#206 class) - manual
  `docker compose restart` per the runbooks (#88) is the current recovery.
* No SLO/alerting numbers - #89. The signals they will be written against,
  their units and an initial (coarse) alert schema now exist in
  `docs/observability.md` (issue #113); this file stays the behaviour view,
  that one is the contract view.

## Reading a failure now (issue #113)

The rows above describe behaviour; these are the probes that make a row
observable. Full definitions in `docs/observability.md`.

| Question | Ask | Meaning |
| --- | --- | --- |
| Is the process alive? | `GET /livez` | no dependency is touched; a 200 says nothing about grading |
| Can the database be read? | `GET /healthz` | unchanged, and still what both compose healthchecks and `viewer`'s `depends_on` use |
| Would a request succeed right now? | `GET /readyz` | 503 = database read **or write** or free space failed; 200 `degraded` = only Orthanc is unreachable |
| What are the numbers? | `GET /internal/signals` (internal network only) | counters/gauges/histograms; answers `db_available: false` instead of 503 when the database is gone |

The write half of `/readyz` is what covers the "readable but not writable"
class (a volume remounted read-only, or filled): `_check_data_writable()`
refuses that state at start-up only (#209), and a `/healthz` that only reads
would stay green while every `/submit` answers 500.

## NEW-D: shutdown and recovery (#116)

- **SIGTERM:** Uvicorn stops accepting new connections and allows up to 5 s for
  in-flight requests. A submit completed in that window returns normally. A
  blocked synchronous handler cannot be stopped by cancelling its ASGI task;
  Docker can still SIGKILL it at its default 10 s grace. Lifespan shutdown logs
  `graceful_stop` with the checkpoint outcome, not a promise every request finished.
- **Crash during submit:** submission and progress share one transaction (#60).
  Both commit or neither does. A lost response can hide a successful commit;
  reload `/case` after reconnecting to discover the current position. A retry
  of an already committed answer can return 409. A crash alone does not revoke
  tokens or imply 401; 401 means an invalid/expired token. Through nginx an
  interrupted request can fail with 502/504 or a network error; the panel shows
  its existing retry/error flow and does not report completion on failure.
- **Startup integrity failure:** SQLite first handles WAL recovery. One
  `PRAGMA quick_check` runs before initialization/migration. Failure is cached;
  migrations and database traffic are blocked, `/healthz` and `/readyz` return
  503, `/livez` remains alive, and `db_integrity_failed` names only the error
  class. A new viewer cannot start through Compose's service-health dependency.
  An already running viewer shows its API error flow. No automatic reset or
  deletion occurs. This check is structural, not an application/schema audit.
- **Interrupted migration:** the pre-migration backup remains. All existing-schema
  migration steps (including the old submissions rebuild and column backfills)
  now share one explicit transaction. Restart sees the old or new schema, never
  the intermediate table swap. This does not automatically repair a database
  already half-migrated by an older release: restore a verified backup first.

### Durability and limits

WAL with `synchronous=FULL` syncs on each commit. SQLite's guarantee assumes the
filesystem, hypervisor and storage device honor sync requests; it is not a
substitute for backups or a tested storage stack. `NORMAL` also survives process
crashes but can lose recent commits after OS/power loss. The subprocess SIGKILL
tests prove process-crash behavior only, not a power-loss guarantee or a latency
SLO. Measure FULL's commit latency on the deployment volume before cohort use.
See [SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous).

The explicit shutdown `wal_checkpoint(TRUNCATE)` uses a separate short-lived
connection and a 100 ms lock timeout. It is best effort: SQLite normally
checkpoints at last close anyway. Busy/failure leaves WAL for recovery, never
justifies deleting `-wal`/`-shm`, and never changes commit semantics. Filesystem
I/O and the complete shutdown sequence have no hard in-process time bound.

### Recovery procedure

1. Stop grading traffic and the service before replacing files. Preserve the
   failed database and its sidecars together for investigation; do not copy only
   a live main database or delete WAL to make a check pass.
2. Fix storage/permissions if that caused the failure. For actual corruption,
   validate and restore a consistent backup using the existing backup/restore
   procedure in `docs/runbooks/grading-db-migration-and-case-versions.md`. Do not overwrite the only copy.
3. Restart grading-api. Confirm `/healthz` and `/readyz` pass, inspect startup logs,
   then resume the viewer. `/livez` alone is insufficient. A failed gate stays
   closed until restart; repeated probes never rerun the integrity check.
4. Reconcile a student's current case before re-entering an uncertain submission.

### Verification and rollback

Run `scripts/test-shutdown-recovery.sh`: real Uvicorn subprocesses, isolated
loopback sockets, temporary synthetic databases, no Docker or deployment data.
It tests graceful drain, drain-timeout plus a simulated final Docker kill,
acknowledged WAL commits and an uncommitted submit across restart, corrupt-store
refusal, interrupted migration branches, and restoring a migration backup.
The regular grading suite pins FULL and bounded-lock-wait checkpoint behavior.
The subprocess suite runs in the Integration stage before degraded-mode E2E.

Rollback is a code revert; FULL changes no database format. Reverting also
removes the startup gate and restores the older migration interruption risks.
Keep the backup. This change provides no replica, automatic failover, power-cut
proof, or recovery-time objective; those require deployment drills.
