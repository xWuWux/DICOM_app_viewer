# DICOM Viewer — MVP

Minimal, runnable proof-of-concept for the IP_CMC Lung-RADS training platform.
See `CLAUDE.md` for the hard rules this project must never violate.

(This README occasionally refers to "the original design discussion" — PM
correspondence and radiology requirements gathered during scoping. That
material is kept local-only and gitignored, never shipped in this repo, so a
fresh clone won't have it; nothing below depends on it being present.)

**Scope of this MVP** (deliberately narrow — see "What's NOT in this MVP"
below): a DICOM store with a couple of sample studies, a forensic watermark
baked into every session, served through a container that's meant to sit
behind Kasm Workspaces so each session comes from its own unique,
individually-issued link. No Moodle, no payments, no stratified sampling yet.

**Setting up your own copy?** See `docs/LOCAL_SETUP_GUIDE.md` for a
complete, step-by-step walkthrough (no assumed context) — the sections
below are more of a technical decisions log than an onboarding doc.
**Just want the shape of it?** See `docs/ARCHITECTURE.md` for current-state
Mermaid diagrams (session flow + deployment topology — those cover the
Chrome+Orthanc flow described below; they predate the Weasis flow and
haven't been redrawn for it yet).

## Two viewer flows, side by side

This project didn't replace Orthanc with Weasis — **Orthanc is still the
DICOM store and DICOMweb server behind both flows.** What changed is which
*viewer* a student's Kasm session actually shows them, after PM feedback
wanted something closer to the native desktop viewers
(Horos/Weasis) radiologists already know, instead of Orthanc's own web UI.
Both flows are real, both are registerable in Kasm at the same time, and
both talk to the same `grading-api` for the Lung-RADS mechanics:

| | **Chrome flow** (original MVP) | **Weasis flow** (in progress) |
|---|---|---|
| Workspace image | `docker/kasm-workspace/` | `docker/kasm-workspace-weasis/` |
| What the student sees | Chrome, kiosk mode, one page: Orthanc's web viewer in an iframe + the grading panel as a sidebar | Weasis, a real native DICOM viewer window + the grading panel as its own small browser window beside it |
| Frontend page(s) | `docker/viewer/watermark.html` (iframe + sidebar, one page) | `docker/viewer/grading-panel.html` (panel only — Weasis shows the images itself) |
| Watermark | DOM-based, `mix-blend-mode:difference` for guaranteed contrast (browser-only, one page) | Native GTK overlay (`overlay.py` + `watchdog.sh`) covering the whole screen, since Weasis is a separate window a page-based watermark could never reach |
| Status | Done, this is what's actually deployed today | Milestone "Weasis viewer migration", GitHub issues #3–#8 — see below for exactly what's shipped vs. still open |

Nothing about `orthanc`, `grading-api`, or the local dev quick-start below
changes between the two — only which workspace image you register in Kasm,
and which frontend page that image's `custom_startup.sh` points at.

## Quick start (local dev, no Kasm needed)

This is the fastest way to poke at `grading-api`/the grading UI without
installing Kasm at all — it exercises the same `orthanc` + `grading-api` +
`viewer` stack either flow uses, just through a plain browser tab instead of
a Kasm session.

```bash
cp .env.example .env
sed -i "s/^ORTHANC_PASSWORD=.*/ORTHANC_PASSWORD=$(openssl rand -hex 16)/" .env
sed -i "s/^GRADING_COORDINATOR_KEY=.*/GRADING_COORDINATOR_KEY=$(openssl rand -hex 32)/" .env
docker compose up -d --build
./scripts/fetch-public-samples.sh   # pulls in BRAINIX (~64MB, not committed to git)
./scripts/load-sample-studies.sh
```
Both `sed` lines are required — a real tester following an earlier version
of this section that only set `ORTHANC_PASSWORD` hit
`docker-compose.yml`'s own `GRADING_COORDINATOR_KEY:?...` guard failing
`docker compose up`, which cascaded into `load-sample-studies.sh` failing
too (nothing was actually running yet).

```
┌──────────────┐      ┌───────────────────────┐      ┌─────────────┐
│  Your browser│ ───► │ viewer (nginx:8080)   │ ───► │  orthanc    │
│  (stand-in   │      │ watermark.html +      │      │  (DICOM     │
│  for Kasm's  │      │ grading panel +       │      │  store,     │
│  streamed    │      │ reverse proxy         │      │  internal   │
│  session)    │      └───────────┬───────────┘      │  only)      │
└──────────────┘                  │                  └─────────────┘
                                   ▼
                       ┌───────────────────────┐
                       │  grading-api          │
                       │  (FastAPI + SQLite,   │
                       │  internal only)       │
                       └───────────────────────┘
```

**Opening bare `http://localhost:8080/` no longer works** — since the
session-token system shipped (see "Lung-RADS grading" below),
`watermark.html`'s own `/api/case` fetch has nothing to authenticate with
and unconditionally 401s, showing "Błąd ładowania: HTTP 401: Unauthorized"
in the panel instead of a case. This bit a real tester cold; the fix is to
mint a token first:

```bash
./scripts/mint-local-link.sh
```

This mints a `grading-api` session token (gated by `GRADING_COORDINATOR_KEY`
from `.env`, the same mechanism `scripts/create-session.py` uses for real
Kasm sessions) and prints a ready-to-open link, e.g.
`http://localhost:8080/?student_id=STU_LOCAL_TEST&session_id=sess_.../&token=...`.
Open that link — you should see the watermarked viewer wrapper
(`watermark.html`, the Chrome-flow page) with a rotating
`STUDENT_ID | SESSION_ID | timestamp` overlay, loading Orthanc Explorer 2
(browse to a study, then open it in the Stone Web Viewer) inside it.

Optional student/session IDs: `./scripts/mint-local-link.sh STU_12345
SESS_9921A3B`. The link's `token` is what actually authenticates every
`grading-api` call now — `student_id`/`session_id` in the URL are display-only
(the watermark text), same as in a real Kasm session. The link also carries
`orthanc_url` (default `http://localhost:8043/` for this local setup — the
auth-injecting proxy, not Orthanc's own port, see below) so the wrapper
knows which origin to iframe.

Want to see the Weasis-flow page (`grading-panel.html`) locally instead?
Take the same link `mint-local-link.sh` printed and swap the path:
`http://localhost:8080/grading-panel.html?student_id=...&session_id=...&token=...`
renders it the same way, minus Weasis itself (that only runs inside a
Kasm/XFCE session, or the Xvfb-based dev technique described further down)
— useful for iterating on the grading UI in isolation.

**Credentials**: there is no hardcoded Orthanc password anywhere in this
repo — `ORTHANC_PASSWORD` comes from `.env` (copied from the committed
`.env.example` placeholder, generated fresh above), and both the `orthanc`
and `viewer` containers read it from there at start. `docker-compose.yml`
refuses to start either service if it isn't set, rather than silently
falling back to a known default.

### Sample data

`sample-data/*.dcm` are [pydicom](https://github.com/pydicom/pydicom)'s own
bundled test fixtures (`CT_small.dcm`, `MR_small.dcm`) — synthetic/test files
used for exactly this purpose upstream, not real patient data, and safe to
commit to a public repo.

`sample-data/brainix/` is the classic **BRAINIX** MRI teaching study (232
instances / 7 series / ~64MB), pulled from Orthanc's own official public demo
server (`orthanc.uclouvain.be/demo`) via `scripts/fetch-public-samples.sh`.
It's a long-standing public/anonymized teaching dataset, not real patient
data — but at ~64MB it's **not committed to git** (see `.gitignore`); re-run
the fetch script to get it back after a fresh clone.

`sample-data/dicomlibrary_ct/` is a real, multi-hundred-slice **CT study**
(970 instances / 5 series / ~494MB) from
[dicomlibrary.com](https://www.dicomlibrary.com)'s own public "DICOM
Samples" gallery — the kind of large, realistic study the smaller fixtures
above can't stand in for (e.g. issue #8's real-data scrubbing/DLP test).
Confirmed anonymized directly via `pydicom` tag inspection before use
(`PatientName` = `"Anonymized^^"`, `PatientID` = `"0"`, no institution/
physician/accession), not just trusted from the site's own claim. **Not
committed to git** and **not fully automatable** like `brainix/` above —
`fetch-public-samples.sh`'s own comment explains why (dicomlibrary.com's
download link is a per-visit, 5-minute-expiring token, not a stable public
URL) and how to re-fetch it manually if this copy is ever lost.

Real studies still need the anonymization pipeline described in `CLAUDE.md`
(hard rule: *"All DICOM files must be anonymized and stripped of PHI before
ingestion"*) — that pipeline doesn't exist yet and is out of scope for this
MVP.

## Lung-RADS grading

A `grading-api` service (FastAPI + SQLite, `docker/grading-api/`)
implements the 3-stage state machine from the PM's spec, in Polish. It's the
same backend for both viewer flows — only the frontend page calling it
differs (`watermark.html` for Chrome, `grading-panel.html` for Weasis):

1. **Nauka (learning)** — free-text impression, submit, then the real
   reference report appears to cross-check against. Not graded — no NLP,
   no correctness scoring (matches CLAUDE.md's "no NLP or free-text
   grading" hard rule exactly).
2. **Ocena (assessment)** — structured Lung-RADS category (`0`–`4X`,
   dropdown) + "S" modifier (radio), submit, immediate correct/incorrect
   feedback against ground truth (revealed either way, right or wrong).
3. **Test (exam)** — same structured inputs, submit, **no** feedback at
   all until all test-stage cases are done, then a final score summary.

**In the Chrome flow**, the grading panel lives in `watermark.html` as a
sidebar next to the DICOM viewer iframe (same page, no separate route) — on
loading a case it deep-links the iframe to that one assigned study
(`ui/app/index.html#/filtered-studies?StudyInstanceUID=...`), not the full
patient list, matching the original design discussion's "Single DICOM Study
Isolation" requirement. **Worth knowing**: Orthanc's Explorer 2 has no true per-study
*viewer* deep-link (confirmed against its actual router source — no route
takes a study ID at all); this filters the list to one row, which still
needs one click from the student to open the viewer on it. The
`stone-webviewer` plugin this project assumed earlier isn't even installed
on this Orthanc image (confirmed via `GET /plugins`) — corrected once
actually tested against the real instance, not left as an assumption.

**In the Weasis flow**, there's no iframe at all — Weasis itself opens
directly on the assigned study (see "The two workspace images" below, issue
#4), and `grading-panel.html` only ever renders the form, in its own small
window beside it.

Ground truth + reference reports are **placeholder content** on the
existing sample studies (CT_small/MR_small/BRAINIX — none of which are
actually lung CTs) — one case per stage, purely to prove the mechanics work
end to end. Real curated content (500 studies, real ground truth, a real
radiologist's reference reports) is separate work, not yet started.

`grading-api` is internal-only (no host port, not on `kasm_default_network`
— nothing outside `viewer`'s nginx needs to reach it directly), reached via
a same-origin `/api/` proxy so the panel's `fetch()` calls need no CORS
handling. SQLite chosen deliberately over the Postgres the original design
discussion specified — lighter for proving out the mechanics now; migrating
later means changing a connection string, not the app logic. (Flagging
explicitly: this means CLAUDE.md's literal "Stratified random sampling via
PostgreSQL" hard rule doesn't hold today — no Postgres, and no real
stratified sampling yet either, since there's no real 500-study pool to
stratify. Worth revisiting CLAUDE.md's wording once this direction is
confirmed as lasting.)

**A real gap, now fixed**: every `grading-api` endpoint used to take
`student_id` as a plain, unauthenticated parameter — nothing bound it to
the caller's actual Kasm session, so any client on the network could read
or submit as *any* student ID. This mattered more than it might look: at
real cohort sizes (300-500 students), one compromised or malicious session
could manipulate many other students' Lung-RADS exam results, undermining
the whole assessment's validity.

Fixed with a server-issued session token: `POST /session` mints an
unguessable token bound to a `student_id`, but only for a caller that
supplies `GRADING_COORDINATOR_KEY` (a shared secret only
`scripts/create-session.py` knows — otherwise a student could just mint
their own token for anyone's ID and recreate the exact hole this closes).
Every other endpoint (`/case`, `/submit`, `/reset`, `/results`) now takes
that `token` instead, resolving the real `student_id` server-side; a
missing, unknown, or expired token (default TTL 8h, `GRADING_TOKEN_TTL_SECONDS`)
is rejected outright. `student_id`/`session_id` are still shown in the
watermark text — display was never the problem, trusting them for grading
actions was. `custom_startup.sh` (both images) bakes the token in the same
way it already baked in `STUDENT_ID`/`SESSION_ID`; `watermark.html`/
`grading-panel.html` read it from the query string. Minting a new token
for a `student_id` also revokes whatever token existed before it — a
coordinator re-minting a link they suspect leaked gets real revocation,
not just a second valid link.

## What's NOT in this MVP (on purpose)

Per the original design discussion's own recommendation ("About the
two-week MVP" section), all of this is deferred:

- Moodle / LTI 1.3 launch and grade passback.
- Payments (300 PLN), certificates, access expiry.
- Real stratified case sampling (50/50/30 across Lung-RADS classes, drawn
  from a real curated 500-study pool) — see "Lung-RADS grading" above for
  what's actually built: the 3-stage mechanics, with 1 placeholder case per
  stage rather than a real stratified pool.
- Admin dashboard, leaderboard, expert-review queue.

## Kasm Workspaces

### Installing Kasm itself

Kasm Workspaces Community Edition is installed on this machine (via
<https://kasm.com/docs/latest/install/single-server-install/>, using
`sudo bash kasm_release/install.sh` — a host-level change with its own
systemd service on port 443, deliberately not scripted here since it needs
an interactive sudo password; see git history for the exact steps if setting
this up somewhere else, e.g. the real Proxmox VM).

Everything below assumes Orthanc/viewer run on this same machine as Kasm.
Running them on a separate Proxmox VM/LXC instead? See
`docs/PROXMOX_DEPLOYMENT.md` and `docker-compose.remote-host.yml` — only the
networking/firewall piece differs, everything else here still applies.

**Kasm Workspaces Community Edition licensing, read before planning a real
cohort**: non-commercial/non-profit/personal use only (EULA §2.2) and capped
at 5 concurrent sessions — see `CLAUDE.md`'s hard rules. A real paid,
10-20+ concurrent deployment needs a paid tier and a legal/procurement
review first.

### The two workspace images

**Chrome** (`docker/kasm-workspace/`) — a Kasm Chrome workspace in kiosk
mode, pinned to `watermark.html`. Build/rebuild with:
```bash
docker build -t ipcmc/dicom-viewer:mvp docker/kasm-workspace
```
Then register it in the Kasm admin UI as a Workspace (type **Container**,
Docker Image `ipcmc/dicom-viewer:mvp`, registry blank since it's built
locally on the same Docker host Kasm's agent uses). This is what's actually
deployed and confirmed working end-to-end today.

**Weasis** (`docker/kasm-workspace-weasis/`, milestone "Weasis viewer
migration", GitHub issues #3–#8) — built on Kasm's lean `core-ubuntu-noble`
base (XFCE + KasmVNC only, not the bloated "desktop" bundle) with Weasis
4.7.3 installed from its own `.deb`. Build the same way:
```bash
docker build -t ipcmc/dicom-viewer-weasis:mvp docker/kasm-workspace-weasis
```
Registering it in the Kasm admin UI works the same as Chrome above (same
**Container** type, this image's tag); both images can be registered side
by side.

**How to run/test Weasis without a full Kasm session** — this is a real
native GUI app, so there's no plain-browser equivalent to the Chrome flow's
"just open the page" quick start. What actually verified every piece of
this image during development, and still works for re-verifying it:
```bash
# Inside a running container built from the image above (or any container
# with GTK3 + Weasis's own JRE), with an X display available:
Xvfb :99 -screen 0 1280x800x24 &
export DISPLAY=:99
xfwm4 &                      # a real window manager -- bare Xvfb has none,
                              # so "always on top" has nothing to test against
/opt/weasis/bin/Weasis        # or with a weasis:// URI, see custom_startup.sh
import -window root screenshot.png   # ImageMagick, to actually look at it
```
A real Kasm session already provides all of this (KasmVNC's own Xvnc +
xfwm4) — this is purely the technique for developing/debugging the image
without one.

What's confirmed working in this image, in the order it was built:

- **Issue #3 (New Kasm Image)**: launched under a virtual X display, the
  JVM starts, OSGi bundles load, the DICOM codec registers, and the main
  window renders correctly (DICOM Explorer panel, menus, the standard "not
  a certified medical device" disclaimer responds to a real click) — Weasis
  bundles its own JRE at `/opt/weasis/lib/runtime`, so no separate Java
  package is needed.
- **Issue #4 (Automatic Launch Without Extra Clicks)**:
  `docker/kasm-workspace-weasis/custom_startup.sh` fetches the current case
  from `grading-api` (same `/api/` proxy the Chrome flow uses) and launches
  Weasis straight into it via its `dicom:rs` command, with zero manual
  clicks — the disclaimer dialog is also suppressed at build time
  (`weasis.show.disclaimer=false` patched into Weasis's own config, since
  ephemeral Kasm containers get a fresh `$HOME` every session and would
  otherwise hit it every time). Getting `dicom:rs` to actually work took
  real debugging, worth recording:
  - It only works sent as a `weasis://` URI, not raw CLI tokens — Weasis's
    own main-argv command parser races the OSGi bundle that provides the
    command and silently no-ops otherwise (confirmed against
    `nroduit/Weasis`'s own launcher source).
  - The query needs an explicit `requestType=STUDY` ahead of `studyUID=`
    — without it, Weasis's request-type classification (it implements the
    IHE "Invoke Image Display" profile) falls through with no error.
  - **A real bug this surfaced, fixed as part of this issue, not a
    Weasis-side problem**: the auth-injecting proxy (`:8043`) was
    forwarding nginx's `$host` to Orthanc, which strips the port even
    when the original request had one — Orthanc's DICOMweb plugin embeds
    whatever Host it receives into every QIDO-RS response's
    `RetrieveURL`, so Weasis's *queries* worked but every subsequent
    *image download* connection-refused against the wrong port. Fixed by
    forwarding `$http_host` instead (see
    `docker/viewer/default.conf.template`).
- **Issue #5 (Grading Panel)**: `docker/viewer/grading-panel.html` — a
  trimmed-down `watermark.html` with the DICOM iframe removed, everything
  else (the 3-stage JS/API logic, the watermark tiling, the anti-tamper
  deterrents) reused unchanged. A new, separate file rather than an
  in-place edit — `watermark.html` is still the live page for the Chrome
  flow, gutting it would have broken that working flow mid-migration.
- **Issue #7 (Lock Down Weasis Native Save/Copy/Export)** — a direct answer
  to the context-menu/export concern raised on a reference screenshot: File
  > Export offered two items, confirmed via a real running session before
  disabling anything (not guessed from menu labels alone) —
  "Exporting view" (screenshot/clipboard copy of the current view, no
  matching preference exists anywhere in Weasis's own config, see below)
  and "DICOM" (raw export to local disk/CD/ISO). `docker/kasm-workspace-weasis/patch-weasis-config.py`
  patches `weasis.export.dicom`, `weasis.export.dicom.send`,
  `weasis.import.dicom`, `weasis.import.images`, and
  `weasis.import.dicom.qr` to `false`, and blanks the `felix.auto.start.110`
  bundle group entirely (not just hides it) — Felix never loads the DICOM
  Send/Q-R/ISO-writer bundles at all, so the underlying code doesn't exist
  at runtime. Consolidated with issue #4's own disclaimer patch in the same
  script, since both edit the same `base.json` file. Verified in the built
  image: all six preference values confirmed `false`/blanked via
  `docker cp`'d config inspection (not by running the full Kasm entrypoint,
  which starts a real desktop session rather than exiting).
  **Residual gap, not silently accepted**: "Exporting view" has no
  matching preference in Weasis's own config at all — confirmed against
  its actual source (`ActionW.java`'s `EXPORT_VIEW` is an unconditional,
  always-registered core action, not gated by any property). Saving a
  file this way stays trapped inside the ephemeral container regardless,
  since Kasm's own `allow_kasm_downloads` setting blocks that path — but
  the **"Clipboard" option was a real, confirmed exfiltration path, not
  hypothetical**: a live test showed an image copied this way landed on
  the real local clipboard, pasteable into an application outside the
  session entirely, disproving the assumption that Kasm's DLP already
  covered this. Root cause and fix are in the DLP section below.
- **Issue #6 (Watermark Overlay)**: `docker/kasm-workspace-weasis/overlay.py`
  is a transparent, always-on-top, click-through native window (GTK3, not a
  browser page — Weasis is a separate native window a page-based watermark
  could never cover) tiled with `STUDENT_ID | SESSION_ID | timestamp` text,
  paired with `watchdog.sh` so killing it just gets it relaunched within
  about a second. Verified for real, not just assumed:
  - **True transparency without a compositor**: no compositor (e.g.
    `picom`) runs in this XFCE/KasmVNC session by default, so this uses
    the X Shape extension instead (`Gdk.Window.shape_combine_region()`)
    — only the actual glyph pixels are part of the window at all.
    Confirmed with a screenshot against a solid-color test background:
    the color showed through everywhere except the rendered text.
  - **Genuine click-through**: `input_shape_combine_region()` set to an
    *empty* region, independent of the bounding shape above. Confirmed
    by placing a real clickable test button underneath and clicking
    directly on top of rendered watermark glyphs — the click reached the
    button every time.
  - **Stays on top of a real window manager**, not just bare Xvfb (which
    has no window manager at all, so nothing enforces stacking order):
    `Gtk.WindowType.POPUP` (override-redirect, outside window manager
    control entirely) plus `set_keep_above()`, tested under a real
    `xfwm4` session.
  - **The watchdog actually relaunches it**: killed the overlay process
    directly and confirmed a new one appeared within ~1 second, watermark
    coverage intact in a follow-up screenshot.
  - **A limitation flagged, not hidden**: without a compositor, CSS-style
    `mix-blend-mode:difference` (what `watermark.html`/`grading-panel.html`
    use for guaranteed contrast) has no real cross-window equivalent here
    — that trick only works within one browser's own rendering pipeline.
    This uses a dark-stroke + light-fill "halo" around each glyph
    instead (the same technique subtitle overlays use), which is legible
    against both light and dark content but isn't the same mathematical
    guarantee.

**Confirmed via the first real Kasm-issued session** (registered in the
admin UI, launched through `create-session.py`'s minted link — not just
Xvfb/manual container testing): Weasis actually launches on the assigned
study through a real session end to end. That same real test also caught
one bug Xvfb testing couldn't have (fixed): the watermark overlay measured
screen size once before KasmVNC's client-viewport resize happened, so it
covered only part of a real browser-sized screen — see git log for
`docker/kasm-workspace-weasis/overlay.py`.

**The grading panel gap that same test surfaced is now fixed**:
`docker/kasm-workspace-weasis/grading_panel_window.py` displays
`grading-panel.html` (issue #5) in its own window, launched (backgrounded)
by `custom_startup.sh` before the final `exec` into Weasis, pinned to the
right edge of the screen. Deliberately **not** a general-purpose browser
install (no Chromium/Firefox/Epiphany) — that would reopen exactly the
save/print/devtools attack surface issue #7 just closed, through a
different door. Instead: a single WebKit2GTK `WebView` with no browser
chrome at all (no address bar, no menu, nothing to build one from since
none exists), locked down via WebKit2's own documented APIs — context
menu suppressed, developer tools disabled, new-window/popup creation
vetoed, navigation restricted to this session's own origin (same-origin
`fetch`/XHR calls to `grading-api` are unaffected, only top-level page
navigation is restricted), plus a keyboard-shortcut blocklist (F12,
Ctrl+U/S/P, Ctrl+Shift+I/J/C) as defense in depth. See that file's own
header comment for the full rationale and what's explicitly *not* claimed
(same "deterrence, not prevention" framing as the watermark overlay).
Verified against a real `grading-api` session token, not just that it
renders: the actual Polish learning-stage form loaded correctly, complete
with the page's own DOM watermark tiling inside the window.

**Auto-tiled against Weasis, not left to manual resizing**:
`docker/kasm-workspace-weasis/arrange_windows.sh` (launched the same way,
backgrounded before the `exec`) resizes both Weasis's and the grading
panel's windows to split the screen cleanly — `wmctrl` turned out to
already be present in this base image (confirmed via `apt-cache policy`
before assuming otherwise). Two real gotchas found via live tests, not
assumed:
  - Weasis's main window opens already maximized
    (`_NET_WM_STATE_MAXIMIZED_HORZ`/`_VERT`, confirmed via `xprop`), and
    xfwm4 silently ignores a plain geometry resize request against a
    maximized window — a "successful" (exit-0) `wmctrl -r ... -e ...`
    call had zero visible effect until the maximized state was explicitly
    removed first.
  - An *earlier, one-shot* version of this script measured the screen
    size once at startup — but KasmVNC starts at a fixed default geometry
    and only resizes to the client's real browser-viewport size *after* a
    real client connects (confirmed via a live session's own VNC log:
    "Got request for framebuffer resize to 1920x950", logged well after
    this script's first pass had already run and tiled both windows
    against the smaller, stale geometry — the exact same root cause as
    the watermark overlay bug fixed earlier). Fixed the same way as that
    bug: this now re-reads the screen size and re-applies both windows'
    geometry every 2 seconds for the life of the session, not once —
    `wmctrl` calls are cheap and idempotent, so this is a harmless no-op
    once the geometry is already correct, and self-corrects within one
    cycle of any later resize. Verified against a scripted, stubbed
    resize (not just reasoned about): a fake `xrandr` reporting a
    different resolution on successive calls confirmed the script
    re-tiles both windows to the new size on its very next cycle.

**Still outstanding**:
- **Issue #8 (Regression + Real-Data Scrubbing Test)** — re-verify DLP still
  functions on this new image type, test real-world scrubbing smoothness on
  an actual multi-hundred-slice CT study (current fixtures are too small),
  update documentation. The unit-test slice of this (see Testing below) is
  already done; the DLP/real-data piece is not.

### Networking

`docker-compose.yml` attaches `orthanc`/`viewer` to `kasm_default_network`
(external, created by the Kasm installer) so Kasm's session containers
reach them by container name — `http://ipcmc-viewer:8080/` and the
auth-injecting proxy at `http://ipcmc-viewer:8043/` (see
`docker/viewer/default.conf.template` for why Orthanc isn't reached
directly: an iframe/Weasis can't answer its Basic Auth challenge).
`grading-api` stays off this network entirely — nothing outside `viewer`'s
nginx needs to reach it.

### Per-student links

`scripts/create-session.py` first mints a `grading-api` session token
(`POST /session`, see "Lung-RADS grading" above — this is the only place
that ever happens, gated by `GRADING_COORDINATOR_KEY`), then calls Kasm's
public API (`/api/public/request_kasm`) to mint a one-off session with
`STUDENT_ID`/`SESSION_ID`/`GRADING_TOKEN` all baked into its environment —
that's what each image's `custom_startup.sh` reads to launch its viewer
(Chrome or Weasis) with the right identity and credential already in
place. Needs an API key from the admin UI (**Settings → Developers → Add
API Key**, with the **"Users Auth Session"** and **"User"** permissions
enabled — `request_kasm` 403s without both), the target workspace's
image_id (from its edit URL in the admin UI), and the same
`GRADING_COORDINATOR_KEY` value the running stack's `.env` has:
```bash
KASM_SERVER=https://your-kasm-host \
KASM_API_KEY=... KASM_API_KEY_SECRET=... KASM_IMAGE_ID=... \
GRADING_COORDINATOR_KEY=... \
python3 scripts/create-session.py --student-id STU_12345 --insecure  # drop --insecure with a real cert
```
`GRADING_API_URL` (default `http://localhost:8080/`) points at wherever
`viewer`'s `/api/` proxy is reachable from — override it if this script
runs somewhere other than the Docker host itself (e.g. the separate-Proxmox
setup, `docs/PROXMOX_DEPLOYMENT.md`).

Prints a ready-to-share `link` — no login required, it's pre-authenticated
via a session token Kasm generates. The script also tries a readiness
check (`get_kasm_status`) but treats it as best-effort: a scoped API key
commonly lacks the separate "impersonate another user" permission that
call needs for an anonymous/other user, so it's fine if that part logs a
non-fatal warning and skips straight to printing the link.

### DLP settings

Clipboard, file upload/download, printing — the on-prem equivalent of
AppStream's "Stack Policy" — configured on the **Group** anonymous sessions
land in (`Access → Groups → All Users → Settings`), not per-workspace:
`allow_kasm_clipboard_down/up/seamless`, `allow_kasm_downloads`,
`allow_kasm_uploads`, `allow_kasm_printing`, `allow_kasm_sharing`,
`allow_kasm_webcam`, `allow_kasm_microphone`, `allow_kasm_gamepad`,
`allow_kasm_audio` all set to `False`. Verified two ways, not just trusted:
queried `group_settings` directly in Kasm's own Postgres DB to confirm the
stored values, then launched a real test session and confirmed inside the
container that
`KASM_SVC_DOWNLOADS`/`KASM_SVC_UPLOADS`/`KASM_SVC_PRINTER` are `0` — those
services aren't just hidden in the UI, they never start.

**A real gap this same testing found, now fixed**: the admin-UI clipboard
toggles above only govern *plain-text* clipboard sync — confirmed correctly
blocking a real attempt to copy text out of a session. But KasmVNC has a
second, independent clipboard-DLP layer for *rich/binary* MIME types
(`data_loss_prevention.clipboard.allow_mimetypes` in `kasmvnc.yaml`), whose
documented default already includes `image/png` — and in practice, an image
copied to the clipboard from inside a session (confirmed via Weasis's own
"Export → Clipboard" feature, which has no config of its own to disable —
see issue #7 above) landed on the real local clipboard, pasteable into an
application outside the session entirely. Kasm's own docs don't explain how
this setting relates to the simpler admin-UI toggles. Fixed by baking a
`kasmvnc.yaml` with `allow_mimetypes: []` directly into **both** workspace
images (`docker/kasm-workspace/kasmvnc.yaml`,
`docker/kasm-workspace-weasis/kasmvnc.yaml`) — confirmed both at the
mechanism level (the real `Xvnc` process's own `-DLP_ClipTypes` flag now
shows empty instead of the previous default) and end to end, via a real
session: the exact same "Export → Clipboard" → paste-into-a-local-app
attempt that previously escaped the session is now blocked. Also flagged
upstream, since the underlying gap is in Kasm/KasmVNC itself, not this
project's code:
[kasmtech/workspaces-issues#912](https://github.com/kasmtech/workspaces-issues/issues/912).

**Verified against the Weasis image via a real click-through test**
(issue #8): the checks above (downloads/uploads/printing services not
starting, text clipboard blocked) all held. The image-clipboard gap above
is the one thing that real testing caught that assuming coverage from the
Chrome-image checks wouldn't have.

## Apache Guacamole flow (PoC, alternative to Kasm)

**Why this exists**: Kasm Workspaces Community Edition is capped at 5
concurrent sessions and restricted to non-commercial/non-profit/personal
use (EULA §2.2, see `CLAUDE.md`) — a real blocker for any deployment
beyond a small pilot, needing a paid tier + legal/procurement review
before scaling further. Apache Guacamole is Apache-licensed with no such
cap. This is a proof of concept evaluated *alongside* the Kasm flow, not
a replacement yet — same backend (`orthanc`/`grading-api`/`viewer`,
untouched), same Lung-RADS mechanics, just a different way of streaming
Weasis to a browser.

**Deliberately basic-functionality only, not a hardened flow**: no
watermark overlay, no Weasis export/import lockdown, no window
auto-tiling, no per-container VNC password, and `--security-opt
seccomp=unconfined` on every session container (see below). **Do not
point this at real patient data or run it as a real deployment** — it
exists to prove the streaming mechanism works, matching exactly what was
asked of this PoC; hardening it to Kasm-flow parity is separate, future
work.

### Architecture

```
scripts/provision-guacamole-session.py
        │
        ├─► grading-api POST /session (mints a token, same as create-session.py)
        ├─► docker run (a fresh, per-student Weasis+TigerVNC container)
        └─► Guacamole REST API (creates a real user + VNC connection)
                        │
   Guacamole webapp ◄───┘ (Postgres-backed JDBC auth, its own DB --
        │                  separate from grading-api's own SQLite)
        ▼
      guacd  ──── VNC (raw RFB) ────►  docker/guacamole-weasis/ container
                                       (TigerVNC + openbox + Weasis +
                                        a plain browser tab for the
                                        grading panel)
```

- **`docker-compose.guacamole.yml`** — an overlay file (combine with the
  base compose file, not standalone): `guacd`, the Guacamole webapp, and
  its own Postgres (JDBC auth backend — a real, separate requirement from
  grading-api's own database, not a redundant duplicate). Reuses the base
  file's existing `ipcmc-internal` network — no dependency on
  `kasm_default_network` or Kasm being installed at all.
- **`docker/guacamole-weasis/`** — a new workspace image, plain
  `ubuntu:24.04` (not any Kasm-maintained base). **TigerVNC, not
  KasmVNC** — confirmed via direct Guacamole-protocol-level testing (not
  assumed from docs) that `guacd`'s VNC client cannot complete an RFB
  handshake against KasmVNC at all: it serves VNC exclusively over its
  own websocket-wrapped protocol, never a raw RFB TCP port, which is what
  `guacd` needs.
- **`scripts/provision-guacamole-session.py`** / **`teardown-guacamole-session.py`**
  — Guacamole's own equivalent of `create-session.py`. Guacamole has no
  built-in per-session container provisioning (unlike Kasm's agent) and
  no supported anonymous quick-link mechanism (checked before building
  this, not assumed — Guacamole's own "quickconnect" extension explicitly
  requires prior authentication, and the only anonymous-login extension
  that exists is an unmaintained third-party fork with no version-
  compatibility guarantee). A real per-student Guacamole user + Postgres-
  backed JDBC auth, combined with Guacamole's documented URL-parameter
  auto-login (`#/?username=...&password=...`), is the officially-supported
  way to get a single, no-further-login-prompt link per student.
- **`scripts/guacamole-iac.sh`** — brings the whole stack up from scratch
  on a fresh host. Plain bash on purpose (not Ansible yet) — the agreed
  starting point before considering a migration once this PoC's shape has
  settled.

### Setup

```bash
./scripts/guacamole-iac.sh
```

Then mint a per-student link:
```bash
GRADING_COORDINATOR_KEY=$(grep GRADING_COORDINATOR_KEY .env | cut -d= -f2) \
  python3 scripts/provision-guacamole-session.py --student-id STU_12345
```
and tear it down once finished:
```bash
python3 scripts/teardown-guacamole-session.py --student-id STU_12345 --session-id <from the link>
```

### Confirmed working, via real tests, not assumed

- Full pipeline verified with a direct Guacamole-protocol test (bypassing
  the webapp entirely): `guacd` receives real display data
  (`img`/`rect`/`blob` instructions) from the TigerVNC container — this is
  what confirmed KasmVNC's incompatibility and TigerVNC's compatibility
  in the first place.
- Full browser-driven E2E tests (`docker/guacamole/tests/test_guacamole_e2e.py`,
  `./scripts/test-guacamole-e2e.sh`): a real headless Chromium, driven by
  Playwright, confirms the auto-login link logs straight in with no login
  form shown, and that the remote-display canvas renders real (non-blank)
  pixel data. Visually confirmed during development (a saved screenshot,
  not kept in the repo) that this is a correctly-rendered Weasis session
  showing the assigned study, not just "some canvas content."
- `docker/guacamole-weasis/launch-session.bats` — same BATS stubbing
  technique as the Kasm flow's own launcher tests: the case-fetch/
  `dicom:rs` URI construction (copied verbatim from
  `docker/kasm-workspace-weasis/custom_startup.sh` — identical regardless
  of which remote-display tech streams the pixels) and the grading-panel
  URL construction both verified.

### Real bugs found and fixed along the way

- Weasis's own `.deb` postinst script calls `xdg-desktop-menu`, which
  fails outright ("No writable system menu directory found") on a
  minimal image without a full desktop environment's usual menu
  directories already present — the Kasm base image never hit this
  because its full XFCE stack provisions them implicitly.
- `java.awt.Desktop.getDesktop()` throws
  `UnsupportedOperationException` on a bare `openbox` window manager
  without `XDG_CURRENT_DESKTOP` set — Weasis calls this at startup and
  crashed outright without it.
- `epiphany` (the grading-panel browser) needs unprivileged user
  namespaces for its own internal `bubblewrap` sandboxing, which this
  container's default seccomp profile blocks — see the security
  trade-offs below.

### Known security trade-offs (tracked, not accidental)

- **`--security-opt seccomp=unconfined`** on every session container —
  needed for `epiphany`'s own internal sandboxing (`bubblewrap`/user
  namespaces). A real hardening pass should replace this with a narrower
  custom seccomp profile permitting just the specific syscalls needed,
  not a blanket disable.
- **No VNC password** (`-SecurityTypes None`) — safe only because these
  containers are never published to the host and are reachable
  exclusively by `guacd` over the internal `ipcmc-internal` network.
- **Guacamole's default admin credentials** (`guacadmin`/`guacadmin`) —
  change these before anything beyond a local PoC; `scripts/guacamole-iac.sh`
  doesn't do this for you.
- **No automatic idle-session teardown** — unlike Kasm's own agent,
  nothing here detects an abandoned session and cleans it up
  automatically; `teardown-guacamole-session.py` must be run explicitly
  (or scheduled) or containers/Guacamole users accumulate over time.

### Guacamole itself: bugs/conflicts checked, nothing new to report

Researched before building (not assumed): one known CVE
([CVE-2024-35164](https://github.com/apache/guacamole-server/security/advisories/GHSA-8wh3-jcvc-qrmq),
an RCE in the terminal emulator's handling of SSH/telnet console codes,
fixed in 1.6.0) — irrelevant to VNC-only usage here, but confirms pinning
`guacd`/`guacamole` at `1.6.0` (already done) rather than an older
version. The KasmVNC incompatibility above is a genuine architectural
difference between two independent projects' own protocol choices, not a
Guacamole bug — nothing new was found worth filing against Guacamole's
own tracker for this work.

## Security note (read this before assuming more than it does)

Per the original design discussion: **nothing here, or in the real
Kasm deployment, actually prevents someone from photographing their screen.**
The watermark is deliberately an attribution/deterrence control, not a
prevention control — that distinction needs to be in whatever acceptable-use
agreement participants sign, not just in this code. What this MVP *does*
enforce:

- Raw DICOM pixels never reach a browser outside the internal Docker network
  (Orthanc has no published port beyond `127.0.0.1`).
- Every session's viewer carries a sparse, tiled, timestamped identity
  watermark (student/session ID baked in server-side via the Kasm API, not
  user-controllable JS state) — DOM-based with
  `mix-blend-mode:difference` for the Chrome flow, a native GTK overlay
  using the X Shape extension for the Weasis flow — re-rendered every
  second either way.
- A DOM-tamper check (Chrome flow) reloads the page if the watermark is
  hidden or removed, and a watchdog process (Weasis flow) relaunches the
  overlay within ~1 second if it's killed — deterrents, not guarantees
  (disabling JS entirely, or having root in the container, defeats them
  respectively — same as noted in the docs).

## Testing

Four scripts, all run in CI (`.github/workflows/ci.yml`) on every push/PR
as four parallel jobs (`lint`, `unit-test`, `shell-test`, `smoke-test`, all
depending only on `lint` so they run concurrently, not serialized):

- `scripts/lint.sh` — bash syntax check on every script, `docker compose
  config` validation on both compose files. No infrastructure needed, safe
  to run anytime.
- `scripts/test-grading-api.sh` (issues #8, and the session-token fix) —
  unit tests for `grading-api`'s 3-stage state machine and its session-
  token authorization (`docker/grading-api/tests/test_state_machine.py`,
  21 tests), driven through FastAPI's own `TestClient` against a fresh,
  isolated SQLite file per test — no Docker, no real stack, runs in well
  under a second. These exist specifically to protect the invariants this
  project keeps stating in prose but never had automated coverage for:
  a token is required everywhere and only `POST /session` (coordinator-key
  gated) can mint one; an invalid, unknown, or expired token is rejected;
  minting a new token for a `student_id` revokes whatever came before it;
  ground truth/reference reports never leak before the stage that reveals
  them; the test stage reveals nothing at all; progress advances correctly
  case→case→stage→"complete"; two students' state never crosses; and the
  existing input validation (stage mismatches, `time_spent_seconds` range)
  actually behaves as documented. Verified the suite itself, not just that
  it's green: deliberately broke the token-expiry check in `main.py`,
  confirmed exactly one test failed (the one guarding that invariant,
  nothing else), then reverted.
- `scripts/test-shell-scripts.sh` (issue #16) — BATS tests (20 tests total)
  for the Kasm workspace launcher scripts. No Docker, no real
  Kasm/Weasis/grading-api needed: the scripts under test gained small,
  production-inert seams (`CHROME_BIN`/`WEASIS_BIN`/`OVERLAY_SCRIPT`/
  `WATCHDOG_LOG`, all unset in production) so a test can stub the actual
  binary — a fake executable that just captures its own argv to a file —
  instead of launching a real browser, Weasis, or GTK overlay.
  - `docker/kasm-workspace/custom_startup.bats` (7 tests) and
    `docker/kasm-workspace-weasis/custom_startup.bats` (9 tests): the
    Weasis suite also stubs `curl` (via `PATH`) to stand in for
    `grading-api`'s response, while the real `python3` still runs the
    actual `dicom:rs` URI-building logic being tested — that's the whole
    point, verifying the real percent-encoding behavior that took real
    debugging to get right (issue #4), not mocking it away. Verified the
    suite itself the same way as `test-grading-api.sh`: deliberately
    removed `requestType=STUDY` from the built URI, reran, confirmed
    exactly one test failed, then reverted. Also fixed a small real
    inconsistency found while writing these: `docker/kasm-workspace/
    custom_startup.sh`'s `VIEWER_URL` had no `:?` guard (unlike
    `ORTHANC_URL` right next to it), so a genuinely missing value failed
    with bash's own generic `VIEWER_URL: unbound variable` instead of a
    message actually pointing at the problem — now guarded the same way.
    Both suites also gained a `GRADING_TOKEN` seam and an assertion the
    Weasis suite's `curl` call authenticates with `?token=`, never the old
    `?student_id=` (the session-token fix's own regression coverage here).
  - `docker/kasm-workspace-weasis/watchdog.bats` (4 tests): since the
    script under test is a deliberate infinite loop, every test bounds it
    with `timeout` rather than waiting for it to exit on its own. **Found
    and fixed a real bug writing this test**: the logged exit code was
    always `0`, regardless of what the overlay actually exited with —
    `$(date ...)` runs its own command inside the same `echo`'s string
    and overwrites `$?` before the later `$?` in that string gets
    expanded, clobbering the real exit status before it was ever read.
    Fixed by capturing `$?` into a variable immediately after the command
    it belongs to. Verified the same way: confirmed the test fails
    against the original code, passes against the fix.
- `scripts/smoke-test.sh` — brings the main stack up for real (building
  images, stubbing `kasm_default_network` if it doesn't exist) and checks
  the HTTP status codes that were, until this was added, verified by hand
  after every change: Orthanc healthy, the watermarked viewer wrapper
  loads, the auth-injecting proxy actually injects auth (307, not 401),
  that Orthanc's DICOMweb `RetrieveURL` actually includes the proxy's port
  (issue #4's regression check — a status-code-only check would never have
  caught this, since the QIDO query itself always returned 200 regardless
  of the bug), and (the session-token fix's own regression check) that
  `POST /api/session` actually mints a usable token, that an invalid token
  is rejected with 401, and that `POST /api/session` itself is rejected
  without the coordinator key.
  **Tears the stack down with `docker compose down -v` when it's done** —
  don't run this against an environment with data you care about; it's
  meant for a disposable/CI environment.

Deliberately not covered by any of these (needs real Kasm infrastructure a
CI runner doesn't have, stays manual): actually launching a Kasm session,
`create-session.py` against a live instance, DLP settings — see this
project's own commit history for how each of those was actually verified.
(Each `custom_startup.sh`'s own logic — the command it builds, not an
actual Kasm session launching it — *is* now covered, by the BATS suite
above.)

## Repo layout

```
CLAUDE.md                             hard rules for this project (do not violate)
.env.example                          copy to .env and fill in a real ORTHANC_PASSWORD (gitignored)
docker-compose.yml                    orthanc + grading-api + viewer, for local testing
docker-compose.remote-host.yml        variant for running orthanc/viewer on a separate Proxmox VM/LXC
docker/orthanc/                       Orthanc config (no credentials in here -- see .env.example)
docker/viewer/                        nginx (config templated, auth token computed from env) +
                                       watermark.html (Chrome flow) + grading-panel.html (Weasis flow)
docker/grading-api/                   Lung-RADS 3-stage grading mechanics (FastAPI + SQLite)
docker/grading-api/tests/             unit tests (scripts/test-grading-api.sh)
docker/kasm-workspace/                Chrome-based Kasm workspace image -- the one actually deployed
docker/kasm-workspace-weasis/         Weasis-based workspace image -- milestone in progress, issues #3-#8
docker-compose.guacamole.yml           Guacamole PoC overlay (guacd + webapp + Postgres) -- combine with
                                       docker-compose.yml, doesn't stand alone
docker/guacamole/postgres-init/       Guacamole's own official Apache-licensed JDBC schema
docker/guacamole/tests/               full browser-driven E2E tests (scripts/test-guacamole-e2e.sh)
docker/guacamole-weasis/              basic-functionality-only Weasis+TigerVNC image for the Guacamole flow
                                       -- not security-hardened, see README's own Guacamole section
sample-data/                          public-domain sample DICOM files
scripts/fetch-public-samples.sh       pulls larger public teaching studies (BRAINIX) into sample-data/
scripts/load-sample-studies.sh        uploads sample-data/ (recursively) into Orthanc
scripts/create-session.py             mints a per-student Kasm session link (tested against a live instance)
scripts/mint-local-link.sh            mints a grading-api token for the no-Kasm Quick Start (localhost:8080/... needs a token now, not just student_id)
scripts/guacamole-iac.sh              brings up the whole Guacamole PoC stack from scratch
scripts/provision-guacamole-session.py mints a per-student Guacamole session link (Kasm-free flow)
scripts/teardown-guacamole-session.py tears down a Guacamole session (container + Guacamole user/connection)
scripts/lint.sh                       bash syntax + compose config validation (CI)
scripts/test-grading-api.sh           grading-api unit tests via pytest (CI)
scripts/test-shell-scripts.sh         Kasm + Guacamole launcher script tests via BATS (CI)
scripts/test-guacamole-e2e.sh         full browser-driven E2E test for the Guacamole flow (not in CI --
                                       needs the full stack + built images already up, see its own comment)
scripts/smoke-test.sh                 full-stack integration check (CI)
.github/workflows/ci.yml              the CI jobs above, run on every push/PR
```
