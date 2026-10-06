# DoD mapping (Tier 4) — master 6dab554 + issues #93-#108

> Assessment as of **2026-10-06**, AI-assisted (audit verification + issue filing by pi; verified by human owner), **requires human review** of every verdict. These are point-in-time against master `6dab554` — re-evaluate a row when its ticket closes (a merged fix silently invalidates GAP/UNKNOWN rows).

Legend: GAP = no control yet; PARTIAL = some control; MET = per audit evidence; N/A = justified; UNKNOWN = not assessed. Formerly-proposed NEW-* placeholders are now filed tickets: telemetry contract+signals=#113, SBOM=#114, degraded modes/overload=#115, graceful shutdown=#116, deprecation/vendor-exit=#117 (plus #119 X-Request-Id header validation, from CR #110).
Tier 4 = N/A needs justification; reasons in Ticket column.

## Per-ticket (115)
| # | Item | Verdict | Ticket / note |
|---|---|---|---|
| 1 | Functional requirements and acceptance criteria are defined and testable.… | PARTIAL | #90 |
| 2 | Applicable non-functional requirements are defined: availability, latency, throughput, capacity, security, ret… | GAP | #89 |
| 3 | Error behavior, boundary conditions, and unsupported cases are explicitly defined.… | PARTIAL | #99 #100; #8 has no AC |
| 4 | External dependencies and integration boundaries are identified, including their documented availability and f… | PARTIAL | #86 |
| 5 | Backward-compatibility requirements are identified for APIs, events, schemas, files and persisted data; databa… | GAP | #100 #96 (409 change unannounced) |
| 6 | Security, privacy, compliance and data-classification requirements are identified, including whether data is p… | GAP | #87 |
| 7 | Significant architectural decisions are documented and approved through an ADR (or equivalent) before implemen… | GAP | #90 |
| 8 | Data retention, deletion, archival and legal/compliance requirements are considered.… | GAP | #87 #98 |
| 9 | Critical dependencies have documented failure behavior and fallback/degradation behavior.… | GAP | #115 (degraded modes) #102 |
| 10 | A documented telemetry contract (e.g. docs/observability.md) specifies metric names, units, trace/span names, … | GAP | #113 |
| 11 | Health signals are designed to distinguish 'no problems detected' from 'the monitoring system itself has stopp… | GAP | #113 |
| 12 | Expected workload, capacity assumptions, and latency/throughput/resource budgets are documented for critical p… | GAP | #89 |
| 13 | Performance degradation behavior under overload is understood.… | GAP | #89 #115 |
| 14 | Code formats, lints, type-checks and builds successfully.… | PARTIAL | lint.sh ok; no type-check -> #101 |
| 15 | Code follows project architecture and established design conventions.… | UNKNOWN | needs review pass — owner: repo owner + reviewer (Claude); target: review pass by 2026-10-31 |
| 16 | No dead code, unnecessary duplication, or unjustified complexity is introduced.… | PARTIAL | #99 (dead client code) |
| 17 | Public APIs, events, configuration keys and operational interfaces are documented and versioned.… | GAP | #100 |
| 18 | Static analysis completes without unresolved high-severity findings.… | PARTIAL | #71 |
| 19 | Code review has been completed according to project rules.… | GAP | #106 |
| 20 | New configuration, infrastructure or container changes are validated and scanned where applicable.… | PARTIAL | #71 #51 #68 |
| 21 | The change does not introduce undocumented technical debt or known architectural violations.… | GAP | #108 (tech-debt register) |
| 22 | Configuration parameters and their defaults are documented.… | GAP | #91 #108 |
| 23 | Configuration is validated at startup and fails safely when invalid or incomplete.… | GAP | #97 |
| 24 | Configuration is separated from code.… | PARTIAL | env-based; #91 |
| 25 | Infrastructure is reproducible from version-controlled definitions where practical.… | PARTIAL | #91 |
| 26 | All runtime and build dependencies are explicitly declared.… | PARTIAL | #105 |
| 27 | Dependencies are pinned/version-constrained per policy, with manifests and lockfiles committed and reproducibl… | GAP | #105 |
| 28 | New or existing critical dependencies have a justified purpose, active-maintenance signal, compatible license … | GAP | #73 #105 |
| 29 | Third-party dependency licenses are recorded and compliant with project policy.… | GAP | #73 |
| 30 | Dependency, container, infrastructure and build-tool vulnerability scans (including license restrictions) pass… | PARTIAL | #71 #73 |
| 31 | Container base images and external artifacts are version-pinned and scanned where applicable.… | GAP | #50 #105 |
| 32 | Build and deployment artifacts are traceable to a specific source commit, build workflow, dependency set, rele… | GAP | #105 |
| 33 | A software bill of materials (SBOM) is generated for release/production artifacts where required.… | GAP | #114 |
| 34 | Release artifacts are signed or otherwise integrity-verifiable where the deployment model supports it.… | GAP | #105 |
| 35 | Authentication and authorization requirements are enforced, with authorization checked server-side at every re… | PARTIAL | #61/#27 closed; #63 #103 open; UNKNOWN for rest |
| 36 | Inputs are validated, normalized and safely encoded.… | GAP | #99 |
| 37 | Secrets are retrieved at runtime from an approved secret-management mechanism and are never committed, logged … | PARTIAL | #65 #104 #97 |
| 38 | Least-privilege access is used for services, users, containers, databases, cloud resources and runtime permiss… | GAP | #51 #68 #103 |
| 39 | Sensitive data is encrypted in transit and at rest where required.… | GAP | #107 #54 |
| 40 | Security-sensitive operations are auditable.… | GAP | #98 |
| 41 | Logs and telemetry never emit passwords, keys, tokens, cookies, authorization headers, raw request/response bo… | GAP | #93 #94 |
| 42 | Metric labels and trace attributes avoid raw identifiers, user data, unbounded endpoint values or other high-c… | N/A | no metrics/traces exist yet; revisit with #113 |
| 43 | Security scanning has been completed with no unresolved release-blocking findings.… | PARTIAL | #71 |
| 44 | The pipeline fails the PR if prohibited values such as secrets or sensitive payload fields are detected in app… | GAP | #106 (gitleaks) + #93 test |
| 45 | Security, privacy, infrastructure, data and release changes receive the appropriate reviewers.… | GAP | #106 |
| 46 | Unit tests cover changed behavior, edge cases and regression scenarios.… | PARTIAL | #101 |
| 47 | Integration tests cover applicable component boundaries.… | PARTIAL | smoke-test exists; #101 |
| 48 | Contract tests cover externally consumed interfaces where applicable.… | GAP | #100 |
| 49 | Migration tests cover schema/data transitions, including representative data volumes and failure scenarios.… | GAP | #98 |
| 50 | End-to-end tests cover critical business paths where applicable.… | PARTIAL | guac e2e + smoke exist; no student-flow e2e UNKNOWN |
| 51 | Failure, timeout, retry, cancellation and idempotency behavior is tested.… | PARTIAL | #70 #102 |
| 52 | Negative/security tests cover relevant attack and misuse cases.… | PARTIAL | #93 #99 #94 |
| 53 | Concurrency/race behavior is tested where applicable.… | PARTIAL | #28 closed; coverage UNKNOWN |
| 54 | Critical-path performance remains within the agreed budget; performance regression testing is performed where … | GAP | #89 |
| 55 | Load/stress/soak testing is performed when this specific change can affect capacity or stability.… | GAP | #89 |
| 56 | Tests are deterministic and do not depend on uncontrolled external state.… | MET | #42 closed |
| 57 | Test data does not contain inappropriate production-sensitive information.… | PARTIAL | only public samples today (user, 2026-10-06); real radiologist data -> #87 anonymization gate must precede ingestion |
| 58 | Dependency upgrades are tested for compatibility, migration requirements and rollback safety.… | GAP | #106 dependabot |
| 59 | Scaling behavior (capacity limits, autoscaling or manual-scaling guidance, overload behavior) is understood an… | GAP | #68 #89 |
| 60 | Health, readiness and liveness checks accurately reflect operational state.… | GAP | #69 |
| 61 | Database migrations are versioned, tested, repeatable, resumable, idempotent where possible, and observable, w… | GAP | #98 |
| 62 | Migration rollback or recovery strategy is documented where rollback is technically possible or meaningful.… | GAP | #98 |
| 63 | Destructive data changes require explicit approval, backup verification and restoration validation.… | GAP | #98 #85 (CLAUDE.md stop condition not enforced) |
| 64 | Data validation rules prevent corruption at ingestion; corrupt, missing, duplicated and partially processed re… | GAP | #99 #96 |
| 65 | Events and messages have versioned schemas and compatible consumer behavior.… | N/A | no event/message bus in architecture |
| 66 | Data changes do not silently alter historical reporting, audit trails or business calculations.… | GAP | #96 (critical) |
| 67 | Reprocessing/replay behavior is defined for data-processing systems.… | N/A | no data-processing/replay pipeline; grading is request/response |
| 68 | Deployments do not leave application and database versions in incompatible states; migrations are compatible w… | GAP | #98 #105 |
| 69 | Errors use the project's stable taxonomy and safe client-facing responses, while internal causes are preserved… | PARTIAL | #74 closed; #93 #95 open |
| 70 | Timeouts are explicitly configured for network/external operations; retries are bounded, use backoff/jitter an… | GAP | #70 #102 #67 #104 |
| 71 | Side effects are idempotent or have a documented compensation/recovery path.… | PARTIAL | #60 closed; #104 #102 open |
| 72 | Resource exhaustion, retry storms, unbounded queue/memory growth and cascading-failure risk are understood and… | GAP | #93 #68 #66 |
| 73 | Rate limiting/backpressure is implemented where required.… | PARTIAL | nginx limit on /api only; :8043 unlimited -> add to #103 |
| 74 | Graceful shutdown and startup-recovery behavior is implemented and understood where applicable.… | UNKNOWN | not assessed -> #116 — owner: implementer (pi) via #116; verdict re-check when #116 closes, by 2026-11-15 |
| 75 | The service has a documented degraded/failure mode for when full functionality cannot be maintained.… | GAP | #115 #88 |
| 76 | Resource limits, timeouts, concurrency limits and rate limits are defined so the system cannot consume resourc… | GAP | #68 |
| 77 | The system can be safely paused, drained, restarted and scaled where applicable.… | GAP | #88 |
| 78 | Metrics, traces and structured logs exist for new critical paths, with trace/log correlation propagated across… | PARTIAL | #72 closed; #95 open |
| 79 | Important state transitions and business-critical events are observable.… | GAP | #98 |
| 80 | Metrics use documented names, units and low-cardinality attributes.… | N/A | no metrics yet; revisit with #113 |
| 81 | Logs are structured with timestamp, severity, service/component, environment and correlation ID; error logs in… | PARTIAL | #95 + add service/env fields, ms timestamps |
| 82 | Telemetry follows the project's OpenTelemetry conventions where OpenTelemetry is used.… | N/A | OpenTelemetry not used |
| 83 | Every externally visible API operation, scheduled job, background worker, alert-evaluation path and notificati… | N/A | no OTel wrapper; see #113 |
| 84 | New configuration settings, alert rules, queues, external dependencies and database migrations include appropr… | N/A | no alerting/queues; revisit with #113 |
| 85 | Collection/ingestion telemetry covers polls/events received, success/failure, duration, queue depth, freshness… | N/A | no ingestion/collection pipeline in this product |
| 86 | Processing telemetry covers events processed, latency, error/retry count, backlog age, worker utilization; par… | N/A | no event-processing pipeline |
| 87 | Storage telemetry covers read/write latency, error rate, connection-pool saturation, storage size, migration d… | GAP | #113 (SQLite latency/lock signals, #70) |
| 88 | API/UI telemetry covers request count, latency histogram, 4xx/5xx count, concurrent sessions; incoming request… | GAP | #113 |
| 89 | Alert-evaluation telemetry covers rules evaluated, evaluation latency, alerts fired/resolved/suppressed, stale… | N/A | no alert-evaluation engine |
| 90 | Notification telemetry covers notifications queued/sent/failed, delivery latency, retry count, dead-letter que… | N/A | no notification system |
| 91 | Background-worker telemetry covers active workers, job throughput, queue depth, oldest-job age, crash/restart … | N/A | no background workers/queues |
| 92 | A required observability-contract CI job validates that code and infrastructure changes follow the documented … | N/A | depends on telemetry contract; revisit after #113 |
| 93 | CI exports workflow/job telemetry (queue time, run/job duration, pass/fail rate, retry count, flaky-test rate,… | N/A | justify: single-maintainer GitHub CI; revisit later |
| 94 | Every PR workflow run can be correlated by repository, PR number, commit SHA and GitHub run ID; CI duration an… | N/A | as 93 |
| 95 | Core collection, processing, storage, alerting and notification paths are confirmed to emit metrics, logs and … | N/A | paths 'collection/alerting/notification' absent; API/storage covered by #113 |
| 96 | An automated telemetry smoke test runs in the ephemeral environment: deploys the PR version with telemetry exp… | N/A | as 92 |
| 97 | Collection freshness is recorded by the telemetry smoke test.… | N/A | no collection |
| 98 | A complete trace exists from collection/ingestion through processing and persistence.… | N/A | no ingestion trace |
| 99 | Failed work increments an error metric and produces an error-status span.… | N/A | revisit with #113 |
| 100 | Retry activity is visible and bounded.… | N/A | revisit with #113 |
| 101 | Alert evaluations record duration and outcome.… | N/A | no alerting |
| 102 | Notification failures are detectable rather than silently ignored.… | N/A | no notifications |
| 103 | Logs for the synthetic test can be correlated to the relevant trace.… | N/A | revisit with #113 |
| 104 | Dashboards and alerts exist for stale collection, queue backlog, worker failure, alert-evaluation failure and … | GAP | #113 (stale/failed-health dashboards; scope-reduced) |
| 105 | Production deployment uses the same artifact that passed verification; the artifact version is immutable and i… | GAP | #105 |
| 106 | Rollback procedure is tested and documented.… | GAP | #85 #88 |
| 107 | Configuration changes are versioned and auditable.… | GAP | #91 |
| 108 | Feature flags have an owner, purpose, default state, expiry/review date and removal plan.… | N/A | no feature flags used (user, 2026-10-06); define owner/expiry rule if ever introduced |
| 109 | Deployment failure does not leave the system in an undefined state.… | GAP | #105 #88 |
| 110 | Release notes/changelog are updated where applicable.… | GAP | #100 #108 (CHANGELOG missing) |
| 111 | Background jobs, queues, schedulers and workers expose backlog, age, retry and failure signals.… | N/A | no queues/workers |
| 112 | The system exposes signals for dependency failure, resource saturation, stale data, queue backlog and recovery… | GAP | #113 |
| 113 | Containers run with appropriate user, filesystem, capability and network restrictions.… | GAP | #51 #68 #103 |
| 114 | Change-management requirements are followed for production-impacting or regulated changes.… | GAP | #90 #106 |
| 115 | Documentation (README/architecture/API/operational docs) is updated in the same PR when behavior, configuratio… | PARTIAL | #108 #100 |

## Organizational (35)
| # | Item | Verdict | Ticket / note |
|---|---|---|---|
| O1 | The service/component has a named owner, business owner, technical owner, support/on-call contact and escalati… | GAP | #86 |
| O2 | Reliability/operational ownership (on-call responsibility) is explicitly assigned.… | GAP | #86 |
| O3 | Every major external dependency has an identified owner and documented purpose.… | GAP | #86 #91 (Kasm CE licence) |
| O4 | The repository defines code ownership and review requirements for sensitive areas.… | GAP | #106 |
| O5 | A service-catalog record (e.g. SERVICE.md) identifies owner(s), criticality tier, data classification, depende… | PARTIAL | #86 |
| O6 | Known technical debt has an owner and priority.… | GAP | #86 #108 |
| O7 | A deprecation policy exists for obsolete interfaces/features.… | GAP | #117 |
| O8 | An end-of-life/retirement procedure exists for services.… | N/A | MVP, no retirement planned |
| O9 | Production incidents feed improvements back into the system (postmortem / continuous-improvement loop).… | GAP | #88 |
| O10 | Known security exceptions have documented owners, risk acceptance and expiration/review dates.… | PARTIAL | .hadolint.yaml rationale exists; #86 for register |
| O11 | Operational runbooks exist for common failures, degraded dependencies, stuck work, rollback and recovery.… | GAP | #88 |
| O12 | Alerts are actionable, have an owner, link to an appropriate runbook, and change as operational behavior chang… | GAP | #88 #89 |
| O13 | CI/CD workflows use least-privilege permissions and pinned third-party actions or verified action digests.… | GAP | #67 |
| O14 | The CI/CD pipeline is deterministic and reproducible, and deployment is automated where practical.… | PARTIAL | #67 #105 |
| O15 | Infrastructure drift is detectable.… | GAP | #91 |
| O16 | Production changes are auditable.… | GAP | #98 #106 |
| O17 | Backups exist for critical data, with an owner, retention policy and encryption/access policy.… | GAP | #85 |
| O18 | Restore procedures are tested at a defined interval; a backup is not considered valid until restoration succee… | GAP | #85 |
| O19 | Recovery time objective (RTO) and recovery point objective (RPO) are defined for critical services and data.… | GAP | #85 #89 |
| O20 | Failover, restart, replay and recovery behavior is documented and exercised at an appropriate frequency for ap… | GAP | #85 #88 |
| O21 | Load, stress, soak or resilience tests cover the highest-risk workloads on a scheduled (not just per-change) b… | GAP | #89 |
| O22 | Capacity assumptions have an owner and review mechanism.… | GAP | #89 |
| O23 | End-of-life, unsupported, unmaintained or deprecated dependencies (and runtime/base-image versions) are tracke… | GAP | #73 #106 |
| O24 | External API contracts, quotas, rate limits, authentication rotation and deprecation timelines are monitored.… | GAP | #86 #117 (Kasm/Guacamole licensing) |
| O25 | A dependency removal or replacement path exists for high-risk or business-critical dependencies.… | GAP | #117 |
| O26 | Vendor lock-in, data export, data portability and exit risks are assessed for critical third-party services.… | GAP | #117 |
| O27 | Critical user/system journeys have defined SLIs and appropriate SLOs for production-critical services.… | GAP | #89 |
| O28 | Availability, latency, throughput or freshness objectives are measurable.… | GAP | #89 |
| O29 | Alert thresholds are linked to operational objectives rather than arbitrary infrastructure values.… | GAP | #89 |
| O30 | Error-budget behavior is defined for mature production services.… | N/A | immature service; revisit after SLOs |
| O31 | The README explains purpose, architecture, local setup, configuration, test commands, deployment and troublesh… | PARTIAL | #108 |
| O32 | Known limitations, operational risks and deferred work are tracked centrally rather than hidden in tribal know… | GAP | #86 #108 |
| O33 | Repositories are assigned a criticality tier (1: internal/low-risk, 2: standard production, 3: business-critic… | PARTIAL | #86 (tier: 4 label exists; not in CLAUDE.md) |
| O34 | Lower-risk projects may use lighter controls, but must document why a control does not apply rather than silen… | PARTIAL | this mapping documents N/A items |
| O35 | The golden standard is built on recognized external references (ISO/IEC 25010 quality taxonomy, NIST SSDF secu… | N/A | informational |
