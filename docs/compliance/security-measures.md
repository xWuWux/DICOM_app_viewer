# Data security measures: outline (GDPR art. 32)

Summary of the technical measures in the repository on `master` (540354a), with the issue or PR that
holds the details, and the known gaps. Organisational measures (who has access, training, access
reviews) are **TO DECIDE.**

## In place
| Area | Measure | Reference |
|---|---|---|
| Data location | all medical data on-premise; the browser receives stream pixels only | CLAUDE.md |
| Anonymisation boundary | load-time gate refuses DICOM files with patient identity tags | PR #253 (#238 point B1) |
| Exfiltration | Kasm: clipboard, downloads, uploads, printing off; KasmVNC binary clipboard blocked; Weasis export/import/send/Q-R disabled; Guacamole: clipboard off both ways, same Weasis lockdown | README "DLP settings", #263, #264, #265, `scripts/test-copy-lockdown.sh` |
| Attribution | forensic watermark (participant id + time) on every session | CLAUDE.md, #6 (origin), #150, #179 |
| Access to grading | per-session token, minted only with the coordinator key; 8 h default lifetime; revocable | #104 |
| Access to studies | Orthanc behind a read-only auth proxy; per-session scoping in progress | #64, #195 |
| Edge | request rate limit on `/api/`; validated request ids; internal telemetry blocked at the edge | #226, #119, #266 |
| Containers | non-root; narrow seccomp profile for Guacamole sessions; ephemeral session containers | #51, #52 |
| Logging | tokens, participant ids and answers kept out of logs; log rotation | #93, #94, #113 |
| Supply chain | digest-pinned images, hashed Python lockfile, image CVE scans, secret scanning | #105, #245 |
| Backups | automatic copy before database migrations; manual backups | #85 |

## Known gaps (tracked)
- Encryption at rest and TLS for the deployment (Kasm uses a self-signed certificate; Guacamole is
  plain HTTP on localhost): #107.
- Default `guacadmin` password and port exposure in the remote-host compose file: #103.
- Kasm session links do not expire by date; ended sessions leave anonymous users and tokens: #251, #159.
- No backup policy, encryption or restore test: #85.
- Secrets passed as environment variables: #65, #216.
- Guacamole hardening gaps (one viewer per session, kiosk menu, resource limits): #262.
- Availability / DDoS hardening in front of the streamed sessions before going live: #267.
