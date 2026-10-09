# Red Team task list — owner-authorised assessment of this stack

Status: DRAFT, MVP stage. Companion to `SECURITY_RED_TEAM.md` (threat model,
verification discipline, what is out of scope and why). This file is the
**task list**; that one is the **checklist of claims**.

**Red Team, as scoped here:** offensive-security specialists simulating real
attacks against this system — penetration testing, social engineering, phishing,
physical entry — to find gaps and establish **how far an intruder can actually
get**.

**Authorisation.** This is an assessment of the owner's own on-premise
infrastructure, authorised by the owner. Rules of engagement, testing windows,
scope boundaries, emergency stops, escalation contacts and assignees are
**deliberately not in this file** — this repository is public, and that material
belongs in the private IP_CMC document. A task that cannot be described without
naming a real host, account or person does not belong here at all.

**Ground rules, from `CLAUDE.md` (these override any task below):**
- Never run a task against real patient data without the owner's written go.
  Sample data only: `scripts/fetch-public-samples.sh` +
  `scripts/load-sample-studies.sh`.
- No cloud/AppStream/S3 dependency may be introduced to make a test easier.
- Never disable or bypass the watermark except as the explicit subject of an
  RT-T-05/RT-T-06 measurement, and never in a shared environment.
- No destructive DB operation without explicit confirmation.
- Prefer self-tearing-down harnesses (`scripts/smoke-test.sh`,
  `scripts/test-guacamole-integration.sh` are the existing models).

**Evidence handling:** captured DICOM, PHI, tokens, coordinates of the real
deployment and console screenshots go to the private location, referenced here by
hash. **Never commit them.** Prefer a response code + byte count + SHA-256 over a
payload.

Legend: **[hypothesis]** = plausible, untested, must not be reported as a
finding. **[to verify]** = a check to perform, not an assertion.
Severity is two-axis, per `SECURITY_RED_TEAM.md` §4:
**EI** = exam integrity, **PHI** = patient-data exposure (H/M/L/—).

---

## A. Penetration testing (technical)

Order matters: **RT-T-01 and RT-T-04 are first** — cheap, and RT-T-01's answer can
change the deployment.

### RT-T-01 — Enumerate cases the session was never issued
- **Scenario:** a student, mid-exam, wants to see which cases exist and which are
  coming. **[hypothesis]** the Orthanc read surface is not scoped per session.
- **Target:** `docker/viewer/default.conf.template` `location /` → Orthanc.
- **Steps:** from inside a real session (both flows), as the student, request
  through the **proxy**: `GET /studies`, `GET /patients`,
  `GET /dicom-web/studies`. Compare the returned StudyInstanceUID set against the
  single `orthanc_study_uid` this session's `GET /api/case` returned.
- **Success:** any study not returned by `/api/case` is listed.
- **Evidence:** proxy response codes + study counts + UIDs' SHA-256 (not the UIDs
  of real cases); the session's own stage/position from `/api/case`; the flow used.
- **Severity:** EI **H** · PHI **M**.
- **Baseline known:** enumeration of all studies was observed **directly against
  Orthanc** (`SECURITY_RED_TEAM.md` §3.3); through the proxy it is inferred only.
- **Test to add:** `scripts/test-orthanc-proxy-scope.sh` — bring the stack up with
  ≥2 sample studies, assert through the proxy exactly the behaviour the owner
  decides is intended (today it would fail and record the gap; that is the point).

### RT-T-02 — Bulk retrieval of a whole study
- **Scenario:** one request should not be able to hand over an entire study.
- **Target:** proxy → `GET /studies/{id}/archive`, `/studies/{id}/media`,
  `/patients/{id}/archive`, `/series/{id}/archive`.
- **Steps:** through the proxy, for a study the session was not issued: request
  each route; record code, byte count, SHA-256; open one ZIP in a scratch dir and
  list its members (do not view pixels into a shared display).
- **Success:** a ZIP containing a full study's instances comes back.
- **Evidence:** codes, sizes, hashes, member count, which routes are absent/404.
- **Severity:** EI **H** · PHI **H**.
- **Baseline known:** `/studies/{id}/archive`, `/studies/{id}/media` and
  `/patients/{id}/archive` returned ZIPs **directly against Orthanc**;
  `/series/{id}/archive` did **not** complete (bad id in the harness) — verify it.
- **Test to add:** same script as RT-T-01, archive section.

### RT-T-03 — Raw DICOM + tag exposure
- **Scenario:** confirm what leaves with a raw file — images plus the tag set as
  ingested, which is where a pseudonymisation mistake becomes PHI exposure.
- **Target:** proxy → `GET /instances/{id}/file`, `GET /instances/{id}/tags`.
- **Steps:** fetch one instance file and its tags; enumerate present identifying
  tags (`PatientName`, `PatientID`, `PatientBirthDate`, `StudyDate`, Accession,
  institution); check for `DeidentificationMethod`.
- **Success:** any tag the de-identification sign-off (issue #87) does not cover
  is present.
- **Evidence:** tag names only — **never values**; presence/absence table per tag.
- **Severity:** EI L · PHI **H**.
- **Baseline known:** on sample data, `PatientName`, `PatientID`,
  `PatientBirthDate`, `StudyDate` were present via `/instances/{id}/tags`; no
  `DeidentificationMethod` is declared in real batches (README).
- **Test to add:** `scripts/test-dicom-tag-hygiene.sh` (sample-data fixture with a
  deliberately dirty file → assert the checker catches it), next to
  `scripts/dicom-patient-consistency.py`.

### RT-T-04 — Write/delete against the DICOM store
- **Scenario:** a session deletes, uploads or reconfigures the shared store — the
  one failure that breaks a live cohort mid-exam.
- **Target:** proxy, all methods; issue #64's method filter.
- **Steps:** through the proxy attempt `DELETE /studies/{id}`,
  `POST /instances`, `POST /tools/delete-all-associations`, `PUT /changes`,
  `POST /tools/bulk-export`, `POST /tools/create-archive`, `POST /system`
  (restart), `POST /modalities`, `PUT /settings`. Confirm `POST /tools/find` still
  works (it must — browsing depends on it).
- **Success:** any write/delete accepted.
- **Evidence:** method+path+code matrix; `GET /statistics` before/after.
- **Severity:** EI **H** · PHI M (deletion is availability, not disclosure).
- **Baseline known:** config denies non-GET/HEAD except `/tools/find`; **never
  tested through the proxy** → run this as a regression that should PASS.
- **Test to add:** `scripts/test-orthanc-method-lockdown.sh`, wired into the CI
  Security Scan / Integration stage like the other `scripts/test-*.sh`.

### RT-T-05 — Watermark bypass / capture in the overlay gap
- **Scenario:** produce an unwatermarked image of a case, or a crop that excludes
  all watermark tiles.
- **Target:** `docker/kasm-workspace-weasis/overlay.py` + `watchdog.sh` + picom;
  `docker/viewer/watermark.html` DOM watermark for the Chrome flow.
- **Steps:** (a) kill the overlay and capture during the relaunch gap; (b) kill
  picom and test whether the shape fallback stays legible on a white CT window;
  (c) at the real session resolution, extract a full image from a screenshot
  region containing no tile; (d) resize / minimise / second monitor; (e) in the
  Chrome flow, DevTools `display:none`, zoom, fullscreen.
- **Success:** one captured frame with no legible `STUDENT_ID | SESSION_ID |
  timestamp`, or a frame where an entire image is visible and no tile is present.
- **Evidence:** the captures (private store, hashed), measured gap duration, tile
  count per screen, resolution/DPI used.
- **Severity:** EI **H** · PHI M (an unwatermarked capture is also an
  untraceable one).
- **Baseline known:** `watchdog.sh` promises relaunch in ~1 s, backoff capped at
  5 s (issue #156); picom + shape fallback from issue #150; window-keeping from
  issue #151. Nothing here measured.
- **Test to add:** extend `scripts/test-overlay-compositor.sh` /
  `scripts/test-overlay-shape.sh` with the *deterrence* claim itself — capture
  during a forced kill and assert a tile is present — plus a tile-coverage
  assertion at the deployment's real resolution.

### RT-T-06 — Chrome kiosk containment
- **Scenario:** leave the pinned page to reach the origin directly.
- **Target:** `docker/kasm-workspace/custom_startup.sh` (`--kiosk`,
  `--app=…`; its header claims the student cannot navigate away, open a tab, or
  reach devtools/downloads).
- **Steps:** try typed URLs / `view-source:` / `file://`, keyboard shortcuts
  (F12, Ctrl+Shift+I, Ctrl+U, Ctrl+L, Ctrl+O), right-click, `chrome://` pages,
  downloads, external protocol handlers, and whether any other origin is
  reachable from the session network.
- **Success:** any navigation outside the pinned page, a devtools console, or a
  download.
- **Evidence:** what was reachable, with screenshots; the flag list as launched
  (`/proc/<pid>/cmdline`).
- **Severity:** EI **H** · PHI M.
- **Baseline known:** **[hypothesis]** — the claim is a script comment; the
  "can't reach devtools/downloads" behaviour has no test.
- **Test to add:** a Playwright/`bats` pair in the `scripts/test-guacamole-integration.sh`
  style that asserts the effective flag set on the built image, so a Dockerfile or
  flag edit cannot silently loosen kiosk.

### RT-T-07 — Copy / export / DLP egress
- **Scenario:** pixels out of the session to the student's own device.
- **Target:** Weasis native export/import/send/Q-R; KasmVNC rich-clipboard DLP;
  **Kasm admin-console Group-level DLP toggles**; Kasm file transfer; any
  screenshot/archive binary present in the image.
- **Steps:** re-check each Weasis control in a live session; verify the
  admin-console Group toggles against a real Kasm (CI cannot — named gap in
  `scripts/test-copy-lockdown.sh`); attempt clipboard text + image out and file
  download out; enumerate installed binaries able to grab the screen or write an
  archive in the **built image**.
- **Success:** any DICOM/pixel artifact on the client side, or an installed
  grabber that CI assumed absent.
- **Evidence:** artifacts (private), toggle states as configured, the installed
  binary list from the image.
- **Severity:** EI **H** · PHI **H**.
- **Baseline known:** `scripts/test-copy-lockdown.sh` +
  `scripts/tests/test_copy_lockdown.py` cover Weasis config and the clipboard gap;
  admin-console Group toggles are explicitly out of CI scope.
- **Test to add:** image audit in `scripts/test-copy-lockdown.sh` — assert the
  built `docker/kasm-workspace-weasis` image contains no screenshot/archive/transfer
  binary beyond an explicit allowlist (a `command -v` loop is CI-able and cheap).

### RT-T-08 — Session token: replay, forwarding, revocation
- **Scenario:** someone else grades the exam, or a leaked link stays live.
- **Target:** `#token=` fragment → `sessionStorage` → `X-Grading-Token` header
  (issues #94, #145); `POST /session/revoke`; `GRADING_TOKEN_TTL_SECONDS`.
- **Steps:** (a) forward a fresh link to a second machine/browser and complete a
  stage; (b) use the link after `POST /session/revoke`, including a submit in
  flight; (c) replay a captured `X-Grading-Token` from curl, a second tab, and a
  second browser; (d) set TTL to the 7-day max and confirm the session still works
  after 24 h; (e) confirm a second concurrent session on the same `student-id`
  (`scripts/create-session.py` minted twice) does not cross state.
- **Success:** a stage completed by a different browser after forwarding; any
  accepted request after revoke; state visible across two sessions of one id.
- **Evidence:** timestamps, per-attempt HTTP codes, which stage/position was
  reached, token fingerprints (hashes, not values).
- **Severity:** EI **H** · PHI L (a forwarded link still shows the same case).
- **Baseline known:** `docker/grading-api/tests/test_session_revoke.py`,
  `docker/grading-api/tests/test_token_transport.py`,
  `docker/viewer/tests/test_session_token.py` exist; none covers forwarding between
  machines, which is the realistic abuse.
- **Test to add:** replay-after-revoke + two-browser assertions in
  `docker/grading-api/tests/test_session_revoke.py`; a concurrency/`student-id`
  collision test alongside `scripts/tests/test_create_session.py`. Decide in
  writing whether forwarding is accepted risk — if accepted, record that instead
  of writing a test.

### RT-T-09 — Credential hygiene in logs, temp files and inspect output
- **Scenario:** a token, `ORTHANC_PASSWORD` or `GRADING_COORDINATOR_KEY` lands
  somewhere readable and outlives the session.
- **Target:** nginx access **and** error logs, `docker inspect` env, compose
  output, `/tmp` (watchdog log), anything `overlay.py` / `watchdog.sh` write,
  process cmdline, `docker/grading-api/app/logging_config.py` output.
- **Steps:** drive a full real session, then grep every log/file/env dump for a
  sentinel token + sentinel password; repeat for the **Weasis** flow and the
  Guacamole flow, not only Chrome; repeat with a deliberately malformed
  `local/cases.json` and a forced 500, and read what the error path prints.
- **Success:** any sentinel value found anywhere.
- **Evidence:** file path + line number only, never the matched value.
- **Severity:** EI M · PHI **H** (Orthanc's credential is the store-wide one).
- **Baseline known:** `scripts/test-log-leak.sh` covers #93/#94 sentinels; issue
  #94's own comment records that a query-string token was reproduced in *two* log
  files, so this class is proven-live in this project.
- **Test to add:** extend `scripts/test-log-leak.sh` to both flows plus the
  error-path cases, and make CI run it (verify whether it is wired in at all).

### RT-T-10 — Container escape and post-session persistence
- **Scenario:** from inside a session to the host, then to the DICOM volume; or
  artifacts that outlive logout.
- **Target:** session container hardening; `docker/guacamole-weasis/seccomp/`;
  `grading-db` volume; `/tmp`; Weasis/X caches.
- **Steps:** confirm at **run time** which seccomp/`--cap-drop`/user namespace
  settings are actually applied (`docker inspect`), not merely present in the
  repo; after logout, sweep the session filesystem for survivors (watchdog log,
  core dumps, Weasis cache, X auth); confirm no student free text reaches
  `grading-db`.
- **Success:** a file surviving logout, or a runtime seccomp state that differs
  from the committed profile.
- **Evidence:** `docker inspect` security block, before/after file listings.
- **Severity:** EI M · PHI **H**.
- **Baseline known:** `scripts/tests/test_seccomp_profile.py` validates the
  Guacamole profile file; whether the **Kasm** path applies anything at run time is
  **[hypothesis]**, and CLAUDE.md flags Guacamole as not Kasm-parity.
- **Test to add:** a runtime counterpart asserting `docker inspect` matches the
  committed profile, next to `scripts/tests/test_seccomp_profile.py`.

### RT-T-11 — grading-api input abuse
- **Scenario:** push the API past its categorical model.
- **Target:** `POST /submit`, `POST /reset`, `GET /results`, `GET /case`.
- **Steps:** oversized bodies; wrong types; a free-text grade where Lung-RADS
  0/1/2/3/4A/4B/4X is expected; `stage` mismatch against server state; case_id
  enumeration to read other cases' feedback; `results` after revoke; a
  `category_options` probe during the learning stage.
- **Success:** any accepted free text persisted, another case's ground truth or
  feedback returned, or a 500 whose body names a value.
- **Evidence:** request, response code, error taxonomy code, whether the DB row
  kept text.
- **Severity:** EI **H** (answer-key feedback leakage) · PHI L.
- **Baseline known:** `docker/grading-api/tests/test_input_validation.py`,
  `test_error_taxonomy.py`, `test_no_case_data_integrity.py` already push here —
  expect mostly PASS; the point is enumeration of *other* cases via `case_id`.
- **Test to add:** cross-case `case_id` enumeration assertions in
  `docker/grading-api/tests/test_api_surface.py`.

### RT-T-12 — Guacamole PoC reachability
- **Scenario:** the less-hardened path is used because it has no concurrency cap.
- **Target:** `docker/guacamole-weasis/`, `docker/guacamole/`, its session links.
- **Steps:** from inside a Guacamole session, try to reach `grading-api` and
  Orthanc outside the intended path, and re-run RT-T-01/02/04 through that route.
- **Success:** any reachability the Kasm flow does not also have.
- **Evidence:** route map from inside the session; codes.
- **Severity:** EI M · PHI **H**.
- **Baseline known:** CLAUDE.md: the Guacamole flow is **not** hardened to Kasm
  parity and must not carry real patient data. This task is what would move it
  toward parity.
- **Test to add:** extend `scripts/test-guacamole-integration.sh` with a
  "no lateral reachability" assertion set.

---

## B. Social engineering and phishing

Scenario-level only, deliberately: no names, roles of real people, hosts,
addresses, lure text, or timing. Details live in the private IP_CMC document.

### RT-SE-01 — Impersonating authority to obtain a session link
- **Scenario:** an attacker poses as course staff to whoever mints links, and asks
  for a link or a reset.
- **Target:** the human link-provisioning process around
  `scripts/create-session.py` / `scripts/mint-local-link.sh`.
- **Steps (scenario):** request a link through the normal support channel from an
  unverified identity; observe whether identity is verified before issue; attempt
  a reset for a third party.
- **Success:** a valid link issued to an unverified requester.
- **Evidence:** whether a written process exists, and the outcome class only
  (issued / refused / escalated). No transcripts in this repo.
- **Severity:** EI **H** · PHI M.
- **Control test:** not code — record in the private doc who may mint links and
  what verification they must perform. Repo-side, keep
  `GRADING_COORDINATOR_KEY` handling (issue #97) out of every shared channel;
  assert no key material in the repo (`scripts/security-scan.sh` already scans,
  confirm it covers the docs/scripts it should).

### RT-SE-02 — Lookalike "your session link" lure
- **Scenario:** a student receives a message that appears to be a session link and
  enters or reveals their token / clicks a similar address.
- **Target:** the link format itself; any redirect or open parameter on the viewer
  origin; `docker/viewer/default.conf.template`.
- **Steps (scenario):** check whether anything on our origin can redirect a
  token-bearing URL to another origin (a fragment never reaches the server, so the
  risk is a redirect, not the fragment); confirm a lookalike page on a foreign
  origin cannot read `sessionStorage` of ours; confirm nothing in the panel renders
  attacker-controlled markup.
- **Success:** a path on our origin that forwards a token or fragment to a foreign
  origin, or markup injection through the panel.
- **Evidence:** the redirect/markup findings with file+line; otherwise a negative
  result recorded as a negative.
- **Severity:** EI M · PHI M.
- **Test to add:** a lint assertion that `default.conf.template` contains no
  `return`/`proxy_pass` built from `$arg_`, `$http_`, `$request_uri` or other
  request-controlled values, in the same cheap-grep style as the existing
  compose-agreement check in `scripts/lint.sh`.

### RT-SE-03 — Link sharing / proxy grading
- **Scenario:** the most realistic attack in this population: a student shares a
  link with a stronger peer, or asks someone else to complete a stage.
- **Target:** the link model; `sessionStorage` scoping (issue #145);
  `student_id`-keyed progress.
- **Steps (scenario):** run RT-T-08(a) as a usability test rather than a protocol
  test — two people, one link, one stage, and observe whether anything about the
  session changes noticeably.
- **Success:** a stage completed by a different person, indistinguishable from the
  owner completing it.
- **Evidence:** attempt log only, no personal data.
- **Severity:** EI **H** · PHI —.
- **Test to add:** none is possible with today's design; the deliverable is the
  owner's **decision** (accept / mitigate with a concurrency or fingerprint signal
  / proctor). If mitigated, the mitigation needs a test; if accepted, this row
  becomes an accepted-risk note.

---

## C. Physical entry

Generic scenario-level, for the same public-repo reason. The stack is on-premise by
hard rule (CLAUDE.md rule 1), so physical access to the machine room *is* a data
breach path, not a theoretical one.

### RT-PH-01 — Unattended operator access
- **Scenario:** an abandoned, still-authenticated operator session (Kasm console,
  host terminal, browser with the coordinator key in hand).
- **Target:** operator workstations and the Kasm admin console.
- **Steps (scenario):** approach an unattended console and see what is reachable
  without any credential entry — console session, host shell, docker socket,
  minted-link history.
- **Success:** issuing a link or reaching the store with no credential prompt.
- **Evidence:** what was reachable; screen-lock and console-timeout settings.
- **Severity:** EI **H** · PHI **H**.
- **Control test:** private doc, plus keeping the coordinator key out of any
  operator-visible artifact (RT-T-09 covers the technical half).

### RT-PH-02 — Physical access to the on-premise host
- **Scenario:** an intruder with machine-room access and time: removable media,
  live host memory, or the DICOM volume taken directly.
- **Target:** the Proxmox host(s) and the Orthanc storage volume / `grading-db`.
- **Steps (scenario):** establish, on paper and with the owner, what an intruder
  with physical access would obtain in 10 minutes, and whether any tamper evidence
  would exist afterwards.
- **Success criterion (measured as detection, not intrusion):** the assessment can
  point to a log line proving the host was touched — if none exists, that absence
  *is* the finding.
- **Evidence:** which logging/integrity mechanisms exist (or do not); volume
  location and its backup handling.
- **Severity:** EI M · PHI **H**.
- **Control test:** none CI-able; the repo-side deliverable is a runbook note that
  the volume's location and backup handling are documented wherever the real
  deployment exists, since `docs/HOSTING_SIZING.md` is still DRAFT with
  placeholder numbers.

### RT-PH-03 — Gaining entry to the building
- **Scenario:** tailgating / reception bypass to reach the machine room or an
  operator desk, as the first leg of RT-PH-01/02.
- **Target:** the facility housing the on-premise servers.
- **Steps (scenario):** scenario only, with owner-approved boundaries in the
  private doc; no operational detail here.
- **Success:** reaching a server room or operator desk without being
  challenged/recorded.
- **Evidence:** outcome class and challenge status only.
- **Severity:** EI M · PHI **H**.
- **Control test:** physical security is out of the repo's control; report into
  the private document.

---

## D. Chained objectives — "how far can an intruder get"

The point of the engagement is the chain, not the individual rung.

### RT-X-01 — Full exfiltration ladder
- **Ladder:** enumerate unassigned cases (RT-T-01) → bulk-retrieve one as a ZIP
  (RT-T-02) → get the bytes out of the session (RT-T-07) → be untraceable while
  doing it (RT-T-05) → survive the session for a second round (RT-T-10).
- **Success:** every rung passes for one sample study in one session. Report the
  **highest rung actually reached**, per flow, per role (student vs. someone with a
  link vs. someone on the network).
- **Evidence:** one artifact per rung, hashed, private store.
- **Severity:** EI **H** · PHI **H**.
- **Honesty note:** as of writing, rung 1 and 2 are verified **only against Orthanc
  on loopback**; rungs 3–5 are unverified. Nobody has traversed this ladder
  end-to-end, and the nginx proxy has not been exercised at all
  (`SECURITY_RED_TEAM.md` §6). Do not brief this as proven.
- **Test to add:** the ladder script is the union of RT-T-01/02/04/07 tests; keep
  them as separate CI scripts, not one mega-test.

### RT-X-02 — Answer key for unattempted cases
- **Scenario:** obtain ground truth for cases not yet seen — worth more than the
  images to a student, since the exam is scored on it.
- **Target:** `cases` rows (ground truth + reference report), `GET /case`
  `category_options`, feedback responses, `scripts/new-case-version.py`,
  `local/cases.json`, the `grading-db` volume.
- **Steps:** the RT-T-11 enumeration attempts; read what a feedback response leaks
  after a wrong answer; check whether a `learning`-stage session can see
  assessment/test ground truth; inspect what the results screen exposes; look for
  `cases.json` in any log, image layer, or backup.
- **Success:** ground truth for a case the session has not answered.
- **Severity:** EI **H** · PHI M (the file also carries clinical reference
  reports).
- **Baseline known:** `test_no_case_data_integrity.py` and
  `test_ground_truth_snapshot.py` cover the server's own invariants; the
  cross-stage-leak direction is **[hypothesis]**.
- **Test to add:** per-stage ground-truth invisibility assertions in
  `docker/grading-api/tests/`, using a sentinel ground truth and asserting it
  never appears in any response for an earlier stage — sentinel style exactly like
  `scripts/test-log-leak.sh`.

---

## E. Not in scope, with reasons

Recorded so the omissions are decisions: C2/redirector/payload infrastructure and
EDR/AV evasion (no implant exists — the client receives video pixels only); Active
Directory / IdP pivoting (no IdP in this system); cloud/SaaS testing and cloud
provider authorisation (on-premise only by hard rule; the real legal review is the
Kasm CE EULA §2.2 non-commercial + 5-concurrent cap); kernel privilege-escalation
research (the escalation that matters is RT-T-10); live phishing kit or real
credential harvesting against real people; anything requiring real patient data
without the owner's written go.
