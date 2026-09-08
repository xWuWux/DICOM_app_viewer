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

## What's actually running today (this machine, via Docker)

```
┌──────────────┐      ┌───────────────────────┐      ┌─────────────┐
│  Your browser│ ───► │ viewer (nginx:8080)   │ ───► │  orthanc    │
│  (stand-in   │      │ watermark.html +      │      │  (DICOM     │
│  for Kasm's   │      │ reverse proxy         │      │  store,     │
│  streamed     │      └───────────────────────┘      │  internal   │
│  session)     │                                      │  only)      │
└──────────────┘                                      └─────────────┘
```

Orthanc is **not** published on any host port — only the `viewer` container
can reach it, over the internal `ipcmc-internal` Docker network. That's the
same "nothing but pixels leaves the isolated tier" model the docs converge on
for the real Kasm deployment.

### Run it

```bash
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
"one individual link per person." The same query string also carries
`orthanc_url` (default `http://localhost:8042/` for this local setup) so the
wrapper knows which origin to iframe — Orthanc's plugins assume they're
served from their own origin's root, so it's iframed directly rather than
reverse-proxied under a subpath (see `docker/viewer/nginx.conf`).

Default Orthanc credentials are in `docker/orthanc/orthanc.json`
(`orthanc` / `CHANGE_ME_ORTHANC_PASSWORD`) — **change that password** before
this ever touches real data or a shared network.

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

## What's NOT in this MVP (on purpose)

Per the docs' own recommendation (`Dokumentacja/AI_context_Documentation_DICOM.txt`,
"About the two-week MVP" section), all of this is deferred:

- Kasm Workspaces itself is not installed yet — this repo only prepares what
  it needs (the workspace image + session-minting script below).
- Moodle / LTI 1.3 launch and grade passback.
- Payments (300 PLN), certificates, access expiry.
- Lung-RADS category selection UI, grading, ground-truth comparison.
- Stratified case sampling (50/50/30 across Lung-RADS classes).
- Admin dashboard, leaderboard, expert-review queue.

## From MVP to the real thing: adding Kasm Workspaces

This is a bigger, host-level change (downloads several GB, installs a
systemd service, wants port 443, modifies the machine it runs on) — deliberately
**not** automated here. Do this step yourself, on whichever machine/VM will
actually host it (this machine for another local test, or the real Proxmox VM):

1. Install Kasm Workspaces Community Edition following
   <https://kasmweb.com/docs/latest/install/installation.html>.
2. Build the custom workspace image and register it in Kasm:
   ```bash
   docker build -t ipcmc/dicom-viewer:mvp docker/kasm-workspace
   ```
   Point `VIEWER_URL` in `docker/kasm-workspace/Dockerfile` at wherever the
   `viewer` container from this repo is actually reachable from the Kasm host.
3. In the Kasm admin UI, register the image as a Workspace, and set its
   **Permissions** to disable clipboard, file upload/download, and printing
   for this workspace (Kasm's own DLP controls — the equivalent of the
   AppStream "Stack Policy" the docs discuss, done the on-prem way).
4. Generate an API key/secret (Kasm admin UI → Access → API Keys), then mint
   an individual session link per user:
   ```bash
   KASM_SERVER=https://your-kasm-host \
   KASM_API_KEY=... KASM_API_KEY_SECRET=... KASM_IMAGE_ID=... \
   python3 scripts/create-session.py --student-id STU_12345
   ```
   **`scripts/create-session.py` is an unverified template** — Kasm wasn't
   installed as part of this session, so its API response shape hasn't been
   checked against a live instance. Confirm field names against
   <https://kasmweb.com/docs/api.html> (linked in `CLAUDE.md`) before relying
   on it, and expect to adjust.

## Security note (read this before assuming more than it does)

Per the design discussion in `Dokumentacja/`: **nothing here, or in the real
Kasm deployment, actually prevents someone from photographing their screen.**
The watermark is deliberately an attribution/deterrence control, not a
prevention control — that distinction needs to be in whatever acceptable-use
agreement participants sign, not just in this code. What this MVP *does*
enforce:

- Raw DICOM pixels never reach a browser outside the internal Docker network
  (Orthanc has no published port).
- Every session's viewer page carries a rotating, timestamped identity
  watermark, reconstructed every second server-side-adjacent (in the wrapper
  page, not user-controllable JS state).
- A DOM-tamper check reloads the page if the watermark canvas is hidden or
  removed — a deterrent, not a guarantee (disabling JS entirely defeats it,
  same as noted in the docs).

## Repo layout

```
CLAUDE.md                     hard rules for this project (do not violate)
Dokumentacja/                 source design discussion + radiology requirements
docker-compose.yml            Orthanc + watermarked viewer, for local testing
docker/orthanc/               Orthanc config
docker/viewer/                nginx + the watermark wrapper page
docker/kasm-workspace/        custom Kasm workspace image (build after Kasm is installed)
sample-data/                  public-domain sample DICOM files
scripts/fetch-public-samples.sh  pulls larger public teaching studies (BRAINIX) into sample-data/
scripts/load-sample-studies.sh   uploads sample-data/ (recursively) into Orthanc
scripts/create-session.py     mints a per-student Kasm session link (template, unverified)
```
