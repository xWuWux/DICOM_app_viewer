# 0001. Freeze mechanism for Kasm sessions

Status: proposed. Desk research, local experiments and the live matrix (last section, run on the
lab Kasm 1.19.0 CE on 2026-10-15) are done. The owner decisions of issue #159 ((a)-(d)) plus the
freeze policy step (TTL, identity, timer) still gate any implementation.

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
   build.
4. The current stack already restores the exam-relevant state at container start:
   `docker/kasm-workspace-weasis/custom_startup.sh` asks grading-api for the student's current
   case and opens it via a `weasis://` URI built by `weasis_case_uri.py` (issue #185); recorded
   answers live server-side; `arrange_windows.sh` lays out the windows. A cold start therefore
   already lands on "the student's case, panel state intact, windows arranged". What a cold start
   loses is only unsaved in-memory state: Weasis view/zoom/layout, unsaved annotations, open
   auxiliary apps.
5. Local host experiments (Docker 29.8.1, WSL2, 2026-10-15):
   - `docker pause` / `docker unpause` work and are instant (test container: paused `true`, then
     `running` after unpause);
   - `docker checkpoint` is refused: "only supported on a Docker daemon with experimental features
     enabled"; no `criu` binary on the host; treat CRIU as unavailable on the lab host.

## Decision (proposal, pending live tests and owner policy)

Use NATIVE Kasm lifecycle as the freeze primitive, orchestrated by the coordinator (a freeze
subcommand next to `scripts/end-kasm-session.py`), with two tiers chosen by the live results:

- tier 1, `Pause`: true 1:1 desktop return. Gate results (lab, 2026-10-15): paused sessions do NOT
  consume the global concurrent-license cap (test A: PASS) but DO consume the owner user's 5-slot
  per-user record limit (test A: FAIL - frozen sessions accumulate and block new sessions for that
  user until destroyed); paused sessions survive a Kasm services restart (test C: PASS at service
  level; host reboot untested, see Consequences); RAM held is the session's real RSS (test E:
  0.2 GiB idle, cgroup ceiling 2 GiB per session). Net verdict: usable for one-frozen-session-per-
  student workloads only, with a destroy-based TTL; unusable as an unbounded archive.
- tier 2, `Stop` + Start-on-return: disk state only; the existing #185 startup path re-opens the
  student's current case automatically, so the exam content returns; the UI text must say unsaved
  desktop state is lost. Chosen as the fallback whenever tier 1 fails any gate.

`docker pause`/CRIU/`docker commit` run OUTSIDE Kasm's view are rejected as the mechanism: Kasm's
database would keep calling the session live-or-dead wrongly, the keepalive timer
(`keepalive_expiration=3600 s`, action delete, see #122/#159) and the admin UI would drift from
reality, and delete/destroy ownership must stay with Kasm (this is exactly the class of bug #159
documents for the opposite direction).

## Alternatives considered

| Option | Why not primary |
|---|---|
| Native Pause (tier 1) | The only 1:1 option; gated by RAM cost, cap counting (undocumented), reboot survival (docker pause does not outlive a daemon restart by itself) |
| Native Stop + Start (tier 2) | Fidelity level 2 only, but nearly free today thanks to #185; the honest fallback |
| `docker pause` bypassing Kasm | DB/UI drift, keepalive acts on wrong facts; rejected |
| CRIU / `docker checkpoint` | Unavailable on the lab host; needs experimental daemon; CRIU limits around X11/audio/shm unproven on this image; would need owner policy for on-disk memory snapshots containing exam data |
| `docker commit` + recreate | Strictly weaker than native Stop, slower, doubles disk; rejected |
| Persistent profile as the freeze | Files only; useful future addon for "same settings in a fresh session", orthogonal to state resume |

## Consequences

- The freeze policy step of #159 (TTL, same-student binding, timer #232 exclusion, CLAUDE.md
  exception) stays the hard blocker; no orchestration code before it is decided.
- Add to `docs/HOSTING_SIZING.md` once measured: one frozen (paused) session = `R` GB RAM held
  24/7; one stopped session = writable-layer disk only. Both rows currently unmeasured.
- Live matrix results (lab Kasm 1.19.0 CE, image `ipcmc/dicom-viewer:mvp` at 2 GiB / 1 core,
  2026-10-15; API-driven, results mirrored in the #159 comment):
  - A. cap counting: PARTIAL GO. Native `pause_kasm`/`resume_kasm`/`stop_kasm` work on CE (not
    license-gated). Paused and stopped sessions release the global running cap (a 6th request from
    another user was accepted while 4 sessions of user 1 were paused), but each owner's own 5
    record slots stay consumed by frozen sessions - "Kasm limit exceeded for user" fires with 5
    stopped or 5 paused records and 0 running. GO holds only under the policy "one frozen session
    per student, TTL destroy after N days, per-user headroom respected".
  - B. Stop fidelity: not run (needs interactive Weasis session automation); the #185 cold-start
    path remains the assumed recovery; run it before any tier-2 UI copy is promised.
  - C. Pause survival: PASS across `kasm_api`/`kasm_manager` restarts - `paused` state persisted in
    `kasms.operational_status`, `resume_kasm` returned the session to running. Docker daemon or
    host reboot was NOT testable in the lab (Docker Desktop VM); kernel pause state is expected to
    be lost there, so tier 1 needs the same Start-on-loose-session recovery path as tier 2.
  - D. N-day idleness (short-proxy, 90 s + structural): while paused, `expiration_date` and
    `keepalive_date` do NOT advance and no keepalive is issued - a paused session does not age out
    of the 3600 s idle timer (timer #232 semantics hold for free); a 3/7-day soak is still to run
    once a policy TTL exists.
  - E. cost: idle running session = 185-243 MiB resident; paused keeps the same RSS charged
    (cgroup freeze, not swap-out); plan worst case 2 GiB per frozen student at the image ceiling.
  - F. link lifecycle: `kasm_url` is unchanged across Pause/Resume and the same link keeps working;
    a paused session's `get_kasm_status` cannot be read by the manager (empty status) so any link
    validity check must first resume or consult the DB state.

Not covered: fidelity test B with a loaded Weasis study (needs interactive session automation),
pause survival across a full host reboot (Docker Desktop VM in the lab), multi-day soak runs, and
behavior of Kasm Enterprise-only features (not applicable; deployment is CE).
