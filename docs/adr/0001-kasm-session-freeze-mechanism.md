# 0001. Freeze mechanism for Kasm sessions

Status: proposed. Desk research, local experiments and the live matrix (Consequences below) are done
as far as one local install can do them. Read the gates before quoting any number: B never ran,
D and E were re-run on 2026-10-16 with a paired protocol (below); C never covered a Docker daemon
restart, both runs saw an EMPTY desktop only. F is measured on the production link path for a
RUNNING session, and open for what a PAUSED session exposes and what the connect page does. The
owner decisions of issue #159 ((a)-(d)) plus the freeze policy step (TTL, identity, timer) still
gate any implementation.

Where the measurements come from ("the lab" below): a single-node Kasm 1.19.0 CE install on a
developer workstation (native dockerd 29.8.1 under systemd on WSL2, Ubuntu 26.04, host kernel
`microsoft-standard-WSL2`; NOT Docker Desktop). It is the same machine as the `docker pause` /
`docker checkpoint` experiments in item 5, and every matrix run was API-driven against it: the
first on 2026-10-15, the re-run on 2026-10-16. Consequence: results that live in Kasm's database,
API and licence counting transfer to any single-server CE deployment; results that depend on host
hardware, a reboot, multiple agents or production resource limits do not. The 2026-10-15 run left
no committed log at all; the 2026-10-16 re-run's redacted step log is committed as
`0001-appendix-freeze-matrix.md`. Several sub-results are still single-run observations of one
session (see Reproducibility below).

It was installed from the pinned 1.19.0 release with `sudo bash kasm_release/install.sh -e -p
localhost -L 8443`; that deviates from `docs/LOCAL_SETUP_GUIDE.md:123` (default port 443, no `-e
-p localhost`), because 443 was taken here. Accounts were the installer defaults
`admin@kasm.local` and `user@kasm.local` plus one temporary `lab-user3@kasm.local`. The admin and
user passwords were overwritten by writing hashes straight into the Kasm database (`docker exec
kasm_db psql`); the file holding the generated password lived in `/tmp` and is gone, so any re-run
starts with another password reset. Host: ~15 GiB RAM total, ~12 GiB available; one session capped
at 1 core / 2 GiB. The runs removed their session containers, the temporary user,
the `group_settings` override and their probe API keys; the Kasm platform itself is still installed
on this workstation. What they did NOT remove, and `destroy_kasm` does not remove either: anonymous
users (one per session created through the public endpoint - 3 rows after three such sessions) and
`session_tokens` rows (41 of them with 0 sessions in the table). Nothing observed shows Kasm
garbage-collecting those.

What these runs establish is Kasm CE SEMANTICS (how the licence is counted, which states survive,
what the API and the timers do), and that transfers to any single-server CE. This is NOT a capacity
benchmark: no study was loaded into Weasis, no network or multi-agent load was applied, and no
multi-day soak was run.

## Context

Requirement (issue #159 extension, owner request): a student closes the browser and returns days
later to the SAME session; the desktop shows the state from the moment of closing.

Facts the spike established (each with its source):

1. Kasm 1.19.0 (the pinned release, `docs/LOCAL_SETUP_GUIDE.md`) natively offers per-session
   actions Stop, Pause and Start on the admin Sessions page
   (`docs.kasm.com/docs/1.19.0/how-to/workspaces-sessions/sessions/`):
   - Stop preserves the disk state; "previously running programs will need to be restarted";
   - Pause preserves "the disk, process and memory state", resumed sessions "still have the
     previous programs launched and active", but paused sessions "still consume resources in
     physical memory and/or swap".
2. Persistent profiles (volume mount, legacy S3, storage-mapping) persist the home directory
   between sessions; the storage-mapping variant is marked Preview for production use in 1.19.
   Files persist; processes never do (fidelity level 2 at best).
3. The Community Edition limit of 5 concurrent sessions is a LICENCE term (EULA 2.2, cited by
   `docs/HOSTING_SIZING.md:28`; the EULA text about paused or stopped sessions has NOT been read for
   this ADR). What the software does is a separate and narrower fact, measured once on one machine:
   its own counter accepted a further request while frozen records existed, i.e. the running-session
   counter released frozen sessions. There is also a per-user record limit (`Kasm limit exceeded for
   user`) that counts records regardless of state - but it was measured on a NAMED user, and it does
   not bind our flow: `scripts/create-session.py:423-446` deliberately omits `user_id`, so every
   student link creates a throwaway anonymous Kasm user and nothing accumulates per student (2
   public `request_kasm` calls in the lab produced exactly 2 `anon_*` users). The
   `max_kasms_per_user` group setting (inserted in `group_settings`, services restarted) did not
   change the observed limit in this build - a single probe (one value, one attempt, the setting name
   read off the Kasm 1.17.0 docs), so untested rather than disproved.
4. The current stack already restores the exam-relevant state at container start:
   `docker/kasm-workspace-weasis/custom_startup.sh` asks grading-api for the student's current case
   and opens it via a `weasis://` URI built by `weasis_case_uri.py` (issue #185); recorded answers
   live server-side; `arrange_windows.sh` lays out the windows. A cold start therefore lands on
   "the student's case, panel state intact, windows arranged" - read off the code and the scripts,
   NOT verified on a live session (that is exactly test B). What a cold start loses is only unsaved
   in-memory state: Weasis view/zoom/layout, unsaved annotations, open auxiliary apps.
5. Docker primitives on the same workstation (2026-10-15):
   - `docker pause` / `docker unpause` work and are instant (test container: paused `true`, then
     `running` after unpause);
   - `docker checkpoint` is refused: "only supported on a Docker daemon with experimental features
     enabled"; no `criu` binary on the host; treat CRIU as unavailable on that host.

## Decision (proposal, pending live tests and owner policy)

Use NATIVE Kasm lifecycle as the freeze primitive, orchestrated by the coordinator (a freeze
subcommand next to `scripts/end-kasm-session.py`), with two tiers chosen by the live results:

- tier 1, `Pause`: true 1:1 desktop return, PARTIAL GO overall (matrix below). Frozen sessions did
  not count toward the software's running-session counter in one run (A; see the licence note in
  Facts - that is a technical observation, not a licence interpretation); the per-user record limit
  does not bind, because each student link is its own anonymous Kasm user; paused sessions survive a
  Kasm services restart (C: PASS at service level; Docker daemon restart and host reboot NOT RUN);
  the memory charged is the session's own working set (E: ~92 MiB right after pausing an EMPTY
  desktop in a lab whose sessions are limited to 2 GiB; #122 measured ~1.9 GiB idle and up to 3 GiB
  with a real CT study in Weasis, so PLAN 3 GiB per frozen student until a frozen session with a
  loaded study is measured). Net verdict: technically workable; the licence reading of EULA 2.2 and
  the RAM of a study-loaded frozen session are the two open gates.
- tier 2, `Stop` + Start-on-return: disk state only; the existing #185 startup path re-opens the
  student's current case, so the exam content is expected to return - the code path exists but was
  NOT verified on a live session (test B not run), so treat the return as assumed until then. The UI
  text must say unsaved desktop state is lost. Chosen as the fallback whenever tier 1 fails any
  gate.

`docker pause`/CRIU/`docker commit` run OUTSIDE Kasm's view are rejected as the mechanism: Kasm's
database would keep calling the session live-or-dead wrongly, the keepalive timer
(`keepalive_expiration=3600 s`, action delete, see #122/#159) and the admin UI would drift from
reality, and delete/destroy ownership must stay with Kasm (this is exactly the class of bug #159
documents for the opposite direction).

## Alternatives considered

| Option | Why not primary |
|---|---|
| Native Pause (tier 1) | The only 1:1 option; gated by RAM cost WITH A LOADED STUDY (unmeasured, plan 3 GiB per #122), by what EULA 2.2 says about frozen sessions (not read), and by reboot survival (untested) |
| Native Stop + Start (tier 2) | Fidelity level 2 only, but nearly free today IF the #185 path holds on a live session (untested); the honest fallback |
| `docker pause` bypassing Kasm | DB/UI drift, keepalive acts on wrong facts; rejected |
| CRIU / `docker checkpoint` | Unavailable on that host; needs experimental daemon; CRIU limits around X11/audio/shm unproven on this image; would need owner policy for on-disk memory snapshots containing exam data |
| `docker commit` + recreate | Strictly weaker than native Stop, slower, doubles disk; rejected |
| Persistent profile as the freeze | Files only; useful future addon for "same settings in a fresh session", orthogonal to state resume |

## Consequences

- The freeze policy step of #159 (TTL, same-student binding, timer #232 exclusion, CLAUDE.md
  exception) stays the hard blocker; no orchestration code before it is decided.
- `docs/HOSTING_SIZING.md` section 4 now carries frozen/stopped rows. What is still unmeasured:
  the writable-layer disk footprint of a stopped session, the memory of a session with a loaded
  study, and the per-profile `C`/`R` rows of the sizing table, which stay `[PLACEHOLDER]` until the
  load test of HOSTING_SIZING section 6 runs.
- Live matrix results (the single-node lab install defined at the top, image
  `ipcmc/dicom-viewer:mvp` with the lab's session limit of 2 GiB / 1 core, first run 2026-10-15;
  API-driven). An earlier version of these figures is in the #159 comment of 2026-10-15 13:17 UTC;
  THIS FILE SUPERSEDES IT for the environment naming and for D, E and F - that comment still says
  "lab", "Docker Desktop VM" and "RSS" and omits the empty-desktop caveat. The owner has not
  accepted A or D from either text; treat both as author measurements, not as agreed facts:
  - A. cap counting: PARTIAL GO, and its per-user half does not apply to us. Native
    `pause_kasm`/`resume_kasm`/`stop_kasm` work on CE. In that one run the software's counter kept
    accepting new sessions while frozen records existed (a second user's request was accepted while
    the first held 4 paused plus 1 stopped record), and a third user hit "Per concurrent session
    license limit exceeded" once 5 records were RUNNING. A second, per-user limit ("Kasm limit
    exceeded for user") did fire with 5 paused or stopped records for one NAMED user - but our flow
    never has one user holding several records: `create-session.py` sends no `user_id`, so every
    link is a fresh anonymous user (Facts item 3). What binds the design is therefore the CE licence
    term itself (EULA 2.2, unread for frozen sessions) and host RAM, not user slots. A TTL is still
    wanted - to bound frozen RAM and the anonymous users and token rows sessions leave behind - not
    to free slots.
  - B. Stop fidelity: not run (needs interactive Weasis session automation); the #185 cold-start
    path remains the assumed recovery; run it before any tier-2 UI copy is promised.
  - C. Pause survival: PASS across `kasm_api`/`kasm_manager` restarts (`docker restart`, no sudo) -
    `paused` persisted in `kasms.operational_status` and `resume_kasm` returned the session to
    running. NOT RUN at Docker-daemon level, and "not testable" would be wrong: a `sudo -n systemctl
    restart kasm` attempt was refused ("interactive authentication is required") and the run carried
    on with the service-level restart instead. A real daemon restart needs sudo and also stops every
    other container on this workstation, so it needs the owner's go-ahead. Kernel pause state is
    EXPECTED to be lost across a daemon restart or a reboot (hypothesis, untested), so tier 1 needs
    the same Start-on-lost-session recovery path as tier 2 either way.
  - D. N-day idleness (short proxy: one 80 s interval between two reads, plus a structural reading
    of the timer config): while paused, `expiration_date` and `keepalive_date` did not advance
    and no
    new keepalive date appeared (inferred from equal timestamps, not from observing the keepalive
    process) - a paused session does not age out of the 3600 s idle timer, so timer #232 semantics
    hold for free. A 3/7-day soak is still to run once a policy TTL exists.
  - D re-run, 2026-10-16, with real timestamps: running -> `expiration_date 09:02:26`,
    `keepalive_date 08:02:26`; paused and 80 s later both fields byte-identical; on resume
    `expiration_date` jumped to 09:03:52 while `keepalive_date` stayed (the 300 s keepalive
    interval had not elapsed). The idle timer is therefore suspended while paused, and
    `expiration_date` moved on resume (+86 s). Which mechanism is not distinguished by this data: a
    full re-arm at resume time, or a shift by the paused duration - the session was paused for about
    that long, so both fit. It matters for #232: a shift means frozen time never ages the session at
    all, a re-arm means every resume grants a fresh full 3600 s.
  - E. cost: TWO SINGLE READINGS on two different sessions, empty desktop, no study loaded - 242.8
    MiB (`docker stats` of a session in `paused`, just after its start) and 185 / 185.9 MiB
    (`running`, a different session, seconds after resume). `docker stats` reports cgroup usage
    including page cache, not strict RSS, and no paired running-versus-paused reading of the SAME
    session exists: the paused branch printed nothing, because a paused session reports no
    `container_id`. "Paused keeps the same memory charged" is therefore an inference from cgroup
    freeze semantics plus one reading, not a measurement. A loaded CT/MPR study was NOT measured and
    a student with Weasis open will be materially larger: plan 3 GiB per frozen student (#122
    measured ~1.9 GiB quasi-idle and up to 3 GiB, the Kasm per-session cap, with a real CT study in
    Weasis) until a frozen session WITH a loaded study is measured. The `2 GiB` visible in
    `docker stats` here is only this lab's own session limit.
  - E re-run, 2026-10-16, PAIRED on one container (`docker stats`, empty desktop): 67 MiB while
    starting (CPU 82%), 92.8 MiB 2 s after `pause_kasm`, 92.2 MiB after a further 80 s paused (CPU
    0.00%), 158.8 MiB 6 s after `resume_kasm`. Same `container_id` before and after the pause, so
    this is the comparison the first run never made: the working set neither shrank nor grew across
    the pause (92.8 -> 92.2 MiB over 80 s on a host with ~12 GiB free) and CPU went to 0.00%. No
    claim about swapping: with that much free memory the run cannot show what a frozen cgroup does
    under pressure, where its pages can be reclaimed. The 67 MiB (CPU 82%) and 158.8 MiB (CPU 102%,
    6 s after resume) readings are transients of startup and of resume, not steady states. Still no
    loaded study.
  - F. link lifecycle: MEASURED 2026-10-16 on the production path, for a RUNNING session.
    `POST /api/public/request_kasm` with an `api_key`/`api_key_secret` pair returns `kasm_id`,
    `user_id`, `username`, `session_token` and `kasm_url`, the last of which is
    `/#/connect/kasm/<kasm_id>/<user_id>/<JWT>`; the admin/client `request_kasm` the earlier runs used returns only `kasm_id`/`status`
    and has no `kasm_url` field at all, which is why F had looked unmeasurable (`None == None`).
    Keys are minted by `POST /api/admin/create_api_configs` (username and token in the JSON body,
    not headers) plus `add_permissions_group` with permission id 100 (`User`); the 404/400 trail of
    the previous two days was the wrong URL prefix plus credentials in headers.
  - What the student's link actually is, with negative controls. The URL's JWT, sent alone as
    `token`, authenticates `POST /api/public/get_kasm_status`: 200 with the session body, while a
    garbage token gives 401 "Access Denied", no token gives 400 "Invalid Request", and the API key
    instead of a JWT gives 403. The URL-embedded JWT and the `session_token` field are the same
    string. So the credential handed to the student is a real credential, and in this run it still
    authenticated after `pause_kasm` plus `resume_kasm`.
  - What a frozen session exposes: on a PAUSED session the same call answers 200 with EXACTLY one
    top-level key, `operational_status: "paused"` - no `kasm` object and no `kasm_url`. On a running
    session it answers `current_time`, `kasm` and a top-level `kasm_url`, and `operational_status`
    lives inside `kasm` instead. Two different shapes, so a client must read both places (see the
    note about `reissue-session-link.py` below). This also corrects an earlier entry here that said a
    paused session reports an empty status: the `paused` value IS reported, only at the top level.
    The operative fact is the missing `kasm_url`: **there is no link for a frozen session**, so a
    return must resume the session before a link exists.
  - Links are minted per call, not per session: the creation URL, the running reading and the
    resumed reading of one session carried three different values (SHA-256 prefixes `53b858527705`,
    `5974678b40fc`, `c5c674608c10`). In the DB, `session_tokens` is keyed by user and every status
    call inserts a row with `session_expiration_date` NULL; `create-session.py:470-481` polls that
    endpoint up to 30 times per session. Nothing observed revokes older tokens on pause/resume, so
    old links keep working until their JWT `exp`.
  - Link lifetime: the creation JWT carries `exp` about 80 h after creation on this install (one
    reading; governed by the server's `session_lifetime`, whose value was not read; expiry itself was
    NOT observed). ~3 days is this lab's configuration, not a Kasm constant. What follows is
    conditional on handing students the raw URL: a freeze longer than that lifetime returns them a
    dead link for a session that is alive.
  - Freezing is privileged: `pause_kasm` under a `User`-only key answers 403 "API Key (...) is not
    authorized to impersonate a user", while the admin JWT paused that anonymous-user session fine.
    Which grant would suffice was not tested (candidates in `get_permissions`: 352
    `USERS_AUTH_SESSION`, 801 `SESSIONS_MODIFY`, 803 `SESSIONS_DELETE`), and an admin JWT is a
    credential that can pause, resume or delete ANY session - that cost belongs next to any design
    that needs one.
  - Retracted the same day, twice. (i) An entry here claimed `delete_api_configs` and
    `update_api_configs` are unusable on CE because they answered 500 and "Missing Target Api or
    api_id". They are not: the field is spelled `api_Id` for delete and `target_api` for update, and
    with those names both calls returned 200 and the probe keys were deleted. The endpoint was never
    broken, my payload was. (ii) "The same URL was accepted after the resume" was first recorded as
    proof that the student's link survives a freeze; the accepting call was `get_kasm_status`, and
    the check that actually isolates the link's own credential is the one with negative controls
    above - read it, not the earlier sentence.
  - What F does not settle: whether the connect PAGE accepts an old link after a resume (everything
    here is an API call against the session, not a page load); what that page does with a paused
    session; and, for tier 2, the link and status of a STOPPED session were never read at all. F
    therefore does not rank tier 1 against tier 2. What it adds to tier 1 is a resume step and a
    privileged credential.
  - F also sharpens the KeyError risk noted before: `kasm_url` exists only on the public path, so
    `scripts/create-session.py:493` depends on that one endpoint keeping the field, with no guard.
  - Owner direction, recorded 2026-10-16: the student must always have ONE link that works, even on
    a return after three days. No Kasm URL can satisfy that, for four independent reasons: its JWT
    carries an `exp`; a Kasm-account logout deletes the token row and kills the link; it does not
    exist while the session is paused; and a fresh one is minted on every status call anyway. So the
    student-facing artifact should be an app-owned, opaque, stable entry point which, on each open,
    resolves student -> session, resumes it if paused, waits for `running`, calls public
    `get_kasm_status` ONCE and redirects to the fresh `kasm_url`. The student's one link then never
    changes while every Kasm URL behind it is disposable. Costs, plainly: that entry point holds a
    privileged credential server-side, it is a single point of failure, and every open adds a
    `session_tokens` row. The re-issue code that this builds on is **#251**
    (`scripts/reissue-session-link.py`, currently on `origin/fix/159-destroy-on-logout`) - not #185,
    which is the Weasis case-follow startup path. That script needs three changes from these
    measurements: read `kasm_url` from the top level of the status response first, treat a response
    with no `kasm_url` and a top-level `operational_status` of `paused` as "resume first" (it
    currently prints "answered without a kasm_url" and reports `unknown` because it reads
    `kasm.operational_status`), and call the endpoint once per open rather than in a retry loop. The
    TTL number stays open (recommendation 7 days plus a warning) and is no longer bounded by link
    lifetime, because the link the student holds is the app's own.
- Driver and stopped-disk status: the matrix was re-run on 2026-10-16 with a rebuilt probe (login ->
  request_kasm -> pause/resume/stop/destroy, container id read from the `kasms` table instead of the
  API, `docker stats` sampled at each state, dates read from the DB). The redacted step log of that
  run is committed as `0001-appendix-freeze-matrix.md`; the driver script itself is still outside
  the repository and should be committed with unit tests before anyone re-measures.
  Stopped-session disk finally has one number: `docker ps -s` on the stopped container reported a
  91.8 MB writable layer (3.44 GB virtual), one observation of an empty-desktop image.
- Reproducibility (stated plainly, because these numbers get quoted outside this file): the
  2026-10-15 driver and its raw output lived under `/tmp`, were never committed, and no longer
  exist; that day's figures survive only as the prose above. The 2026-10-16 re-run left a committed
  redacted log (`0001-appendix-freeze-matrix.md`), so D, E and F are now traceable to a machine
  record: D and E are paired readings of one container, F compares real `kasm_url` values. What is
  still thin is depth, not traceability: one session each, empty desktop, an 80 s idleness proxy,
  no loaded study, no multi-day soak, and A is not re-measured. Re-running needs admin credentials,
  which here meant
  overwriting the password hash in the Kasm database through `docker exec kasm_db` - a
  disposable-lab shortcut that must not be repeated on any host with real users or real data.

Not covered: fidelity test B with a loaded Weasis study (needs interactive session automation),
pause survival across a Docker daemon restart or a full host reboot (needs `sudo systemctl restart
docker` here; the `sudo -n` variant was refused), a browser-level check of what the connect page
shows for a paused and for a resumed session (F covered the API only), multi-day soak runs, and
behavior of Kasm Enterprise-only features (not applicable; deployment is CE).