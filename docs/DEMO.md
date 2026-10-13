# Live test and demo guide (Kasm and Guacamole)

Status: 2026-10-13. For the person who runs the live tests and the demo on their own machine, without asking anyone for
a session link. Every command below was run on the real stack on this date.

## 0. How it works, in one minute

- A **doctor** is a student id (`DEMO_001`, `DEMO_002`, ...). Each id has its own progress; a new doctor always starts at the
  learning stage. Starting over inside the app (`POST /reset`, the "Rozpocznij od początku" button) deletes answers and is **off by
  default** (issue #98), so every new run is a **new doctor**. Earlier doctors' answers stay in the database.
- `scripts/demo-next-doctor.py` creates a doctor and a session in one of the two flows and puts the **session link on the clipboard**.
  The link is never printed. For Guacamole it is also in a file with mode 0600.
- Two flows, same panel and same Weasis: **Kasm** (the main flow) and **Guacamole** (the alternative). Guacamole has no copy or export
  lockdown by design and is **not** for real patient data in production; for your own test on your own machine it is fine.

## 1. Where things live on this machine

| What | Where |
|---|---|
| The running stack (a Git worktree of this repository) | `~/ipcmc-live` (on disk, survives a restart; see section 9) |
| Settings and secrets for the stack | `~/ipcmc-live/.env` (never commit it) |
| Real case list (answer key, reports, study UIDs) | `~/ipcmc-live/local/cases.json` (git-ignored, mode 0600) |
| Real studies and the grading database | Docker volumes `wt-data_orthanc-storage` and `wt-data_grading-db` (they survive `docker compose down`) |
| Kasm API key file | `~/.config/ipcmc/kasm.env` (mode 0600; lines `KASM_API_KEY`, `KASM_API_KEY_SECRET`, `KASM_SERVER`, `KASM_IMAGE_ID`) |
| Your database backups | `~/ipcmc-live/local/backups/` |
| Guacamole session links | `~/ipcmc-live/local/session-links/` |
| Exports of results | `~/ipcmc-live/local/exports/<time>/` |

Addresses: the Kasm console and sessions at `https://localhost` (self-signed certificate: accept the browser warning), Guacamole at
`http://localhost:8090/guacamole/`, the grading API through the viewer at `http://localhost:8080/api/`.

## 2. Start a doctor (the normal case, stack already running)

Shortcut (from any directory): `ipcmc-up` starts the stack if needed, waits until it is healthy and prints its status;
`ipcmc-guac` and `ipcmc-kasm` run the `demo-next-doctor.py` commands below. All three live in `~/.local/bin`.

Or open a terminal in the stack directory and check the stack first:

```
cd ~/ipcmc-live
curl -s localhost:8080/api/healthz          # must print {"status":"ok"}
docker ps --format '{{.Names}} {{.Status}}' | grep ipcmc     # six containers, "(healthy)" where there is a health check
```

**Guacamole:**
```
python3 scripts/demo-next-doctor.py --flow guac --end-previous
```
Then paste the link (it is on the clipboard) into a browser. If you copied something else in the meantime, copy it again from the file the
command printed: `clip.exe < local/session-links/guac-DEMO_nnn-<time>.link`.

**Kasm:**
```
python3 scripts/demo-next-doctor.py --flow kasm --kasm-env ~/.config/ipcmc/kasm.env --insecure --end-previous
```
Then paste the link into a browser. The Kasm link exists **only on the clipboard**: if you lose it, run the command again (a new doctor).

What the options mean: `--end-previous` first ends the previous doctor of the same flow (container, Guacamole user, grading token, link file;
for Kasm the session through its API); leave it out to keep several doctors open at once. `--student-id NAME` picks the id yourself.
`--insecure` is for Kasm's self-signed certificate. The next free `DEMO_nnn` is chosen automatically.

Limits: Kasm Community Edition allows **5 concurrent sessions** (do not plan around exceeding it); a Kasm session ends by itself after about
an hour of inactivity; the session link of a doctor is single-use in the sense that it belongs to that doctor only.

## 3. After new changes were merged: update and rebuild (about 5 minutes)

Do this whenever a PR that changes the app was merged, or before an important demo.

```
cd ~/ipcmc-live
git fetch origin && git merge origin/master            # bring the stack's checkout to the latest master

# 1. Back up the grading database (the new version may migrate it; it also writes its own backup next to it)
mkdir -p local/backups && chmod 700 local/backups
# The database runs in WAL mode: the freshest commits can still sit in grading.db-wal, so grading-api is
# stopped first and ALL grading.db* files are copied into one timestamped directory -- copying only
# grading.db can produce a backup that misses recent answers. Step 2 starts the service again.
docker compose -f docker-compose.yml -f docker-compose.guacamole.yml stop grading-api
TS=$(date +%Y%m%dT%H%M%S)
docker run --rm -v wt-data_grading-db:/data:ro -v "$PWD/local/backups":/out alpine \
  sh -c "mkdir -p /out/grading-$TS && cp -a /data/grading.db* /out/grading-$TS/"
docker run --rm -v "$PWD/local/backups":/out alpine chown -R $(id -u):$(id -g) /out

# 2. Rebuild and restart the stack (never add -v: that would delete the volumes).
# No -p is needed here: COMPOSE_PROJECT_NAME=wt-data in .env keeps the existing wt-data_* volumes -- see section 9.
docker compose -f docker-compose.yml -f docker-compose.guacamole.yml up -d --build

# 3. Rebuild the two workspace images (Kasm session image and Guacamole session image)
docker build -t ipcmc/dicom-viewer-weasis:mvp docker/kasm-workspace-weasis
docker build --build-context kasm=docker/kasm-workspace-weasis -t ipcmc/guacamole-weasis:poc docker/guacamole-weasis

# 4. Check
curl -s localhost:8080/api/healthz
```
Sessions that are already open keep running the OLD images; start new doctors after a rebuild. If `docker compose up` stops with
"container name already in use", another stack with the same container names is running (see section 8).

## 4. What to test (a checklist for each flow)

1. **First load:** the study opens by itself in Weasis, the grading panel shows the learning form, the watermark (doctor id, session id, time) is
   visible over both windows and does not flicker.
2. **Layout:** Weasis on the left, the panel on the right, no overlap. Minimise a window: it comes back by itself. Close the panel: it comes back.
3. **Learning stage:** type an assessment, submit, the reference report appears with the Lung-RADS names in **bold**. "Next case" opens the NEXT study in
   Weasis (it can take up to about 6 seconds).
4. **Assessment stage:** the category list is bold, a verdict appears immediately, the stage chips "Nauka, Ocena, Test" show the current stage.
5. **Test stage:** no feedback after each case.
6. **End:** "Podsumowanie odpowiedzi" lists your answers read-only, with **no** correct answers yet. Press "Zatwierdź i zakończ": the final screen shows the
   score bar, accuracy per category, and every case with the reference. There is **no restart button**.
7. **Reload** (F5 or Ctrl+R in the panel) at the review and at the final screen: you stay where you were.
8. **Reconnect:** close the browser tab and open the same link again: the session is still there.
9. **Copy and export** (Kasm only; Guacamole has none by design): try copying the image out, Weasis export, print. These should be blocked.

## 5. Show the results (for the radiologist and the project manager)

```
cd ~/ipcmc-live
python3 scripts/export-results.py            # demo doctors (DEMO_*) from the running stack
```
It prints a summary (completion, accuracy and time per doctor, a confusion matrix of true category against the doctor's answer, per case) and writes
`attempts.csv` and `summary.txt` to `local/exports/<time>/` (directory 0700, files 0600). The CSV contains the answer key: do not email it around.
`--all` includes every student; `--include-text` adds the learning-stage free text.

## 6. End the test

- End the last sessions: run the same start commands once more with `--end-previous` and close the browser tabs, or end the Kasm session in the Kasm console
  (Guacamole only: `python3 scripts/teardown-guacamole-session.py --student-id DEMO_nnn --session-id <id>`, the id is in `local/demo-state.json`).
- Stop the stack but keep all data: `docker compose -f docker-compose.yml -f docker-compose.guacamole.yml down` (**never** `-v`).
- The doctors you created stay in the database. That is intended; they are test data (their ids start with `DEMO_`).

## 7. If something is wrong

| What you see | What to do |
|---|---|
| `cannot find the stack: is the container ipcmc-grading-api running?` | Start it: `docker compose -f docker-compose.yml -f docker-compose.guacamole.yml up -d` |
| `missing configuration for Kasm: ...` | Pass `--kasm-env ~/.config/ipcmc/kasm.env`; check that the file has all four variables |
| `no clipboard tool found` | Guacamole: open the printed link file. Kasm: add `--write-link FILE` to the command |
| Weasis opens with no study and almost no toolbar | The session image is old. Do section 3 (rebuild the images), then start a NEW doctor |
| Nothing in the panel but "Błąd ładowania" | Check `curl -s localhost:8080/api/healthz`; if it answers, wait 10 seconds and press "Spróbuj ponownie" |
| The panel says the session expired | The doctor's token is gone (revoked by `--end-previous`, or older than 8 hours by default --
 unless GRADING_TOKEN_TTL_SECONDS in the stack .env says otherwise). Start a new doctor |
| Kasm: "no free resources" or the 6th session is refused | Community Edition cap of 5 sessions: end old sessions first |
| Guacamole page asks for a login | You opened `http://localhost:8090/guacamole/` directly. Use the session link from the clipboard or the link file instead |
| The stack does not start after `git merge` | Run `docker compose ... up -d --build` again and read `docker logs ipcmc-grading-api`. A message that starts with `CONFIGURATION ERROR` says what to fix |
| You are unsure whether data is safe | The newest backup is in `local/backups/`; the application also writes `grading.db.pre-migration-*.sqlite.bak` into the volume before a migration |

## 8. Rules that protect the real data

- The stack uses fixed container names and ports (8080, 8043, 8042). Two stacks cannot run side by side, and **test scripts that start their own stack
  refuse to run while this one is up** (`scripts/test-orthanc-proxy-scope.sh`); some others (`smoke-test.sh`, `test-visual-regression.sh`,
  `test-degraded-modes.sh`) do not have that guard: stop this stack first (`down`, no `-v`) and start it again afterwards. They must never be run against data
  you care about, and they use the default Compose project name, so run them from a different checkout than this stack.
- Never run `docker compose down -v` or `docker volume rm` on `wt-data_*` volumes.
- Real patient data stays on this machine. Do not paste links or screenshots of real studies into tickets.

## 9. Before you restart the PC

The stack lives in `~/ipcmc-live` on disk, so a restart keeps it. After a restart run `ipcmc-up` (Docker starts first; WSL may need a moment).
Its `.env` sets `COMPOSE_PROJECT_NAME=wt-data`: the volumes were created under that name when the stack lived in `/tmp/wt-data`, and without
the setting Compose would derive the project from the directory name and start with new, empty volumes. Never remove that line.
Keep copies of `.env`, `local/cases.json` and `local/backups/` in `~/ipcmc-private/` (mode 0700): that is what rebuilt the stack the last time.
`local/demo-counter` holds the last `DEMO_nnn` number; if it is lost, set it to the highest `DEMO_` id in the database so no doctor is reused.
