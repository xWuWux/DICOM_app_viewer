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
~12 GiB available; session limits 1 core / 2 GiB. Lab left clean: 0 `kasms` rows, 0 session
containers.

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

## What this run did NOT capture

- A loaded Weasis study (fidelity test B, and the memory of a real exam session): not run.
- Pause survival across a Docker daemon restart or a host reboot: not run (`sudo -n` refused, needs
  the owner's go-ahead).
- The student link (F): admin `request_kasm` returns only `kasm_id` and `status`; the link comes from
  the PUBLIC `request_kasm`, which needs an `api_key`/`api_key_secret` pair - link stability across
  pause/resume stays unmeasured.
- Multi-day soak (3/7 days) behind a policy TTL: not run.
- Test A (cap counting) was not repeated; its evidence stays the 2026-10-15 run.

