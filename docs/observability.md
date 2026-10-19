# Observability contract (issue #113)

The telemetry surface of this platform, written down so an alert, an SLO or a
runbook can be built against a **name that will not move** instead of against
whatever a log line happened to contain that month.

Scope: the grading service, its database, its container logs and the health
endpoints. Out of scope here (and why):

* **No Prometheus/Grafana.** Tier 4 explicitly does not require a metrics
  server, and the stack has none. The signal names below are deliberately
  Prometheus-compatible (`_total` on counters, `_ms` on histograms, flat
  label maps) so adopting a scraper later is an exporter change, not a
  rename of every alert.
* **No Kasm/Guacamole telemetry.** grading-api has no network path to the
  streaming layer (the relationship runs the other way: session containers are
  allowed to reach grading-api, not the reverse) and no Kasm/Guacamole
  credentials. Session-level signals therefore belong to the launcher scripts
  and the vendor's own API; see docs/runbooks/kasm-session-lifecycle.md. (ADR
  0001, still proposed in #252, is about freezing Kasm *sessions* — it neither
  freezes Kasm nor changes this boundary.)
* **No queue/worker backlog.** There are no queues or background workers in
  this system. Recorded as a justified N/A, same as DOD_MAPPING.md item 111.

DoD anchors: 10 (telemetry contract), 11 (health signals vs. broken
monitoring), 87 (storage telemetry), 88 (API/UI telemetry), 104 (alerts
including alert-evaluation failure), 112 (dependency failure, saturation,
staleness). Related: #70 (busy_timeout), #222 (503 `DB_BUSY` + `Retry-After`),
#89 (SLI/SLO — consumes this contract), #88 (runbooks — the alert table links
into them), #115 (degraded modes).

## Channels

Three, and they are not interchangeable:

1. **stdout, one JSON object per line.** Primary. Container-native — the
   deployment's log driver already collects stdout. Every line carries
   `timestamp`, `level`, `logger`, `message`, `request_id` and then the
   event's own fields as top-level keys. Rotation belongs to the log driver;
   `docker-compose.yml` pins `json-file` with `max-size`/`max-file` so an
   unbounded log cannot fill the volume (the failure class behind #66).
2. **`GET /internal/signals`** — aggregated counters/gauges/histograms as one
   JSON document, plus the last full `/readyz` evaluation under `readyz`. No
   log pipeline needed. Reachability is stated in the matrix below: it is
   unreachable from outside the Docker network, **reachable to every container
   on `ipcmc-internal`**, and unauthenticated by design — which is why it
   carries only aggregate counts, sizes and timestamps.
3. **`GET /livez`, `GET /readyz`** — see [Health endpoints](#health-endpoints).

Aggregation over a window is *not* implemented in this repository: the log
line and the snapshot are the raw signals, and whoever alerts is expected to
do the counting (`docker logs | jq`, a log driver query, or the snapshot
polled by a small checker). That is the Tier 4 "checklist + existing signals"
allowance, stated rather than implied.

### Who can reach what

Stated as a matrix because "internal" is not one property: nginx's
`location /api/` proxies to `http://grading-api:8000/` **with a trailing
slash**, which strips the prefix — so *any* app path is reachable at the edge
as `/api/<path>` unless the edge blocks it by name. (Assuming otherwise is how
the first revision of this feature published the snapshot at
`/api/internal/signals`.)

| Caller | `/api/livez`, `/api/healthz`, `/api/readyz` | `/internal/signals` | `grading-api:8000/internal/signals` |
| --- | --- | --- | --- |
| Anyone reaching viewer's `:8080` | yes | no — nginx has no such location | no (no published port) |
| Anyone reaching `:8080`, path `api/internal/...` | — | no — `location ^~ /api/internal/ { return 404; }` blocks the prefix-stripped form; pinned by `scripts/tests/test_internal_surface_not_exposed.py` and `scripts/smoke-test.sh` | — |
| Any container on `ipcmc-internal` (viewer, and a student's own session container in the Guacamole flow, which `provision-guacamole-session.py` attaches there) | yes | **yes** | **yes** |

Consequences of the last row, accepted deliberately: the snapshot must contain
nothing identifying, nothing secret and nothing about an individual submission
(see [Forbidden fields](#forbidden-fields)); and "internal" here means *the
worst case is a student reading aggregate operational numbers about their own
exam room*, not *unreachable by a student*. If the snapshot ever needs to
carry anything more sensitive, it gets a shared token — do not re-derive
safety from the network layout.

## Correlation

`request_id` is the only join key. Set by `RequestIdMiddleware`: an incoming
`X-Request-Id` is honoured **only** if it matches `[A-Za-z0-9._-]{1,64}`
(issue #119 — otherwise it is rejected and replaced by a server `uuid4().hex`,
so a client cannot forge log lines or grow them without bound). It is echoed
in the response header and appears on every line emitted while handling the
request, including the unhandled-500 handler that runs outside the
middleware's scope.

Traces/spans are **not** implemented: a single-process SQLite-backed service
has no cross-service call graph to trace. `request_id` + the per-request
`duration_ms` are the whole correlation story. Recorded as a scope reduction
for DoD 10, not as an oversight.

## Forbidden fields

Never in a log line, never in a snapshot, never in an error response:

* the session token, `GRADING_COORDINATOR_KEY`, any Kasm/Guacamole secret;
* `student_id` / `session_id` (pseudonymous, not automatically
  non-identifying — `logging_config.py`'s own rule);
* free-text student answers (`submitted_text`) and reference reports;
* raw exception text from a database error (it contains the DB path — the
  leak issue #62 closed; `/readyz` reports the exception **class** only and
  the detail goes to the log);
* the rejected value of a malformed `X-Request-Id` (issue #119).

The sanitizer behind the 422 path (issue #93) and the encryption-at-rest
decision (#107) build on this list; it is a boundary, not a suggestion.

## Events (log lines)

`message` is the event name; these names and field names are the contract.
Diagnostic events (e.g. `telemetry_db_gauge_failed`) may be added freely —
only the ones below are promised to an alert.

| Event | Level | Fields | Emitted when |
| --- | --- | --- | --- |
| `request` | INFO | `method`, `path`, `status_code`, `duration_ms` | every request, once, in the middleware |
| `app_error` | WARNING | `error_code`, `status_code`, `path` | any deliberate error response (`AppError`) |
| `validation_error` | INFO | `path`, `errors` | 422 from Pydantic (input values are not logged) |
| `unhandled_exception` | ERROR | `path`, `request_id`, `exception` | a bug, not a deliberate error — full traceback |
| `db_lock_timeout` | WARNING | — | a SQLite writer lock outlived `busy_timeout`; fires once per occurrence, from the 500 path or the readiness write probe |
| `healthz_database_error` | ERROR | `exception` | `/healthz` could not read the database (503) |
| `submission_recorded` | INFO | `stage`, `is_correct` | a submission was accepted (never the answer text) |
| `stage_advanced` | INFO | `from_stage`, `to_stage` | learning → assessment → test |
| `training_complete` | INFO | `from_stage` | the test stage finished |
| `request_id_rejected` | WARNING | `request_id` (server-generated) | a supplied `X-Request-Id` failed validation — the fact, never the value |
| `telemetry_snapshot` | INFO | `sessions_active`, `requests_total`, `requests_4xx_total`, `requests_5xx_total`, `submissions_total`, `db_lock_errors_total`, `db_size_bytes`, `volume_free_bytes`, `dependency_orthanc_up`, `readyz_ok`, `last_submission_age_seconds`, `telemetry_last_success_unixtime` | every `GRADING_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS` (default 30, `0` disables) |
| `telemetry_snapshot_failed` | ERROR | `exception` | the snapshot could not be built — itself a signal |

## Signals (metrics)

`GET /internal/signals` renders exactly these. `Type` follows Prometheus
conventions; `Labels` is the complete label set (there are no others, and the
`path` label is bounded to a fixed route list — anything else collapses to
`<other>` so a scanner cannot inflate cardinality).

| Signal | Type | Unit | Labels | Source |
| --- | --- | --- | --- | --- |
| `grading_requests_total` | counter | requests | `method`, `path`, `status_class` | request middleware |
| `grading_request_duration_ms` | histogram | milliseconds | `method`, `path` | request middleware |
| `grading_sessions_active` | gauge | sessions | — | `sessions` table (`expires_at > now`) |
| `grading_submissions_total` | counter | submissions | `stage` | `POST /submit`, after the commit |
| `grading_db_lock_errors_total` | counter | errors | — | 500 path and readiness write probe (`database is locked`) |
| `grading_db_write_duration_ms` | histogram | milliseconds | `operation` | around `/submit`'s commit |
| `grading_db_size_bytes` | gauge | bytes | `file` | stat of `grading.db`, `-wal`, `-shm` |
| `grading_volume_free_bytes` | gauge | bytes | — | `statvfs` of the database directory |
| `grading_dependency_up` | gauge | 1 (up) / 0 (down) | `dependency` | readiness probe of `GRADING_ORTHANC_URL` |
| `grading_dependency_probe_duration_ms` | gauge | milliseconds | `dependency` | same probe |
| `grading_readyz_ok` | gauge | 1 (ready) / 0 (not ready) | — | last `/readyz` evaluation |
| `grading_readyz_last_check_unixtime` | gauge | unix seconds | — | last `/readyz` evaluation |
| `grading_telemetry_last_success_unixtime` | gauge | unix seconds | — | last successfully built snapshot |
| `grading_process_start_unixtime` | gauge | unix seconds | — | process start |
| `grading_last_submission_unixtime` | gauge | unix seconds | — | newest `submitted_at` |
| `grading_last_session_mint_unixtime` | gauge | unix seconds | — | newest `created_at` in `sessions` |

`status_class` is `2xx`/`3xx`/`4xx`/`5xx`. Histogram buckets are cumulative, as
Prometheus defines them; request buckets are
`5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000` ms (the 5000 edge is
`busy_timeout`: a sample landing there is a lock wait, not a slow query) and
DB-write buckets are `1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 5000` ms.

### What each group answers (DoD 87 / 88 / 112)

* **API/UI (88)** — request count, latency histogram, 4xx/5xx rate and active
  sessions. Note that `grading_sessions_active` counts *live tokens*, i.e.
  sessions that can still act; it is not a count of Kasm containers.
* **Storage (87)** — write latency, lock-timeout count, database + WAL size and
  free space. The lock counter exists because #70 showed the failure that hurts
  students is a write, not a read: `database is locked` after
  `busy_timeout=5000` used to be a bare 500 with no warning signal anywhere.
  #222 will change that status to `503 DB_BUSY` + `Retry-After`; the counter is
  deliberately independent of the status code, so the alert survives that fix.
* **Dependency & saturation (112)** — `grading_dependency_up` for Orthanc and
  the two saturation gauges (`volume_free_bytes`, `db_write_duration_ms`).
  `grading_dependency_up` means **reachable**, not healthy: Orthanc requires
  authentication and this service holds no Orthanc credentials, so any HTTP
  answer (401 included) counts as up. Only a connection error or timeout is
  down — which is exactly what a student experiences as a blank frame.
* **Staleness** — `grading_last_submission_unixtime` and
  `grading_last_session_mint_unixtime` are the closest thing this system has to
  "collection is stale": during a scheduled exam, no new submission for a long
  window means the cohort is blocked, not that they are thinking.
  "Stale collection" in the sense of an ingestion pipeline does not apply —
  studies are loaded before a cohort, not streamed.

## Health endpoints

| Endpoint | Question | Touches | Codes | May trigger |
| --- | --- | --- | --- | --- |
| `/livez` | is the process answering? | nothing | 200 | a container restart |
| `/healthz` | can the database be read? | DB read | 200 / 503 | existing compose healthchecks; `viewer` startup |
| `/readyz` | would a student's request succeed now? | DB read + DB write + free space + Orthanc reachability (cached) | 200 (`ready`/`degraded`) / 503 | external monitoring only |
| `/internal/signals` | what are the numbers? | read-only queries + `stat`/`statvfs` | 200 always | nothing |

Design rules that are part of the contract:

* **`/readyz` answers with verdicts, `/internal/signals` with measurements.**
  `/readyz` is reachable at the public edge (it is under `/api/`, and the
  prefix is stripped by nginx), so its body carries the status, which check
  produced it and the exception class — never `free_bytes`, `min_free_bytes`,
  probe durations or the dependency `detail`. Those are facts about this
  deployment, not about the caller's request: published disk capacity and
  headroom is reconnaissance for a disk-fill attack. `health.public_view()`
  enforces the split; the full evaluation is exposed as the `readyz` field of
  the internal snapshot and written to the log.
* **`/internal/signals` is blocked at the edge by name, not by accident.**
  `location /api/` strips its prefix, so `/api/internal/signals` would otherwise
  serve the snapshot to anyone who can reach `:8080`. See the reachability
  matrix above.

* **`/healthz` stays narrow.** `viewer` has `depends_on: service_healthy` on
  grading-api. Folding an Orthanc probe into `/healthz` would make an
  unavailable Orthanc un-startable grading panel — a total outage out of a
  partial degradation. Readiness lives at `/readyz`; liveness at `/livez`.
* **`/readyz` is not a restart trigger.** A full disk answers 503 forever; a
  restart policy pointed at it would loop. It is for a dead-man's switch and
  for a human.
* **`/readyz` write-probes a dedicated one-row table** (`telemetry_probe`), not
  real data, and its result is cached (`GRADING_HEALTH_CACHE_SECONDS`, default
  15) because an unauthenticated endpoint that takes a SQLite write lock on
  every call would be a self-inflicted contention source on the public edge.
  `cached: true` marks a stale verdict.
* **`/readyz` reports `degraded`, not 503, when only a dependency is down.**
  Grading works without Orthanc; the two must be alertable separately.
* **`/internal/signals` answers with `db_available: false` instead of an
  error** when the database cannot be opened. "The probe works and reports a
  broken database" is the distinction DoD 11 is about; a 503 there would erase
  it.

## Alert schema (DoD 104, 112)

Initial thresholds — deliberately coarse, to be tuned against #89's SLIs once
real cohorts run. Every alert must name an owner and a runbook (DoD O12); the
runbook catalogue is #88, the service/owner catalogue #86.

| Alert | Condition | For | Severity | First action |
| --- | --- | --- | --- | --- |
| `ApiUnreachable` | `/readyz` connection refused/timeout | 2m | critical | grading-api down runbook (#88) |
| `ApiNotReady` | `/readyz` → 503 | 2m | critical | read `checks` for which core check failed |
| `Api5xxRate` | 5xx share of `grading_requests_total` > 2% and > 5/min | 5m | critical | check `grading_db_lock_errors_total` first, then the traceback lines |
| `DbLockStorm` | `increase(grading_db_lock_errors_total)` > 0 | 10m | warning | writer contention; #70 / #222, and whether a cohort is submitting simultaneously |
| `DbWriteSlow` | p95 of `grading_db_write_duration_ms` > 1000 ms | 10m | warning | same as above, earlier |
| `VolumeLow` | `grading_volume_free_bytes` < 500 MiB | 5m | warning | free space; backups accumulate (#85) |
| `VolumeCritical` | `grading_volume_free_bytes` < 100 MiB | 1m | critical | writes will fail; `/readyz` is already 503 |
| `DatabaseGrewFast` | `grading_db_size_bytes{file="db"}` up > 2× in 1h | 10m | warning | unexpected writes; check for a migration loop |
| `OrthancDown` | `grading_dependency_up{dependency="orthanc"} == 0` | 2m | warning | images unreadable, grading unaffected — do not restart grading-api |
| `NoSubmissions` | `time() - grading_last_submission_unixtime` > 15m **during a scheduled exam** | 15m | warning | cohort blocked (409 loop, panel error) — check panel and API logs |
| `TelemetryStale` | `grading_telemetry_last_success_unixtime` not advancing for 3× the snapshot interval | 2m | critical | the monitoring path itself is suspect — see the dead-man's switch below |
| `AlertEvaluatorDead` | the evaluator's own heartbeat is missing | 5m | critical | the check is independent of `grading-*`; see below |

`NoSubmissions` is the only alert that needs a human-defined window (an exam
schedule); the rest are self-contained. Every alert that fires should be
answered with a runbook entry, not improvised on the night.

## Dead-man's switch (DoD 11, 104, 112)

A monitored process cannot report "my monitoring is dead" — a dead monitor
reports nothing, and "nothing" is indistinguishable from "everything is fine".
So the last link is **external and independent**:

1. An evaluator (compose sidecar or a systemd timer on the Proxmox host, i.e.
   outside every container it watches) polls `/readyz` and
   `/internal/signals` on a fixed interval.
2. It alerts on the two *different* failures: a probe that answered "bad"
   (`ApiNotReady`, `Api5xxRate`, …) and **no answer at all**.
3. It writes its own heartbeat, and a second, independent check —
   ideally on a different host — alerts when that heartbeat stops
   (`AlertEvaluatorDead`). That second check is what makes "the monitoring is
   not running" observable.
4. `grading_telemetry_last_success_unixtime` is the in-band half: it stops
   advancing when the snapshot thread dies, which an evaluator can see without
   trusting the evaluator.

The evaluator itself is not in this repository yet. What this contract fixes
is the part that must exist first: stable names for both halves, and the
`/readyz` `/livez` split that makes "bad" and "silent" separable.

## Known limitations (documented, not hidden)

* **Counters reset on process restart.** They are in-process. An alert must
  use `increase()`-style windows and `grading_telemetry_last_success_unixtime`,
  never "absolute value is zero", or a restart will look like a clean slate.
* **One process, one set of counters.** The Dockerfile runs uvicorn without
  `--workers`. Adding `--workers N` would split every counter into N partial
  totals — a change that must come with a real exporter or a shared store.
* **`/readyz`'s write probe is a write.** Cached, small, one row — but under a
  held exclusive lock it waits out `busy_timeout` like any other writer, which
  is intentional: that is the condition it exists to detect.
* **No traces/spans** (single process, no cross-service calls) and **no
  ingestion-pipeline staleness** (no pipeline) — both are scope reductions for
  DoD 10/104, recorded here so the absence is a decision, not an omission.
* **Kasm/Guacamole session counts are not visible from here.** Active
  containers are the launcher's and the vendor's view; see
  docs/runbooks/kasm-session-lifecycle.md.

## DoD mapping

| DoD | Where it is satisfied |
| --- | --- |
| 10 — documented telemetry contract | this file: events, signals, units, correlation, forbidden fields |
| 11 — health signals vs. broken monitoring | `/livez` + `/healthz` + `/readyz` semantics above; dead-man's switch section |
| 87 — storage telemetry | `grading_db_write_duration_ms`, `grading_db_lock_errors_total`, `grading_db_size_bytes`, `grading_volume_free_bytes` |
| 88 — API/UI telemetry | `grading_requests_total`, `grading_request_duration_ms`, `grading_sessions_active` |
| 104 — dashboards/alerts incl. alert-evaluation failure | alert schema table + dead-man's switch |
| 112 — dependency failure, saturation, staleness, recovery | `grading_dependency_up`, volume/write-latency gauges, `grading_last_*_unixtime`, `/readyz` re-probe after recovery |
