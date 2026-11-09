# Testing proposal assessment (#103)

## Evidence and current status

Reviewed default-branch commit `ec0b7031005ac500619555eea6f49760a4fd2f9f`, #103,
#122 and comments, #195's prerequisite comment, #238 and its decisions, #89,
and current open PRs. The supplied URL is an issue comment, not a PR. No open
#103 implementation PR existed. #274 is the separate #238 B2 scope-regression
PR; this change does not extend its authorization scope or duplicate it.

The default branch's [CI run](https://github.com/xWuWux/DICOM_app_viewer/actions/runs/40480131777)
failed lint: six monitoring images lacked immutable digests, and monitoring Python
changes had unused/unsorted imports and formatting errors. Downstream tests were
skipped, and `ci-success` correctly failed. Successful CodeQL checks are not a
successful application pipeline. This PR pins those exact monitoring versions
against registry manifest bytes and corrects the lint errors without relaxing
any gate. Full local tests also exposed two existing monitoring-workflow
hardening failures (unrestricted default permissions and a mutable checkout tag),
and 94.47% API coverage below the required 95%. This patch fixes those workflow
settings, its wrong `main` push branch and absent Compose validation credentials,
and adds disabled/error/repeated-setup/shutdown telemetry tests without reducing
the coverage floor. The first PR CI run then passed lint but exposed an incomplete
OpenTelemetry dependency lock during the image build (`protobuf` was unpinned).
The lock is regenerated with Python 3.12 and pip-tools 7.5.0, preserving existing
pins and adding the missing transitive dependencies. A clean-resolution
`pip install --dry-run --ignore-installed --require-hashes` verifies this boundary;
the existing image-build gate remains mandatory.

Existing tests include browser axe/console gates, a watermark contrast baseline,
Hypothesis state invariants, degraded-mode restart/recreate journeys, SQLite
shutdown/crash recovery, log-leak checks and Guacamole browser smoke. The grading
database now uses WAL/FULL; the older review's WAL/NORMAL finding is stale.
There is no Kafka or `ephemeral-deploy-benchmarks` job in the checked repository.

## Specific problems and rewritten recommendations

| Quoted claim / section | Problem, context or edge case | Replacement |
|---|---|---|
| "solid foundation (security, contracts, Kafka E2E)" | Unsupported general assurance; Kafka is absent. Current CI is red and legacy Orthanc reads remain. | "Start from the verified repository and current CI result. Enumerate the remaining security boundaries; do not infer production readiness from test names." |
| "tests ... completely missing" | Incorrect for accessibility and restart/recovery; duplicates work and obscures the actual gaps. | "Map each proposed test to existing coverage, known exclusions and the missing behavior. Extend an existing job when it owns that behavior." |
| "several hundred to several thousand concurrent users" | Different from the recorded 50-session target. API virtual users do not model JVM rendering, guacd or streamed pixels. | "Measure 50 simultaneous doctor workflows and an agreed reserve on representative lab hardware. Test API throughput separately from desktop capacity and simultaneous study loading." |
| "nightly builds" / "every test deployment" | Conflicting cadence; #238 explicitly defers scheduled/full-scale runs until a self-hosted runner exists. | "Keep bounded regressions on PRs. Use an explicitly approved disposable self-hosted environment for full-load/soak runs; do not add a schedule yet." |
| "average response time does not exceed 200 ms" | Invented threshold and a mean hides slow-tail users; ignores failed/429 requests and operation differences. | "Agree per-operation success/error rates and p95/p99 targets under a defined workload. Use three measured runs for #238 H before choosing a PR threshold; do not relax rate limits silently." |
| "SAST ... (CodeQL, osv-scanner) ... works great" | CodeQL checks exist, but no evidence supports the blanket quality claim or this specific osv-scanner pipeline. A green scanner does not prove authorization. | "Report the actual checks and findings. Add targeted live authorization and port tests with authorized positive controls." |
| DAST "searches for ... SQL injection, XSS" | Generic crawling may never enter authenticated desktop/grade states, may mutate data, and cannot prove study isolation. | "Run bounded authenticated scans only on disposable public-fixture stacks, with logout/revocation and explicit endpoint scope. Prioritize cross-session negative tests and fail-closed errors; track scan coverage, not just a green scan." |
| "Since your application uses Kafka ... distributed system" | Wrong dependency and architecture inference. Kafka outages and pod restarts are not this stack's failure model. | "Exercise Orthanc, grading API/SQLite, Guacamole, PostgreSQL, guacd and desktop/network failures. Assert recovery and persisted acknowledgements, not merely process health." |
| "database switches from the master to a replica" | No such database topology is established; local SQLite has no replica role. | "Distinguish process restart, same-volume recovery, disk loss and host loss. Test restore separately; add replica tests only after selecting and implementing replication." |
| "automatically ... meets WCAG standards" | Automated checks cover only some criteria. Browser axe cannot inspect the semantics of a remote desktop drawn as canvas pixels. Existing baseline exceptions matter. | "Keep the axe regression gate, review contrast exceptions, and add manual keyboard, focus, screen-reader and streamed-desktop usability checks. Do not claim WCAG conformance from automation alone." |
| "If the tests still pass ... they're useless" | A surviving mutant can be equivalent, unreachable or outside the selected contract. Mutation scores are not correctness proofs. | "Use bounded mutations around security and grading invariants, classify survivors and improve missing assertions. Preserve the existing Hypothesis tests and 95% floor." |
| "start with k6 ... ephemeral-deploy-benchmarks" | Nonexistent integration point and poor immediate priority while a known exposure and red CI exist. | "First restore CI and enforce port/default-credential contracts. Keep load testing under #238 H/I and #122 with measured workloads and an approved runner." |
| Earlier comment: "publish only the HTTPS entry point" | Good target, but ambiguously presented next to incremental fixes; requires a real TLS/WebSocket configuration and management path. | "This PR fixes loopback defaults and bootstrap. The HTTPS-only target is a separate acceptance milestone; public production is not certified by this PR." |
| Earlier comment: "A desktop cannot reach another desktop" | A shared bridge does not enforce this; the flow diagram shows callers, not network isolation. | "Define a source/destination/port matrix, implement network enforcement, and test from two real desktop containers before claiming lateral isolation." |
| Earlier comment: "Required CI gates fail if Docker/Compose is unavailable" | Existing skip-based checks and shell process substitution can hide missing prerequisites. | "Make the resolved-port checker a non-skipping lint command. Detect an unsupported lint shell environment explicitly; do not report green when `/dev/fd` loops did not execute." |
| Broad lists of tools before acceptance criteria | Mixes requirements, implementation choices and deployment topology; repeated assurances make review harder. | "Organize each change as risk -> invariant -> test -> evidence -> remaining limit. Keep one independently reviewable behavior scope per PR." |

## Administration decisions and consequences

- #103 clarification permits a single Docker host and selects Guacamole/Weasis.
  This simplifies the port boundary, but does not replace #122's capacity/recovery
  planning. A single-host pilot and a future four-host installation are different
  deployment stages, not interchangeable capacity claims.
- #122 references the lab's Guacamole, desktop recreation and no mandatory
  Ceph/ZFS decisions. Disposable desktops simplify recovery but do not recreate
  central databases or Orthanc data. Alternative: tested backup restoration with
  agreed data loss/outage, or replication if the eventual objectives require it.
- #238: existing committed DICOM fixtures, approximately 40-minute PR budget,
  no scheduled full-load runs until a self-hosted runner exists. This patch uses
  the existing integration job; full-scale evidence remains a lab release gate.
- #195 explicitly requires non-conforming-UID/caller evidence before legacy
  closure. Keep that sequencing; do not convert an unresolved exposure into an
  accepted production exception. No real patient data is needed for these tests.
- RTO/RPO and reserve percentages are still proposals awaiting a decision in this
  scope. Measure demand and failure recovery, then approve numbers. More spare
  RAM alone cannot make a lost single host available.

## Prioritized essential changes

1. Restore required CI and enforce exact port/default-credential contracts, with
   negative controls and real fresh/existing-volume authentication tests.
2. Complete scoped image authorization and desktop/backend isolation, preserving
   the explicit non-conforming-UID prerequisite before legacy route closure.
3. Prove the full doctor journey through Guacamole, including saved grades and
   reconnect; nonblank canvas and API-only tests are insufficient.
4. Agree recovery objectives and demonstrate database/Orthanc/config restoration
   on a clean destination; separate this from same-volume restart evidence.
5. Measure the actual 50-session workload and reserve on lab hardware, then set
   latency/error/soak thresholds. Add targeted DAST/mutation work where it closes
   a demonstrated gap, not to satisfy a tool checklist.
