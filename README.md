# DICOM Viewer — MVP

Minimal, runnable proof-of-concept for the IP_CMC Lung-RADS training platform
described in `Dokumentacja/`. See `CLAUDE.md` for the hard rules this project
must never violate.

**Scope of this MVP** (deliberately narrow — see "What's NOT in this MVP" below):
a DICOM store with a couple of sample studies, a web-based viewer with a
forensic watermark baked in, served through a container that's meant to sit
behind Kasm Workspaces so each viewer session comes from its own unique,
individually-issued link. No Moodle, no payments, no grading, no stratified
sampling yet.

**Setting up your own copy?** See `docs/LOCAL_SETUP_GUIDE.md` for a
complete, step-by-step walkthrough (no assumed context) — the sections
below are more of a technical decisions log than an onboarding doc.
**Just want the shape of it?** See `docs/ARCHITECTURE.md` for current-state
Mermaid diagrams (session flow + deployment topology).

## What's actually running today (this machine, via Docker)

```
┌──────────────┐      ┌───────────────────────┐      ┌─────────────┐
│  Your browser│ ───► │ viewer (nginx:8080)   │ ───► │  orthanc    │
│  (stand-in   │      │ watermark.html +      │      │  (DICOM     │
│  for Kasm's  │      │ reverse proxy         │      │  store,     │
│  streamed    │      └───────────────────────┘      │  internal   │
│  session)    │                                     │  only)      │
└──────────────┘                                     └─────────────┘
```

Orthanc is **not** published on any host port — only the `viewer` container
can reach it, over the internal `ipcmc-internal` Docker network. That's the
same "nothing but pixels leaves the isolated tier" model the docs converge on
for the real Kasm deployment.

### Run it

```bash
cp .env.example .env && sed -i "s/^ORTHANC_PASSWORD=.*/ORTHANC_PASSWORD=$(openssl rand -hex 16)/" .env
docker compose up -d --build
./scripts/fetch-public-samples.sh   # pulls in BRAINIX (~64MB, not committed to git)
./scripts/load-sample-studies.sh
```

Then open **http://localhost:8080/** — you should see the watermarked viewer
wrapper with a rotating `STUDENT_ID | SESSION_ID | timestamp` overlay, loading
Orthanc Explorer 2 (browse to a study, then open it in the Stone Web Viewer)
inside it.

Change the query string to simulate different individual sessions, e.g.:
`http://localhost:8080/?student_id=STU_12345&session_id=SESS_9921A3B`.
This is exactly what `scripts/create-session.py` + `custom_startup.sh` do
automatically once Kasm is wired up (see below) — it's the mechanism behind
"one individual link per person." The same query string also carries `orthanc_url` (default
`http://localhost:8043/` for this local setup — the auth-injecting proxy, not
Orthanc's own port, see below) so the wrapper knows which origin to iframe.

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

Real studies still need the anonymization pipeline described in `CLAUDE.md`
(hard rule: *"All DICOM files must be anonymized and stripped of PHI before
ingestion"*) — that pipeline doesn't exist yet and is out of scope for this
MVP.

## Lung-RADS grading

A new `grading-api` service (FastAPI + SQLite, `docker/grading-api/`)
implements the 3-stage state machine from the PM's spec, in Polish:

1. **Nauka (learning)** — free-text impression, submit, then the real
   reference report appears to cross-check against. Not graded — no NLP,
   no correctness scoring (matches CLAUDE.md's "no NLP or free-text
   grading" hard rule exactly).
2. **Ocena (assessment)** — structured Lung-RADS category (`0`–`4X`,
   dropdown) + "S" modifier (radio), submit, immediate correct/incorrect
   feedback against ground truth.
3. **Test (exam)** — same structured inputs, submit, **no** feedback until
   all test-stage cases are done, then a final score summary.

The grading panel lives in `watermark.html` as a sidebar next to the DICOM
viewer iframe (same page, no separate route) — on loading a case it
deep-links the iframe to that one assigned study
(`ui/app/index.html#/filtered-studies?StudyInstanceUID=...`), not the full
patient list, matching `Dokumentacja/`'s "Single DICOM Study Isolation"
requirement. **Worth knowing**: Orthanc's Explorer 2 has no true per-study
*viewer* deep-link (confirmed against its actual router source — no route
takes a study ID at all); this filters the list to one row, which still
needs one click from the student to open the viewer on it. The
`stone-webviewer` plugin this project assumed earlier isn't even installed
on this Orthanc image (confirmed via `GET /plugins`) — corrected once
actually tested against the real instance, not left as an assumption.

Ground truth + reference reports are **placeholder content** on the
existing sample studies (CT_small/MR_small/BRAINIX — none of which are
actually lung CTs) — one case per stage, purely to prove the mechanics work
end to end. Real curated content (500 studies, real ground truth, a real
radiologist's reference reports) is separate work tracked in `Dokumentacja/`.

`grading-api` is internal-only (no host port, not on `kasm_default_network`
— nothing outside `viewer`'s nginx needs to reach it directly), reached via
a same-origin `/api/` proxy so the panel's `fetch()` calls need no CORS
handling. SQLite chosen deliberately over the Postgres `Dokumentacja/`
originally specified — lighter for proving out the mechanics now; migrating
later means changing a connection string, not the app logic. (Flagging
explicitly: this means CLAUDE.md's literal "Stratified random sampling via
PostgreSQL" hard rule doesn't hold today — no Postgres, and no real
stratified sampling yet either, since there's no real 500-study pool to
stratify. Worth revisiting CLAUDE.md's wording once this direction is
confirmed as lasting.)

## What's NOT in this MVP (on purpose)

Per the docs' own recommendation (`Dokumentacja/AI_context_Documentation_DICOM.txt`,
"About the two-week MVP" section), all of this is deferred:

- Kasm Workspaces itself is not installed yet — this repo only prepares what
  it needs (the workspace image + session-minting script below).
- Moodle / LTI 1.3 launch and grade passback.
- Payments (300 PLN), certificates, access expiry.
- Real stratified case sampling (50/50/30 across Lung-RADS classes, drawn
  from a real curated 500-study pool) — see "Lung-RADS grading" below for
  what's actually built: the 3-stage mechanics, with 1 placeholder case per
  stage rather than a real stratified pool.
- Admin dashboard, leaderboard, expert-review queue.

## Kasm Workspaces: real per-student links

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

What's wired up, end to end, and confirmed working:

1. **Custom workspace image** (`docker/kasm-workspace/`): a Kasm Chrome
   workspace in kiosk mode, pinned to the watermarked viewer. Build/rebuild
   with `docker build -t ipcmc/dicom-viewer:mvp docker/kasm-workspace`, then
   register it in the Kasm admin UI as a Workspace (type **Container**,
   Docker Image `ipcmc/dicom-viewer:mvp`, registry blank since it's built
   locally on the same Docker host Kasm's agent uses).
2. **Networking**: `docker-compose.yml` attaches `orthanc`/`viewer` to
   `kasm_default_network` (external, created by the Kasm installer) so Kasm's
   session containers reach them by container name — `http://ipcmc-viewer:8080/`
   and the auth-injecting proxy at `http://ipcmc-viewer:8043/` (see
   `docker/viewer/default.conf.template` for why Orthanc isn't reached
   directly: an iframe can't answer its Basic Auth challenge).
3. **Per-student links** (`scripts/create-session.py`): calls Kasm's public
   API (`/api/public/request_kasm`) to mint a one-off session with
   `STUDENT_ID`/`SESSION_ID` baked into its environment — that's what
   `custom_startup.sh` reads to launch Chrome with the right watermark
   identity already in the URL. Needs an API key from the admin UI
   (**Settings → Developers → Add API Key**, with the **"Users Auth Session"**
   and **"User"** permissions enabled — `request_kasm` 403s without both) and
   the workspace's image_id (from its edit URL in the admin UI):
   ```bash
   KASM_SERVER=https://your-kasm-host \
   KASM_API_KEY=... KASM_API_KEY_SECRET=... KASM_IMAGE_ID=... \
   python3 scripts/create-session.py --student-id STU_12345 --insecure  # drop --insecure with a real cert
   ```
   Prints a ready-to-share `link` — no login required, it's pre-authenticated
   via a session token Kasm generates. The script also tries a readiness
   check (`get_kasm_status`) but treats it as best-effort: a scoped API key
   commonly lacks the separate "impersonate another user" permission that
   call needs for an anonymous/other user, so it's fine if that part logs a
   non-fatal warning and skips straight to printing the link.

4. **DLP settings** (clipboard, file upload/download, printing — the
   on-prem equivalent of AppStream's "Stack Policy"): configured on the
   **Group** anonymous sessions land in (`Access → Groups → All Users →
   Settings`), not per-workspace — `allow_kasm_clipboard_down/up/seamless`,
   `allow_kasm_downloads`, `allow_kasm_uploads`, `allow_kasm_printing`,
   `allow_kasm_sharing`, `allow_kasm_webcam`, `allow_kasm_microphone`,
   `allow_kasm_gamepad`, `allow_kasm_audio` all set to `False`. Verified two
   ways, not just trusted: queried `group_settings` directly in Kasm's own
   Postgres DB to confirm the stored values, then launched a real test
   session and confirmed inside the container that
   `KASM_SVC_DOWNLOADS`/`KASM_SVC_UPLOADS`/`KASM_SVC_PRINTER` are `0` — those
   services aren't just hidden in the UI, they never start. Clipboard
   restriction is enforced separately, per-session, by Kasm's proxy checking
   the group permission — not a container env flag.

## Security note (read this before assuming more than it does)

Per the design discussion in `Dokumentacja/`: **nothing here, or in the real
Kasm deployment, actually prevents someone from photographing their screen.**
The watermark is deliberately an attribution/deterrence control, not a
prevention control — that distinction needs to be in whatever acceptable-use
agreement participants sign, not just in this code. What this MVP *does*
enforce:

- Raw DICOM pixels never reach a browser outside the internal Docker network
  (Orthanc has no published port).
- Every session's viewer page carries a sparse, tiled, timestamped identity
  watermark (student/session ID baked in server-side via the Kasm API, not
  user-controllable JS state), rendered with `mix-blend-mode:difference` so
  it stays visible against light or dark content and re-rendered every
  second.
- A DOM-tamper check reloads the page if the watermark is hidden or
  removed — a deterrent, not a guarantee (disabling JS entirely defeats it,
  same as noted in the docs).

## Testing

Two scripts, both run in CI (`.github/workflows/ci.yml`) on every push/PR:

- `scripts/lint.sh` — bash syntax check on every script, `docker compose
  config` validation on both compose files. No infrastructure needed, safe
  to run anytime.
- `scripts/smoke-test.sh` — brings the main stack up for real (building
  images, stubbing `kasm_default_network` if it doesn't exist) and checks
  the HTTP status codes that were, until this was added, verified by hand
  after every change: Orthanc healthy, the watermarked viewer wrapper
  loads, the auth-injecting proxy actually injects auth (307, not 401).
  **Tears the stack down with `docker compose down -v` when it's done** —
  don't run this against an environment with data you care about; it's
  meant for a disposable/CI environment.

Deliberately not covered by either (needs real Kasm infrastructure a CI
runner doesn't have, stays manual): actually launching a Kasm session,
`custom_startup.sh`, `create-session.py` against a live instance, DLP
settings — see this project's own commit history for how each of those was
actually verified.

## Repo layout

```
CLAUDE.md                     hard rules for this project (do not violate)
Dokumentacja/                 source design discussion + radiology requirements
.env.example                  copy to .env and fill in a real ORTHANC_PASSWORD (gitignored)
docker-compose.yml            Orthanc + watermarked viewer, for local testing
docker/orthanc/               Orthanc config (no credentials in here -- see .env.example)
docker/viewer/                nginx (config templated + auth token computed from env, not hardcoded) + the watermark wrapper page + grading panel
docker/grading-api/           Lung-RADS 3-stage grading mechanics (FastAPI + SQLite)
docker/kasm-workspace/        custom Kasm workspace image (build after Kasm is installed)
sample-data/                  public-domain sample DICOM files
scripts/fetch-public-samples.sh  pulls larger public teaching studies (BRAINIX) into sample-data/
scripts/load-sample-studies.sh   uploads sample-data/ (recursively) into Orthanc
scripts/create-session.py     mints a per-student Kasm session link (tested against a live instance)
```
