# Kasm session lifecycle: end a session, re-issue a lost link

Status: added with issue #159 (logout does not destroy the container). Owner decision on the
enforcement option (a)-(d) of #159 is still open; this runbook is the manual coordinator path that
works today.

## Purpose

Two operations that Kasm's own panel does not offer reliably:

1. `scripts/end-kasm-session.py` destroys a session's container AND its Kasm record, then revokes
   the student's grading token (issue #159's "end for good").
2. `scripts/reissue-session-link.py` mints a fresh link for a session that is still running after
   its old link died (Kasm account logout deletes the old link's `session_tokens` row; the stuck
   browser then loops on `login_saml`).

## Scope

Kasm path only. The Guacamole alternative already has its own paths:
`scripts/teardown-guacamole-session.py` (end) and the link file written by
`scripts/provision-guacamole-session.py`. The revocation step is the same
(`grading-api POST /session/revoke`, shared code in `scripts/guacamole_session.py`).

## Prerequisites

| Variable | Used by | Notes |
|---|---|---|
| `KASM_SERVER`, `KASM_API_KEY`, `KASM_API_KEY_SECRET` | both scripts | same variables as `scripts/create-session.py` |
| `TLS_CA_BUNDLE` | both scripts | CA that signed the Kasm certificate; `--insecure` only on a lab box |
| `GRADING_API_URL`, `GRADING_COORDINATOR_KEY` | end script | optional; without them the token is not revoked and expires on its TTL |

The `--kasm-id` and `--user-id` values are printed by `create-session.py` in its JSON summary
(also kept by `scripts/demo-next-doctor.py` in `local/demo-state.json`). The full session link
embeds a JWT: never paste it into a shell (history), never commit it, treat a file holding it as
a credential (mode 0600).

## Steps

End a session for good (revokes the token when the grading variables are set):

```
python3 scripts/end-kasm-session.py --kasm-id 42 --user-id 7 --student-id STU_12345
```

Re-issue a link for a session that is still running:

```
python3 scripts/reissue-session-link.py --kasm-id 42 --user-id 7 > /tmp/link.txt
```

Hand `/tmp/link.txt` to the student and have them delete it.

If the student's page loops on the login screen (the old link is dead, see #159): close ALL tabs
of the Kasm origin, clear the site's data, then open the freshly re-issued link. A loop will keep
emitting roughly 17 failed requests per second from every stuck tab while it stays open.

## If the session is paused

Freezing (issue #159, ADR 0001) leaves a session that exists but answers `get_kasm_status` in a
different shape from a running one. Both shapes below were measured on Kasm 1.19.0 CE, one node,
in the #159 freeze matrix:

| state | response body | link |
|---|---|---|
| running / starting | `kasm_url` at the top level, plus a nested `kasm` object | yes, and each call mints a new one |
| paused | exactly one key, a top-level `operational_status` | none at all |

Consequences for the two scripts, both covered by their tests:

- `reissue-session-link.py` refuses on a paused session and names it as paused. Resume it first
  (the Kasm admin UI does this; the API equivalent needs a key holding that permission, a key
  with only the User permission answers 403), then re-run the script.
- `end-kasm-session.py` counts a paused session as still present after a destroy, not as gone: a
  paused container still occupies disk, so reporting it destroyed would hide exactly the residue
  the end step exists to clear.

Every `get_kasm_status` call mints a new session token row and a fresh JWT: three status calls on
one session produced three different links. Both scripts therefore ask exactly once and never
poll.

## Verification

The end script prints one line per step and exits 0 only when every executed step succeeded:

- `destroy_kasm: ok` - Kasm accepted the destroy;
- `verify_gone: OK (...)` - `get_kasm_status` no longer reports the session. With a scoped key
  that lacks the "impersonate" permission, a destroyed and an invisible session answer the same
  way; the line says `gone or invisible` instead of guessing;
- `revoke_token: revoked` (or `nothing to revoke`) - the grading token is dead; without the
  grading variables the step prints `NOTE ... skipped`.

Cross-check in the Kasm admin UI (Sessions list must not contain the kasm_id).

## Rollback

There is none for a destroy, by design: re-create the session with
`scripts/create-session.py` (fresh token, fresh container; grading-api keeps the student's
recorded progress). Re-issuing a link is idempotent in the sense that it is safe to repeat: every
call returns a fresh link. Whether the superseded link stops working at that moment was NOT
measured (the freeze matrix confirmed that different calls return different links, not that the
older one dies).

## Known limitations

- A failed destroy leaves nothing half-done by the script (it stops at step 1), but the session
  itself may be untouched: retry and check the admin UI.
- A revoke failure prints the exact manual command (a `POST /api/session/revoke` body); follow it
  - until then the token stays usable until TTL even though the container is gone.
- The Kasm keepalive timer (idle 3600 s then delete, group settings, see #122) remains the only
  automatic path; it neither revokes the token nor fires before the idle window.
- Key storage and rotation is tracked by the security review (#106, #86); the key leaked on
  2026-10-07 was rotated the same day.
- Neither script can resume a paused session. Resume is manual (admin UI) or needs a key with the
  resume permission, and the resume API call itself was not exercised end to end.

Not verified: running these two scripts against a live Kasm instance (developer machine has no
Kasm credentials); their logic is covered by `scripts/tests/test_end_kasm_session.py` and
`scripts/tests/test_reissue_session_link.py`. The Kasm-side behaviours quoted above (the two
answer shapes, the per-call link, the 403 on pause with a User-only key) are separate and ARE
measured on the lab install - see the ADR 0001 appendix once PR #252 merges.
