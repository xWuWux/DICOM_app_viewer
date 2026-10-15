# 0001. Freeze mechanism for Kasm sessions

Status: proposed. Desk research and local experiments are done; the live matrix (last section)
needs a real Kasm instance, and the owner decisions of issue #159 ((a)-(d)) plus the freeze policy
step (TTL, identity, timer) still gate any implementation.

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
3. Community Edition is licensed for 5 concurrent sessions
   (`kasm.com/community-edition`). Whether a paused or stopped session counts as concurrent is NOT
   documented - live test A below is a go/no-go for the whole feature.
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

- tier 1, `Pause`: true 1:1 desktop return, accepted only if paused sessions do NOT consume the
  CE 5-session cap (test A) AND survive a Kasm services restart / host reboot (test C) AND the
  RAM cost of N frozen students fits the host budget (test E);
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
- Live matrix (run against the lab Kasm; results go into a comment on #159):
  - A. cap counting (GO/NO-GO): pause 5 sessions, request a 6th; repeat with stopped;
  - B. Stop fidelity: open a case with Weasis, Stop, Start, open the link - does the #185 path
    reopen the same study, what visibly differs;
  - C. Pause survival: pause, restart Kasm services, reboot host, then Start;
  - D. N-day idleness: keep a paused session 3 and 7 days; check keepalive/`session_timeout` does
    not delete it (paused must not look "idle" to the timer);
  - E. cost: `docker stats` memory of a paused session with one study loaded (fills the
    HOSTING_SIZING row);
  - F. link lifecycle: after Pause and after Stop, does `reissue-session-link.py` (#251) still
    return a working link on Start, and does the old link stay dead.

Not covered: any cost numbers for real Weasis workloads (the host used for experiments has no Kasm
installation), and behavior of Kasm Enterprise-only features (not applicable; deployment is CE).
