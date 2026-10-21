# Data inventory (facts for the compliance outline)

What personal or study data the system holds, where, and for how long, as the code on `master`
(540354a) and the lab configuration show it on 2026-10-21. Input for a record of processing
(GDPR art. 30), not the record itself. UNKNOWN marks what could not be established from the
repository or the lab.

## People involved
- **Doctors (users, called "students" in the code).** Identified in the system only by a
  `student_id` assigned by the coordinator (e.g. `DEMO_001`, `STU_12345`). The mapping from
  `student_id` to a real person is kept **outside** this system (UNKNOWN where and by whom).
  Note: the allowed `student_id` pattern (`^[A-Za-z0-9][A-Za-z0-9._@-]*$`, max 128) also accepts an
  e-mail address; using real names or e-mail addresses as ids would put direct identifiers in every
  store below. **TO DECIDE:** require pseudonymous ids.
- **Patients.** Appear only in the DICOM studies. Rule (CLAUDE.md): studies are anonymised before
  ingestion; the load-time gate `scripts/phi-gate.py` refuses files that still carry patient
  identity tags (#253). Anonymisation itself is upstream (owner decision 2026-10-15); the
  radiologist's written anonymisation statement is tracked in #162.
- **Coordinator / operators.** Hold the keys that mint sessions; no account data stored by us.

## Stores
| Store | Contents (personal or study data) | Lifetime today |
|---|---|---|
| grading-api SQLite (`grading-db` volume): `sessions` | session token, `student_id`, `session_id`, created/expiry time | token TTL 8 h default, 7 days max; expired rows deleted when the next token is minted |
| grading-api: `progress` | `student_id`, stage, position, timestamps, approval time | no expiry |
| grading-api: `submissions` | `student_id`, case, chosen category, free text (learning stage), correctness, time per case, timestamp, frozen ground truth | no expiry |
| grading-api: `cases` | case titles, study UIDs, ground truth, reference reports (the answer key) | no expiry; content comes from `local/cases.json` (git-ignored) |
| Orthanc (`orthanc-storage` volume) | anonymised DICOM studies | no expiry |
| Pre-migration database backups (`grading.db.pre-migration-*.sqlite.bak`, same volume) | full copy of the grading database | no expiry |
| Manual backups and result exports (`local/backups/`, `local/exports/`, operator folders) | full database copies; CSV of all answers incl. the answer key | no expiry; files 0600, folders 0700 |
| Container logs (Docker json-file) | nginx access log: client IP, time, method, path, status; grading-api JSON logs: request ids, paths, status (tokens, `student_id` and answers are kept out, #93, #94, #113) | rotated at 10 MB x 3 files per service |
| Session containers (Kasm / Guacamole) | the doctor's desktop; watermark text `student_id | session_id | time` drawn on screen | destroyed at the end of the session (Kasm: after 3600 s without a connection; Guacamole: teardown or reaper, idle 180 min / max 48 h) |
| Kasm database (vendor) | anonymous Kasm users, session records, session tokens without an expiry date, Kasm logs | Kasm-managed; anonymous users and tokens currently accumulate (#251 review); Kasm's own log retention UNKNOWN |
| Guacamole database | per-session user `stu_<student_id>`, connection, `guacamole_connection_history` (username, **client IP**, start/end) | accounts expire by `valid-until` (48 h default, day-grained); history kept indefinitely |
| grading-api telemetry snapshot (`/internal/signals`, in memory, #113) | aggregate counters, sizes and timestamps only; no participant ids, tokens or IP addresses (`docs/observability.md`, forbidden fields) | process lifetime |
| Session link files (`local/session-links/`) | bearer link with a Guacamole password | until the operator deletes them; mode 0600 |

Sources for the Kasm rows: the lab Kasm database, read 2026-10-21 (Group setting `keepalive_expiration` = 3600 s, action `delete`) and the live test of 2026-10-15 (`session_tokens.session_expiration_date` empty).

**Future store:** if session freeze/resume is built (#258, ADR 0001 / #252), a paused or stopped session keeps the desktop's memory and disk state, including study pixels, on the host for days. It must be added here with its lifetime before that feature ships.

## Data that leaves the host
None by design: medical data stays on-premise (CLAUDE.md); the client receives only stream pixels.
Not yet verified: whether Kasm Community Edition contacts the vendor (licence check, telemetry):
UNKNOWN, to be checked before go-live. See [processors-and-disclosures.md](processors-and-disclosures.md).
