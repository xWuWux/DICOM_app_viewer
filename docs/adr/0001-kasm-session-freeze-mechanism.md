# 0001. Freeze mechanism for Kasm sessions

Status: proposed. Desk research, local experiments and the live matrix (Consequences below) are done
as far as one local install can do them. Read the gates before quoting any number: B never ran,
D and E were re-run on 2026-10-16 with a paired protocol (below); C never covered a Docker daemon
restart, both runs saw an EMPTY desktop only, and F is still open for the production link path.
did not measure what its own label claims. The owner decisions of issue #159 ((a)-(d)) plus the
freeze policy step (TTL, identity, timer) still gate any implementation.

Where the measurements come from ("the lab" below): a single-node Kasm 1.19.0 CE install on a
developer workstation (native dockerd 29.8.1 under systemd on WSL2, Ubuntu 26.04, host kernel
`microsoft-standard-WSL2`; NOT Docker Desktop). It is the same machine as the `docker pause` /
`docker checkpoint` experiments in item 5, and every matrix run was API-driven against it on
2026-10-15. Consequence: results that live in Kasm's database, API and licence counting transfer
to any single-server CE deployment; results that depend on host hardware, a reboot, multiple
agents or production resource limits do not. Several sub-results are single-run observations with
no committed log (see Reproducibility below).

It was installed from the pinned 1.19.0 release with `sudo bash kasm_release/install.sh -e -p
localhost -L 8443`; that deviates from `docs/LOCAL_SETUP_GUIDE.md:123` (default port 443, no `-e
-p localhost`), because 443 was taken here. Accounts were the installer defaults
`admin@kasm.local` and `user@kasm.local` plus one temporary `lab-user3@kasm.local`. The admin and
user passwords were overwritten by writing hashes straight into the Kasm database (`docker exec
kasm_db psql`); the file holding the generated password lived in `/tmp` and is gone, so any re-run
starts with another password reset. Host: ~15 GiB RAM total, ~12 GiB available; one session capped
at 1 core / 2 GiB. The run removed its session containers, the temporary user and the
`group_settings` override; the Kasm platform itself is still installed on this workstation.

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
3. Community Edition license limit is 5 sessions (`kasm.com/community-edition`). The lab run
   measured how it is enforced: TWO independent limits. (i) A global license limit counts RUNNING
   sessions only - pause and stop release it. (ii) A per-user limit (`Kasm limit exceeded for user`)
   counts all not-destroyed session records regardless of state - paused and stopped sessions keep
   occupying the user's 5 slots; only destroy frees them. The `max_kasms_per_user` group setting
   (inserted in `group_settings`, services restarted) did not change the observed limit in this
   build - and that is a single probe (one value, one attempt, the setting name read off the Kasm
   1.17.0 group-settings docs page), so treat it as untested rather than disproved.
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

- tier 1, `Pause`: true 1:1 desktop return, PARTIAL GO overall (matrix below). Paused sessions do
  NOT consume the global concurrent-license cap (A-global: pause and stop release it) but DO consume
  the owner's 5-slot per-user record limit (A-per-user: frozen sessions accumulate and block new
  sessions for that user until destroyed); paused sessions survive a Kasm services restart (C: PASS
  at service level; Docker daemon restart and host reboot NOT RUN, see Consequences); the memory
  charged is the session's own working set (E: ~0.2 GiB for an EMPTY desktop with no study loaded,
  cgroup ceiling 2 GiB per session; a loaded study is unmeasured). Net verdict: usable for
  one-frozen-session-per-student workloads only, with a destroy-based TTL; unusable as an unbounded
  archive.
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
| Native Pause (tier 1) | The only 1:1 option; gated by RAM cost WITH A LOADED STUDY (unmeasured), by per-user record counting (measured, A) and by reboot survival (untested) |
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
  `ipcmc/dicom-viewer:mvp` at 2 GiB / 1 core, 2026-10-15; API-driven). An earlier version of these
  figures is in the #159 comment of 2026-10-15 13:17 UTC; THIS FILE SUPERSEDES IT - that comment
  still says "lab", "Docker Desktop VM" and "RSS" and omits the empty-desktop caveat:
  - A. cap counting: PARTIAL GO. Native `pause_kasm`/`resume_kasm`/`stop_kasm` work on CE (not
    license-gated). Paused and stopped sessions release the global running cap: a further request
    from a second user was accepted while the first user held 4 paused plus 1 stopped record, and a
    third user hit "Per concurrent session license limit exceeded" once 5 records were RUNNING (the
    two error texts come from the two different limits). Each owner's own 5 record slots stay
    consumed by frozen sessions: "Kasm limit exceeded for user" fires with 5 stopped or 5 paused
    records and 0 running. GO holds only under the policy "one frozen session per student, TTL
    destroy after N days, per-user headroom respected".
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
    interval had not elapsed). The idle timer is suspended while paused and resume re-arms it.
  - E. cost: TWO SINGLE READINGS on two different sessions, empty desktop, no study loaded - 242.8
    MiB (`docker stats` of a session in `paused`, just after its start) and 185 / 185.9 MiB
    (`running`, a different session, seconds after resume). `docker stats` reports cgroup usage
    including page cache, not strict RSS, and no paired running-versus-paused reading of the SAME
    session exists: the paused branch printed nothing, because a paused session reports no
    `container_id`. "Paused keeps the same memory charged" is therefore an inference from cgroup
    freeze semantics plus one reading, not a measurement. A loaded CT/MPR study was NOT measured and
    a student with Weasis open will be materially larger: plan the full 2 GiB ceiling per frozen
    student until it is.
  - E re-run, 2026-10-16, PAIRED on one container (`docker stats`, empty desktop): 67 MiB while
    starting (CPU 82%), 92.8 MiB 2 s after `pause_kasm`, 92.2 MiB after a further 80 s paused (CPU
    0.00%), 158.8 MiB 6 s after `resume_kasm`. Same `container_id` before and after the pause, so
    this is the comparison the first run never made: pause holds the working set (it does not swap
    it out) and costs no CPU, and memory grows again after resume. Still no loaded study.
  - F. link lifecycle: NOT VERIFIED. The check reported `url same after pause/resume: True`, but the
    comparison was `None == None`: `get_kasm_status` returned no `kasm_url` in any of these
    responses
    and no link was opened after a resume. What the run did show is that a paused session reports no
    `container_id` and an empty status to the manager, so a link validity check must either resume
    first or read the database. The 2026-10-16 re-run went further: `request_kasm` on the ADMIN path
    returns only `kasm_id` and `status` - it has no `kasm_url` field at all - while
    `scripts/create-session.py:493` builds the student link from `created['kasm_url']`, i.e. from the
    PUBLIC `request_kasm`, which authenticates with an `api_key`/`api_key_secret` pair. API-key
    management was not reachable through the admin paths tried here (`get_api_keys`,
    `create_user_api_key`, `create_api_key` all 404), so closing F needs a key pair minted in the
    admin UI and the comparison re-run against the public endpoint, then the link actually
    opened. The
    corollary matters more than the freeze question: if the public `request_kasm` ever answers
    without
    `kasm_url`, `create-session.py:493` raises KeyError - that field is load-bearing and unverified.
- Driver and stopped-disk status: the matrix was re-run on 2026-10-16 with a rebuilt probe (login ->
  request_kasm -> pause/resume/stop/destroy, container id read from the `kasms` table instead of the
  API, `docker stats` sampled at each state, dates read from the DB). The probe still lives outside
  the repository; commit it with unit tests plus a redacted log before these numbers get quoted.
  Stopped-session disk finally has one number: `docker ps -s` on the stopped container reported a
  91.8 MB writable layer (3.44 GB virtual), one observation of an empty-desktop image.
- Reproducibility (stated plainly, because these numbers get quoted outside this file): the API
  probe driver and its raw output lived under `/tmp` during the run, were never committed, and no
  longer exist. As of 2026-10-16 the matrix is reproducible only by hand, from the text above (and
  an older, partly wrong copy of it in the #159 comment). The evidence base is thin: E is two
  unrelated single readings, D is an 80 s proxy, F was not measured at all. Before a capacity or
  licence decision quotes these figures, rebuild the probe driver (suggested
  `scripts/spike/kasm_freeze_probe.py`, credentials from the environment only, as
  `scripts/end-kasm-session.py` already does), re-run A, D, E and F so that F compares two real
  `kasm_url` values and E takes a paired running/paused reading of one session, and commit the
  driver plus a redacted results file. A re-run needs admin credentials, which here meant
  overwriting the password hash in the Kasm database through `docker exec kasm_db` - a
  disposable-lab shortcut that must not be repeated on any host with real users or real data.

Not covered: fidelity test B with a loaded Weasis study (needs interactive session automation),
pause survival across a Docker daemon restart or a full host reboot (needs `sudo systemctl restart
docker` here; the `sudo -n` variant was refused), a link check that actually opens the resumed
session (F), multi-day soak runs, and behavior of Kasm Enterprise-only features (not applicable;
deployment is CE).