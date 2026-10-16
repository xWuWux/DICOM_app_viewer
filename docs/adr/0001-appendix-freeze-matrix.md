# 0001 appendix - freeze matrix run 2026-10-16 (redacted step log)

Provenance: produced by a rebuilt probe (`login -> request_kasm -> pause -> resume -> stop ->
destroy`). It reads the container id from the `kasms` table instead of from `get_kasm_status`,
because a paused session reports no `container_id` there - which is exactly why the 2026-10-15
run could not pair a running and a paused reading of the same container. The 2026-10-15 run left
NO log: its driver lived in `/tmp` and was never committed, so those figures survive only as prose
in ADR 0001 and in the #159 comment.

Redaction: sessions are aliased `S1..Sn` and container names with them; there is no token, session
URL, password, hash or raw Kasm/user/container UUID here. Responses are reduced to field NAMES and
fingerprints, because the log's purpose is the shape of each answer, not its secrets. Every memory
figure is `docker stats` cgroup usage including page cache, NOT strict RSS, from a session with an
EMPTY desktop and no study loaded.

## Environment

- kasm: 1.19.0 CE single-node (local WSL2, native dockerd)
- image: ipcmc/dicom-viewer:mvp
- run: 2026-10-16

Single node, native dockerd under systemd on WSL2 (not Docker Desktop), ~15 GiB RAM total and
~12 GiB available; this lab limits one session to 1 core / 2 GiB. State after every run: 0 `kasms`
rows and 0 session containers, 1 API key (the pre-existing `ipcmc_team`, never touched). What
`destroy_kasm` does NOT clean: 3 anonymous users and 41 `session_tokens` rows were left behind by
three public-path sessions, and nothing observed garbage-collects them.

## Step log (2026-10-16, one session, one container throughout)

| time | step | reading |
|---|---|---|
| 09:58:06 | `request_kasm` | status=200; keys=["kasm_id", "status"] |
| 09:58:06 | `request_kasm ids` | session=S1; url_sha=c627c09c14e5; has_url=False |
| 09:58:06 | `wait running` | db_status=running; attempts=0 |
| 09:58:06 | `container from DB` | container_id_present=True |
| 09:58:07 | `E1 running stats` | stats=session-container-S1	67MiB / 2GiB	82.44% |
| 09:58:07 | `status running` | status=200; keys=["current_time", "kasm"]; url_in_status=None |
| 09:58:08 | `pause_kasm` | status=200; resp={} |
| 09:58:08 | `paused confirmed` | db_status=paused; container_id_same=True |
| 09:58:09 | `E2 paused stats (paired, same container)` | stats=session-container-S1	92.81MiB / 2GiB	0.00% |
| 09:58:09 | `F: status while paused` | status=200; empty=True; keys=["operational_status"] |
| 09:58:09 | `D t0 dates` | note=step produced by a NULL-yielding concatenation, superseded by the dates table below |
| 09:59:29 | `D t80 dates` | note=step produced by a NULL-yielding concatenation, superseded by the dates table below |
| 09:59:30 | `E2b paused stats after 80 s` | stats=session-container-S1	92.19MiB / 2GiB	0.00% |
| 09:59:37 | `resume_kasm` | status=200; resp={} |
| 09:59:37 | `running after resume` | db_status=running |
| 09:59:38 | `E3 resumed stats (paired)` | stats=session-container-S1	158.8MiB / 2GiB	101.76% |
| 09:59:38 | `F: url stability` | url0_sha=c627c09c14e5; url_user_list_sha=c627c09c14e5; url_status_sha=c627c09c14e5; same_list=False; same_status=False; status_had_url=False |
| 09:59:41 | `stop_kasm` | status=200; resp={} |
| 09:59:47 | `stopped status` | db=stopped |
| 09:59:47 | `stopped writable-layer size` | size=91.8MB (virtual 3.44GB) |
| 09:59:48 | `destroy_kasm` | status=200; resp={} |
| 09:59:53 | `cleanup` | kasms_left=0; containers_left=0 |

## Idle-timer dates (same day, separate run, queried correctly)

| moment | expiration_date | keepalive_date |
|---|---|---|
| running | 09:02:26 | 08:02:26 |
| paused (t0) | 09:02:26 | 08:02:26 |
| paused (t+80 s) | 09:02:26 | 08:02:26 |
| after resume | 09:03:52 (re-armed) | 08:02:26 (300 s interval had not elapsed) |

Config from `group_settings`: `keepalive_expiration=3600`, `keepalive_interval=300`,
`keepalive_expiration_action=delete`.

## F, captured later the same day (production path, two runs at 10:34 and 10:49-10:57)

A key minted by `/api/admin/create_api_configs` and granted permission 100 (`User`) through
`add_permissions_group`. `POST /api/public/request_kasm` returned `kasm_id`, `user_id`, `username`,
`session_token` and `kasm_url`. Identities are aliased; `sha` values are SHA-256 prefixes.

```
creation URL sha=53b858527705; URL JWT == session_token: True
status call authenticated by that URL's JWT ALONE (no api_key, no admin token):
  [running] 200  top_keys=['current_time','kasm','kasm_url']        kasm_url sha=5974678b40fc
  [paused]  200  top_keys=['operational_status']  value='paused'    kasm_url absent
  [resumed] 200  top_keys=['current_time','kasm','kasm_url']        kasm_url sha=c5c674608c10
negative controls on the same endpoint: garbage JWT -> 401 Access Denied; no token -> 400 Invalid
Request; api_key instead of a JWT -> 403.
```

Three consequences recorded here because the raw readings say them and prose did not: the student's
link is a real credential (the negative controls are what make that statement worth anything); a
PAUSED session exposes no link at all, only `operational_status: "paused"` at the top level, so a
return has to resume before any link exists; and each status call mints a different link, so a
status-polling loop mints one token row per poll.

Also corrected during these runs: `delete_api_configs` and `update_api_configs` are NOT broken on CE.
They failed for me because the field is spelled `api_Id` (delete) and `target_api` (update); with
those names both returned 200. Earlier notes here and in ADR 0001 claiming "key lifecycle unusable"
are retracted in ADR 0001.

Cleanup final state: session destroyed, both probe keys deleted through the API, `ipcmc_team`
untouched, 0 `kasms` rows, 3 anonymous users and 41 `session_tokens` rows left behind.

## Destroy and status under a scoped key (attempted 10:36-10:41, negative result)

Attempted on the same node to answer two questions the freeze design still owed: what
`get_kasm_status` answers for a session that was really destroyed, and whether `destroy_kasm`
accepts a PAUSED session. Neither was answered, because neither call ever happened.

Key: created for this run by `/api/admin/create_api_configs`, granted permission 100 (`User`),
deleted again by `/api/admin/delete_api_configs` (200). Two sessions, both created through the
admin `request_kasm`; one paused first, one left running. Ground truth read from Kasm's own DB and
from `docker ps -a`, never from the API's own word.

| call | Kasm's answer | ground truth at the same moment |
|---|---|---|
| `POST /api/public/get_kasm_status` (api_key + user_id) | 403 `API Key (name) is not authorized to impersonate a user.` | container present, `kasms.operational_status` = running |
| `POST /api/public/destroy_kasm` (same key, same session) | 403, the same message | container present |
| both again with permission 50 granted on top of 100 | 403, the same message | container present (one paused, one running) |

Four consequences:

- Permission 100 is not enough for the destroy/verify path and 50 + 100 is not either; the id of
  the "impersonate a user" permission stays unidentified in this install.
- A 403 is an authorization answer, not an absence answer. `end-kasm-session.py` counted it as
  "gone or invisible to this key" and reported a verified destroy that never happened; fixed on
  PR #251, where the measured text is quoted.
- The two open questions stay open, and anything built on them ("destroying a frozen session
  frees its disk") is unverified until a key that may impersonate a user exists.
- Anonymous-user and `session_tokens` growth is unchanged by this run: sessions created here were
  destroyed, `0` `kasms` rows left, and the measurement key is gone.

Method note, because it is a repeatable mistake and worth writing down: the first version of this
probe selected `*` from `kasms` and logged the row, which carried `docker_environment` -- VNC
passwords and a live session JWT -- into a local log. Naming the columns IS part of redaction; a
redacted log built on `select *` is not redaction. The committed probe names every column it reads
and never selects `*`, and a future extension should keep it that way.

## What this step log did not capture

- A loaded Weasis study (fidelity test B, and the memory of a real exam session): not run.
- Pause survival across a Docker daemon restart or a host reboot: not run (`sudo -n` refused, needs
  the owner's go-ahead).
- What the connect PAGE does with a paused session (browser-level behaviour, API-only here).
- What `destroy_kasm`/`get_kasm_status` say about a destroyed or paused session to a key that may
  impersonate its user: blocked in this install, the scoped key is refused (section above).
- Multi-day soak (3/7 days) behind a policy TTL: not run.
- Test A (cap counting) was not repeated; its evidence stays the 2026-10-15 run.

