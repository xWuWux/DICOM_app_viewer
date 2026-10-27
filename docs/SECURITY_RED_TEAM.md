# Security assessment checklist (pre-deploy, red-team style)

Status: DRAFT, MVP stage. Scope deliberately narrow: this is a list of
**falsifiable claims the repo already makes**, to be attacked by a human before
the first real cohort, not a generic enterprise pentest plan.

Every claim below is tagged **[verified]** (checked against the repo or a live
Orthanc — see §6 for how), **[hypothesis]** (plausible, untested — do not report
as a finding), or **[to verify]**. Unmarked items are checks to perform, not
assertions about the current system.

Ops detail that must NOT live in this public repo, and belongs in the private
IP_CMC doc instead: ROE and testing windows, emergency cutoff triggers,
escalation matrix, who owns what, any real hostnames/IPs.

## Why this is pre-deploy, not post-MVP

The properties below are *preventive correctness*, not detection quality. There
is no blue team, no SIEM and no alerting in this MVP, so MTTD/MTTR are not
measurable here — the only meaningful metric is "was the control present before
the first patient study was served".

Everything in `overlay.py` / `watchdog.sh` is framed as **"deterrence, not
prevention"** (their own words). This document exists to check that the
deterrence tier actually holds, and to record where it does not.

## Out of scope, on purpose

Each of these is boilerplate that does not map to this stack — recorded so the
omission is a decision, not an oversight:

- C2 / redirectors / payload obfuscation / EDR-AV evasion → no implant; the
  client receives only HTML5 video pixels via Kasm (CLAUDE.md hard rule 2).
- Active Directory / Azure AD / identity-provider pivoting → no IdP. Auth is one
  unguessable per-session token + a shared Orthanc Basic auth injected by nginx.
- Cloud provider authorisation, bucket/SaaS testing → CLAUDE.md hard rule 1
  (on-premise Proxmox only, no cloud). The real legal item is Kasm CE EULA §2.2
  (non-commercial, 5 concurrent sessions), tracked separately.
- Spear-phishing, vishing, tailgating, badge cloning, drop devices → the
  adversary population is students taking a Lung-RADS exam, not an APT.
- Kernel privilege escalation → the escalation that matters is container escape,
  below, and CLAUDE.md already mandates destroy-on-logout.

## 1. Threat model (what we are actually defending against)

Ordered by realistic likelihood, not by ATT&CK neatness:

- **T0 — the student who wants a better score.** Screenshots/phone-photos the
  images, copies the case, reaches cases they were not assigned, or has someone
  else grade their exam. Not malicious-in-the-crime-sense; extremely motivated,
  patient, and inside the authorized perimeter.
- **T1 — a leaked credential dump** from the shared Docker host / `.env`,
  yielding `ORTHANC_PASSWORD` + `GRADING_COORDINATOR_KEY`.
- **T2 — container escape** from a Kasm session to the KasmVNC host, then to
  Orthanc's volume (raw pixels, CLAUDE.md rule 1).
- **T3 — a malicious/compromised study** in the store driving a viewer/parser
  bug (Weasis Java stack, Orthanc, Explorer2).
- Not modelled: nation-state interception of a video stream.

## 2. Pre-flight (blocking, cheap, do first)

- [ ] **Anonymisation sign-off is a human statement, not a technical control.**
      issue #87. Files carry no `DeidentificationMethod` declaration, and dates +
      age are present. Confirm in writing, per batch, before ingest.
      `scripts/dicom-patient-consistency.py` counts disagreements; it does not
      prove de-identification.
      Blocker: `build-cases-file.py` matches studies on PatientName/PatientID —
      so those tags are populated by design, and "stripped of PHI" (CLAUDE.md) is
      a claim about pseudonymisation only. Record what it does and does not cover.
- [ ] **Kasm CE EULA §2.2** — non-commercial + 5 concurrent sessions. Any paid
      10–20 concurrent deployment is a procurement/legal item that will land
      before any CVE does.
- [ ] **Guacamole PoC is not a target for real data** (CLAUDE.md: not
      Kasm-parity-hardened). First red-team question is literally "can a session
      reach `grading-api` or Orthanc through the Guacamole path", and the honest
      answer today is that nobody has tried.
- [ ] Written authorization for the assessment, naming the host and the data
      class. Destructive DB ops need explicit confirmation (CLAUDE.md stop
      condition) — never rehearse migration work against a live `grading-db`.

## 3. Attack cases

Each case names the claim under test and the existing test that covers it, so a
gap is visible as "no script listed".

### 3.1 Watermark integrity (CLAUDE.md rule 4 — mandatory)

- [ ] Chrome flow: does the `mix-blend-mode:difference` DOM watermark survive
      DevTools removal, `display:none`, zoom, and fullscreen?
- [ ] Weasis flow: can the picom compositor be killed from inside the session so
      the overlay falls back to shape-only, and does the fallback stay legible on
      a white CT window?
- [ ] **Watchdog gap.** `docker/kasm-workspace-weasis/watchdog.sh` promises reappearance in ~1 s (issue #156
      caps backoff at 5 s). Kill the overlay and try to capture inside that
      window. Quantify the gap; do not assume it is under a second.
- [ ] **Crop-out test.** At the real session resolution, is a full image
      extractable from a screenshot region containing no tile? This is the
      realistic T0 attack and the cheapest failure mode.
- [ ] Multi-monitor / resized / minimised session: does the overlay follow?
      (issue #151 covers window-keeping — check watermark, not just liveness.)
- [ ] Does the watermark carry STUDENT_ID *and* SESSION_ID in every flow, or
      does `grading-panel.html` differ from the native overlay?
- Existing coverage: `scripts/test-visual-regression.sh` proves it *renders*;
  nothing here proves it is *unkillable-while-capture-is-possible*. Treat the
  gap as open.

### 3.2 Copy / export lockdown (the pixels leaving the platform)

- [ ] Weasis native export, import, send, Q-R — re-check in a live session.
- [ ] KasmVNC rich/binary clipboard DLP gap.
- [ ] **Kasm admin-console Group-level DLP toggles.** `scripts/test-copy-lockdown.sh`
      says explicitly these are out of CI scope because no live Kasm instance
      exists to re-check them. This is the single named gap in the repo's own
      test suite — it needs a human with a real Kasm console.
- [ ] Kasm file-transfer / download-to-client-device, in the direction
      container → student laptop.
- [ ] Keyboard-screenshot OS shortcut inside the session (PrtSc, `gnome-screenshot`,
      `import`, `scrot`, `xfce4-screenshooter` — is any of it installed?).

### 3.3 Orthanc proxy surface (highest-value case)

**This is a documented, deliberate design choice, not a missed bug.**
`docker/viewer/default.conf.template`'s `location /` filters by HTTP *method*,
and its own comment says so explicitly (issue #64): *"This -- not a path
allowlist -- is what actually closes the real gap (write/delete reachable at
all)"*, with the rationale that a path allowlist would break Explorer2/Weasis
browsing. Issue #64 legitimately closed the **write/delete** gap. What was not
assessed — and is the actual gap in the *documentation*, not in the code — is
what read-only freedom costs for exam integrity and PHI exposure. Frame it that
way in any ticket.

Consequence of the choice, **[verified]** against a live Orthanc (§6): `location
/` permits *any* GET/HEAD to Orthanc with a shared server-side Basic auth
injected by nginx, so every read-only Orthanc endpoint is in scope, including
bulk-retrieval ones.

Endpoint matrix — **[verified against Orthanc directly]**: every row was obtained
by requesting Orthanc itself on loopback, **not through the nginx proxy**. Image:
the one `docker-compose.yml:18` pins, `orthancteam/orthanc
:26.9.1@sha256:d2705f2c…` (reports `ApiVersion 31 / Version "1.13.0"`, matching
compose's comment that 26.9.1 bundles Orthanc 1.13.0), with the repo's own
`docker/orthanc/orthanc.json` mounted and bound to `127.0.0.1` only, loaded with
the repo's `sample-data/`. See §6 for the method and for the two checks that did
**not** complete. Orthanc internal IDs are opaque; the endpoints below take them:

| request | observed | matters because |
|---|---|---|
| `GET /studies` | 200, lists **every** study | enumeration needs no help from `grading-api` |
| `GET /patients` | 200, lists every patient | same, per patient |
| `GET /dicom-web/studies` | 200, tag JSON incl. **StudyInstanceUID (`0020,000D`)** for both studies | hands out the StudyInstanceUIDs of all studies — the identifier `grading-api` names `orthanc_study_uid`, and the one DICOMweb/Weasis `studyUID=` queries on (not to be confused with SOP Instance UID, `0008,0018`) |
| `GET /instances/{id}/file` | 200, 39 206 B, DICOM Part-10 file (`file(1)` misreports it as TIFF because of the 128-byte preamble) | the original file as ingested, tag set intact (`/instances/{id}/tags` listed `PatientName`, `PatientID`, `PatientBirthDate`, `StudyDate`) |
| `GET /studies/{id}/archive` | 200, ZIP | whole study, one request |
| `GET /series/{id}/archive` | **not verified** — 404 both attempts, my own harness passed a bad series id | presumed equivalent to the study archive; re-run before citing it |
| `GET /patients/{id}/archive` | 200, ZIP | every study of a patient |
| `GET /studies/{id}/media` | 200, ZIP | bulk-export route, not just the archives |
| `GET /changes` | 200 | the bulk-export feed |
| `GET /tools/find` | 405 | `POST /tools/find` is the only POST nginx allows (`location = /tools/find`) |
| `GET /tools/create-archive?Studies=…` | 400, JSON error, **not** an archive | not a GET vector: `Studies` is a JSON body key, not a query parameter |

**What the table does not prove.** Two statements about the proxy are *inferred
from `default.conf.template`, not observed*:

- that these GETs are reachable through it — inferred, because `location /`
  `limit_except GET HEAD` permits GET/HEAD and injects Orthanc's Basic auth;
  Orthanc may answer differently behind a prefix-handling proxy;
- that `/tools/create-archive` is unreachable through it — likewise inferred from
  config (POST denied except `/tools/find`), never tested. The POST variant also
  returned 400 in both payload shapes tried against Orthanc directly, so its
  accepted body shape is unknown and it is not verified as working at all. That
  does not change the design question: GET retrieval is what would need scoping,
  and the GET archive routes are confirmed.

`/studies/{id}/tags/StudyInstanceUID` is not a route (404).
`/studies/{id}/shared-tags` exists (200) but was not confirmed to expose
`0020,000D`; use `/dicom-web/studies` for StudyInstanceUID enumeration, which was
confirmed.

- [ ] **Re-run this entire matrix through the nginx proxy** (`location /`), both
      with and without the `/dicom-web/` prefix, and record proxy codes next to
      the direct ones. The proxy, not Orthanc, is the trust boundary — until then
      this table is a property of Orthanc, not of the deployment.
- [ ] **No per-session authorization exists at the Orthanc layer.** One shared
      credential serves every session, so Orthanc cannot distinguish which case a
      session was issued. Whether that is *exploitable* is the hypothesis below,
      not a finding.
- [ ] **HYPOTHESIS — cross-stage study access (do not report as a finding until
      reproduced live).** Read path: `GET /dicom-web/studies` → StudyInstanceUIDs
      of all studies → `GET /studies/{id}/archive` for a stage the session was not
      issued. Stage gating (learning/assessment/test) lives only in
      `grading-api`, which the Orthanc path never passes through.
      **[verified against Orthanc directly]** preconditions: enumeration returns
      all studies with their StudyInstanceUIDs (§3.3 matrix); `GET /api/case`
      returns exactly one
      `orthanc_study_uid` — the current one
      (`docker/grading-api/app/main.py:449`), so `grading-api` itself does not
      leak the others.
      **[unverified]** and decisive: (a) whether a student's session can reach
      these paths at all — the panel and Orthanc share the nginx origin, so this
      is same-origin `fetch()`, no CORS obstacle, but no test has done it from
      inside a real Kasm session; (b) whether the Chrome flow's kiosk mode
      blocks the input needed (`docker/kasm-workspace/custom_startup.sh`'s
      header claims kiosk/app mode means the student "can't navigate away, open
      a normal tab, or reach devtools/downloads" — unverified against
      address-bar-less keyboard shortcuts, `view-source:`, or a typed URL); (c)
      whether the resulting bytes can leave the session (§3.2 egress chain).
- [ ] Confirm the above is unreachable from the client device, not merely
      inconvenient: in the Chrome flow the browser is *inside* the container, so
      a successful download lands inside the session and then needs one unlocked
      egress from §3.2. The compound chain is the finding; verify it end to end.
- [ ] Decide and document the intended answer, as a design decision on top of
      #64 rather than a reversal of it. If per-case scoping is wanted, the shape
      is a per-session Orthanc ACL or a proxy-side UID check (which implies
      knowing the session's case at nginx). Do not "fix" it by tightening to
      `/dicom-web/` alone — the config comment records that this breaks Explorer2
      outright, because Explorer2 browses via Orthanc's native REST API across
      many paths (`/ui/*`, `/system`, `/peers`, `/statistics`, …).
- Existing coverage: `scripts/test-copy-lockdown.sh` and
  `scripts/tests/test_copy_lockdown.py` cover Weasis/clipboard copying; the
  `default.conf.template` comments record the #64 reasoning. **No test asserts a
  student cannot read a study they were not assigned** — the open question
  `scripts/test-copy-lockdown.sh` explicitly names (Kasm admin-console Group DLP
  toggles, no live Kasm in CI) is a different gap from this one.

### 3.4 Session token / link model

- [ ] Token transport, two separate mechanisms, two separate issues:
      - **`#token=` fragment for the page load — issue #94**
        (`docker/viewer/grading-panel.html:109`, `watermark.html:108`). The
        browser never transmits a fragment, so it cannot reach nginx.
      - **`X-Grading-Token` header for API calls — also issue #94.** `GET /case`
        reads the header only (`docker/grading-api/app/main.py`, `get_case`);
        `?token=` was removed outright, because a query-string credential was
        reproduced verbatim in nginx's access log *and* error log (issues
        #63/#94). `POST /submit` / `POST /reset` keep the token in the JSON body
        — a body is not logged.
      - **Reload survival — issue #145** (`grading-panel.html:113`,
        `watermark.html:115`): fragment → `sessionStorage` (this tab only, never
        sent, never `localStorage`), and "Sesja wygasła" with no retry button
        when absent or on 401.
      Verify absence from: nginx access + error logs, `Referer`, `Error` headers,
      `/proc/*/cmdline`, and anything
      `docker/kasm-workspace-weasis/overlay.py` or
      `docker/kasm-workspace-weasis/watchdog.sh` writes to `/tmp`.
      Existing: `docker/viewer/tests/test_session_token.py` (#145),
      `docker/grading-api/tests/test_token_transport.py` (#94),
      `scripts/test-log-leak.sh` (#93/#94 sentinels).
- [ ] **Link forwarding.** One student sends the link to another. What stops
      them? Today: nothing observable. Decide whether this is accepted, mitigated
      (IP/UA/concurrency fingerprint), or out of scope — and write the decision
      down, because "each session comes from its own uniquely issued link" is a
      claim in README that this attack defeats in spirit.
- [ ] Revoke path actually invalidates mid-session, including an in-flight
      `/submit`. Existing: `docker/grading-api/tests/test_session_revoke.py`,
      `docker/grading-api/tests/test_token_transport.py` — extend with a
      replay-after-revoke attempt from a live browser, not just an API client.
- [ ] TTL: default 8 h, max 7 d (`docker/grading-api/app/config.py`). A 7-day TTL + forwardable link is
      the worst pairing; check what deployments actually set.
- [ ] `GRADING_COORDINATOR_KEY` (≥32 chars, issue #97) — verify it is absent from
      every launch path's process env dump, `docker inspect`, and compose logs.
- [ ] Error-path leakage: malformed `cases.json` must name entry + field and
      **never a value** (`docker/grading-api/app/cases_file.py`); verify the same
      for 500s, nginx error pages, and
      `docker/grading-api/app/logging_config.py` output — run
      `scripts/test-log-leak.sh` against the Weasis flow, not only the Chrome
      flow. Note `get_case` already logs the stage/index detail server-side
      *only* and returns a generic 500 to the client: keep that asymmetry
      asserted, it is easy to regress while "improving" error messages.

### 3.5 Ephemeral / persistence (CLAUDE.md rule 5)

- [ ] After logout, nothing survives: no grading row, no temp file, no core
      dump, no Weasis cache dir, no X cookie, no `/tmp` watchdog log.
- [ ] `grading-db` volume: the answer key persists between sessions by design.
      Confirm no *student-supplied* free text can reach it (rule 3 — categorical
      Lung-RADS only). Probe `/submit` with oversized/adversarial payloads.
- [ ] Two concurrent sessions, same `student-id`: does state cross?
      (`create-session.py` minting the same id twice.)
- [ ] Container escape from a Kasm session to the host, then to Orthanc's volume
      (T2). Check the seccomp profile is actually applied at run time, not merely
      present in `docker/guacamole-weasis/seccomp/`.

### 3.6 Supply chain / infrastructure

- [ ] Trivy runs pinned + CRITICAL/HIGH only (see `.hadolint.yaml` rationale) —
      so MEDIUM/LOW inventory lives somewhere or is explicitly waived.
- [ ] Sample-data provenance: `sample-data/*.dcm` and the fetched BRAINIX set are
      public-license, never real cases. A real case committed by accident into a
      public repo is the worst single outcome this project has.
- [ ] CI secrets: no real key in any workflow; `security-scan.sh` uses placeholder
      values — verify CI does the same.
- [ ] **Published-port discipline differs per compose file, by design.**
      - `docker-compose.yml`: Orthanc published `127.0.0.1:…` — loopback-only,
        because session containers reach Orthanc over `kasm_default_network` by
        container name, not via a published port. Keep it loopback.
      - `docker-compose.remote-host.yml`: `"${BIND_ADDR:-0.0.0.0}:8042:8042"`
        (line 51), plus `8080` and `8043` on the same variable. `0.0.0.0` is
        **intentional** — that file's own header states it, and states the
        consequence: without a firewall rule, 8042 (Basic-Auth UI) and especially
        8043 (the auth-injecting proxy, **valid Orthanc access with no auth of its
        own**) are reachable by anyone who can route to the host. See
        `docs/PROXMOX_DEPLOYMENT.md`.
      - **Do not** add a "no port mapping without the `127.0.0.1:` prefix" CI
        regex — it fails on the existing file by construction.
      - What to assert instead: (a) `docker-compose.yml`'s Orthanc mapping keeps
        the loopback prefix; (b) `BIND_ADDR`'s `0.0.0.0` default and the
        firewall requirement stay present in `docker-compose.remote-host.yml`'s
        header and `docs/PROXMOX_DEPLOYMENT.md` — e.g. `scripts/lint.sh` greps
        both, in the same spirit as its existing "both compose files must agree
        on the Orthanc tag@digest" check;
      - [ ] and the finding that matters operationally: **the firewall rule is
            declared, not enforced** — nothing in the repo can prove it exists on
            a given host, so deployment needs a verification step (`iptables
            -S`/`nft list ruleset` output captured at deploy time), not a doc
            promise. `docs/history/qwen_weryfikacja.md` already flags this as
            "kontrola deklarowana, nie wdrażana".

## 4. Reporting

- One finding per ticket, with: claim tested → observed → severity as *exam
  integrity* or *PHI exposure* (not CVSS alone) → the script that should now
  assert it forever.
- Every confirmed finding must land as a CI/test addition, not a fix + prose.
  This repo's convention is that the regression suite encodes the guarantee
  (`scripts/test-copy-lockdown.sh`, `scripts/test-log-leak.sh`,
  `docker/viewer/tests/test_visual_regression.py`).
- Findings that can only be verified against a live Kasm console (Group DLP
  toggles) need an owner and a date, or they will silently rot.

## 5. Open questions for the owner

1. Is per-case scoping at the Orthanc layer in scope for the MVP, or accepted
   risk (§3.3)? This is the decision that changes the deployment.
2. Is link forwarding accepted, or must it be mitigated (§3.4)?
3. Which single item gates real-patient-data rollout beyond #87 — watermark
   crop-out, Group DLP verification, or Orthanc scoping?

## 6. Verification log — method, and what did not complete

**Method.** Throwaway Orthanc containers bound to loopback only, never attached
to `ipcmc-internal` or `kasm_default_network`, never sharing the
`orthanc-storage` volume, removed afterwards. Registered instances were the
repo's own public `sample-data/*.dcm`; no real patient data was involved and the
running project stack was not touched. Image: the pinned
`orthancteam/orthanc:26.9.1@sha256:d2705f2c…` with the repo's own
`docker/orthanc/orthanc.json` mounted read-only. Credentials came from an
`--env-file` plus a curl `--netrc-file`, so no password was ever in argv; none is
recorded here, and no hostnames are.

**One earlier pass ran the wrong image.** A locally-present
`orthancteam/orthanc:latest` returned `401 Unauthorized` for every request even
with correct credentials, so that pass partly exercised an ad-hoc
`RegisteredUsers` configuration rather than the project's. Everything marked
**[verified against Orthanc directly]** was re-run on the pinned digest with the
project's own config, where authentication behaves as documented (`200` with
credentials, `401` without). Any claim not tied to the pinned image is
unverified.

**Checks that did not complete — do not cite these as verified:**
- `GET /series/{id}/archive` — 404 on both attempts; the harness passed a study
  id where a series id belonged. The single 200/ZIP observation came from the
  wrong image.
- `POST /tools/create-archive` — 400 in both payload shapes tried; accepted body
  shape unknown.
- `GET /studies/{id}/shared-tags` — 200, but presence of `0020,000D` not
  confirmed.
- **The nginx proxy was never hit.** No request traversed `location /`, so every
  proxy-level statement in §3.3 is a reading of `default.conf.template`, not an
  observation.

**Never exercised at all:** §3.1, §3.2, §3.4 client-side, §3.5, and the
reachability questions in §3.3. No Kasm session, no Weasis, no browser, no Chrome
kiosk attempt, no live grading-api, no deployment. Nothing in this document is a
finding about the running system.

Line numbers cited below were exact at `origin/master` `d4b261c`; prefer the
named symbol if they drift.

**Citation method, and one trap.** Paths were checked with
`git cat-file -e origin/master:<path>` and issue numbers with
`git grep -l "issue #N\b" origin/master`, not against a local working tree.
Trap: a working tree predating issue #145 does not contain
`docker/viewer/tests/test_session_token.py` (added by `45655b5`), so `find` on a
stale clone gives a false negative for a file that exists on `origin/master`.
Check `git ls-tree origin/master <dir>/` before concluding a cited file is
missing.
