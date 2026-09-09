# Complete setup guide: running your own copy of this MVP

For programmers / IP_CMC team members setting up their own instance —
either to develop against, or to host a copy other people can be given
individual links to. **This is not for the radiologists testing the
platform** — they only ever need a link, never this repo. If you're
looking for that, it doesn't belong on GitHub (it names your actual
server address) — ask whoever's coordinating testing for it.

Two parts: **Part 1** gets the core viewer + grading mechanics running on
your own machine (~15 minutes, no `sudo` needed). **Part 2** adds real
per-person individual links via Kasm Workspaces (~30-45 minutes, needs
`sudo` and a lot more disk/network — skip it if you only need to develop
against the viewer/grading pieces).

## Prerequisites

- **OS**: Linux, macOS, or Windows with WSL2. This whole project was built
  and tested on WSL2 (Ubuntu) — if you're on native Windows, install WSL2
  first (`wsl --install` in an admin PowerShell, then work inside the WSL2
  Ubuntu shell for everything below, not PowerShell/cmd directly).
- **Docker** with the `docker compose` plugin (not the older standalone
  `docker-compose` with a hyphen). Check with:
  ```bash
  docker --version && docker compose version
  ```
  If missing: [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  (Windows/Mac, includes WSL2 integration — enable it in Docker Desktop's
  Settings → Resources → WSL Integration) or, on native Linux, your
  distro's `docker.io`/`docker-ce` package plus the
  [compose plugin](https://docs.docker.com/compose/install/linux/).
- **git**, **curl**, **python3**, **openssl** — all normally preinstalled
  on Linux/macOS/WSL2; check with `git --version` etc.
- **Hardware**: 8GB+ RAM, ~10GB free disk for Part 1 (images + sample
  data); Part 2 (Kasm) wants more — see its own section.

## Part 1: Core viewer + grading mechanics (no sudo needed)

### 1. Get the code
```bash
git clone https://github.com/xWuWux/DICOM_app_viewer.git
cd DICOM_app_viewer
```

### 2. Set a real Orthanc password
```bash
cp .env.example .env
sed -i "s/^ORTHANC_PASSWORD=.*/ORTHANC_PASSWORD=$(openssl rand -hex 16)/" .env
```
(On macOS, `sed -i` needs a backup-suffix argument: `sed -i '' "s/.../.../"` —
or just open `.env` in an editor and paste a random string after `=`.)
**Never commit `.env`** — it's already gitignored, and `docker-compose.yml`
refuses to start without a real value here rather than falling back to a
known default.

### 3. Build and start
```bash
docker compose up -d --build
```
First run pulls a fairly large Orthanc image — a few minutes depending on
your connection. Check everything came up:
```bash
docker compose ps
```
You should see `ipcmc-orthanc`, `ipcmc-viewer`, and `ipcmc-grading-api`
all `Up`.

### 4. Load sample data
```bash
./scripts/fetch-public-samples.sh   # pulls BRAINIX (~64MB) from Orthanc's own public demo server
./scripts/load-sample-studies.sh
```
Expect to see `Success` printed for each uploaded file — 234 total across
three studies (two tiny pydicom test fixtures + BRAINIX).

### 5. Verify it worked
Open **http://localhost:8080/?student_id=YOUR_NAME&session_id=test1** in a
browser. You should see:
- A dark sidebar on the right with **"Nauka"** (learning stage), a text
  box, and a "Wyślij ocenę" button.
- A DICOM study browser on the left (Orthanc Explorer 2).
- A faint, rotating watermark tiled across the whole page, containing
  `YOUR_NAME | test1 | <timestamp>`.

If you see all three, Part 1 is working. Stop here unless you specifically
need real per-person Kasm links (Part 2).

### Checking your own changes
Two scripts, also run in CI on every push:
```bash
./scripts/lint.sh          # bash syntax + compose config validation, instant, safe anytime
./scripts/smoke-test.sh    # brings the stack up for real and checks it -- WARNING: ends with
                           # `docker compose down -v`, wiping any data you'd loaded. Don't run
                           # this against a copy you're using for anything real.
```

## Part 2: Real per-person links via Kasm Workspaces

This is the part that turns "one shared URL" into "a unique, individually
watermarked link per tester." It's a genuinely bigger step: Kasm installs
its own systemd service, downloads a couple more GB, and wants port 443
free. Needs `sudo` with an interactive password, so no copy-paste block
does this end to end — follow along a step at a time.

### 1. Install Kasm Workspaces Community Edition
```bash
cd /tmp
curl --fail-early -fO https://kasm-static-content.s3.amazonaws.com/kasm_release_1.19.0.tar.gz \
     -fO https://kasm-static-content.s3.amazonaws.com/kasm_release_1.19.0.tar.gz.sha256sum
sha256sum --check kasm_release_1.19.0.tar.gz.sha256sum   # confirms the download wasn't corrupted/tampered
tar -xf kasm_release_1.19.0.tar.gz
sudo bash kasm_release/install.sh
```
Check the current version number at
<https://docs.kasm.com/docs/tutorials/install/single-server-install/index.html>
in case 1.19.0 is no longer current. Takes 10-15 minutes. **At the end it
prints an admin login (`admin@kasm.local`) and a randomly generated
password** — save that somewhere safe, you'll need it in the next steps and
it's the only time it's shown. Verify it worked:
```bash
systemctl status kasm --no-pager
```
Should show `active (exited)`. Then open **https://localhost/** in a
browser — expect a certificate warning (Kasm generates its own self-signed
cert on install; click through it) and a login page. Log in with the admin
credentials from the install output.

### 2. Wire Kasm to reach this repo's containers
Kasm's installer creates a Docker network called `kasm_default_network`.
`docker-compose.yml` already attaches `orthanc`/`viewer` to it — recreate
them now that the network actually exists:
```bash
docker compose up -d
```

### 3. Build and register the workspace image
```bash
docker build -t ipcmc/dicom-viewer:mvp docker/kasm-workspace
```
In the Kasm admin UI: **Workspaces → Add Workspace**.
- **Workspace Type**: Container
- **Friendly Name**: anything, e.g. `IP_CMC DICOM Viewer`
- Once you select Container, more fields appear:
  - **Docker Image**: `ipcmc/dicom-viewer:mvp`
  - **Docker Registry**: leave blank (it's built locally, Kasm's agent
    already has it on the same Docker host)
  - **Uncompressed Image Size**: `1216` (MB) — a reasonable estimate; Kasm
    uses this to avoid over-filling disk on the agent, not a hard limit
  - **Cores**: `1`, **Memory**: `2048`
- Save it. Open its edit page and copy the UUID out of the URL — that's
  the `image_id` you'll need in step 5.

### 4. Generate an API key
**Settings → Developers → Add API Key.** Give it a name, save — this is
the only time the secret is shown, copy both the key and secret. Then open
it again and go to its **Permissions** tab: search and add **"User"**
(confirmed directly against a live instance this session: a key with no
permissions at all 403s on `request_kasm`; adding just `User` makes it
work — checked via `select * from group_permissions` in Kasm's own
Postgres DB, not just assumed).

### 5. Configure DLP settings (clipboard/download/upload/print)
**Access → Groups → All Users → Settings.** Search `allow_kasm` and set
every one of these to **off**: `allow_kasm_clipboard_down`,
`allow_kasm_clipboard_up`, `allow_kasm_clipboard_seamless`,
`allow_kasm_downloads`, `allow_kasm_uploads`, `allow_kasm_printing`. Also
turn off (not required, but there's nothing in this app that needs them):
`allow_kasm_sharing`, `allow_kasm_webcam`, `allow_kasm_microphone`,
`allow_kasm_gamepad`, `allow_kasm_audio`.

### 6. Mint your first real link
```bash
KASM_SERVER=https://localhost \
KASM_API_KEY=<from step 4> \
KASM_API_KEY_SECRET=<from step 4> \
KASM_IMAGE_ID=<from step 3> \
python3 scripts/create-session.py --student-id TEST_001 --insecure
# drop --insecure once this has a real (non-self-signed) certificate
```
Prints a `link` field — open it in a browser. You should see the same
split-panel viewer+grading UI as Part 1's verification step, but now
streamed through an actual Kasm session (watch for the Kasm loading
splash screen first).

## Troubleshooting (developer setup)

**`docker compose up` fails with "port is already allocated".**
Something else on your machine is using 8080, 8042, 8043, or 443. Find and
stop it, or (for the app's own ports, not Kasm's 443) edit the `ports:`
section of `docker-compose.yml` to use different host ports.

**`./scripts/lint.sh` fails on "docker compose config".**
Almost always a missing `.env` — re-check step 2 of Part 1. The error
message names exactly which required variable is missing.

**Sample data upload fails / `scripts/load-sample-studies.sh` errors.**
Confirm `docker compose ps` shows `ipcmc-orthanc` as `Up` first — the
script assumes it's already running and reachable at `localhost:8042`.

**Kasm install script exits partway through.**
Re-run `sudo bash kasm_release/install.sh` — it's designed to be
re-runnable. Check `kasm_install_<timestamp>.log` in the directory you ran
it from if it fails again (only kept on failure).

**`request_kasm` returns 403.**
Almost always the API key's `User` permission (step 4 above) — the error
message names which permission is missing.

**`get_kasm_status` (inside `create-session.py`'s readiness check) returns
403 "not authorized to impersonate a user".**
Expected and non-fatal — `create-session.py` already treats this as
best-effort and prints the link anyway. A scoped API key commonly can't
query another (anonymous) user's session status; this doesn't affect the
link itself working.

**Chrome never appears in the Kasm session (blank/stuck loading screen).**
Check the session container's logs: find it with
`docker ps --format "{{.Names}}"` (look for `anon_...` or your
`kasm_id`), then `docker logs <name>`. A quoting or syntax error in
`docker/kasm-workspace/custom_startup.sh` will show up here as the script
silently failing to launch Chrome at all — this exact class of bug has
bitten this project before (see git log for
`docker/kasm-workspace/custom_startup.sh`).
