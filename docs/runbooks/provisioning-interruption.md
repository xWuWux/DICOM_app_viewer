# Provisioning interruption recovery

## Purpose

Recover a Guacamole provisioning run interrupted by Ctrl-C, SIGTERM or SIGHUP
(issue #140). The provisioner runs its existing undo stack in reverse order.
It exits 1 for Ctrl-C, 143 for SIGTERM and 129 for SIGHUP. An inherited ignored
SIGTERM or SIGHUP stays ignored, including SIGHUP under `nohup`.

## Scope and prerequisites

Use the Docker host and coordinator credentials used for provisioning.
Keep credentials and session-link files out of logs and issue comments.
Successful sessions keep their token. This change does not alter Kasm's
existing compensation policy; both CLIs now import `scripts/termination.py`.
Deploy that file alongside the scripts (copying a single CLI is insufficient).

## Steps

1. Read stderr for failed undo steps and the affected container name.
2. If container removal failed, restore Docker access and remove the affected
   container first. Confirm it is absent with `docker inspect`. An unavailable
   Docker daemon is not evidence of absence. The existing teardown helper does
   not gate token revocation on its container-removal result.
3. Once removal is confirmed, run
   `python3 scripts/teardown-guacamole-session.py --student-id STU_ID --session-id SESSION_ID`
   with the actual student and session IDs. This command
   is idempotent and checks for sibling sessions. Do not blindly revoke a
   student's token: revocation affects that student's other sessions too.
4. Resolve Docker or API availability failures and retry teardown as needed.

## Verification

Check teardown output for remaining errors. Confirm the affected container,
Guacamole connection and session-link file are gone. Shared user/token state
may remain when another session exists. If container removal is not confirmed,
provisioning rollback leaves the token alone and prints a recovery instruction.
A second interruption inside an undo is recorded as a failed step; remaining
undos are still attempted with the same token-safety guard.

Verified by mocked tests: real OS signal delivery during provisioning,
cleanup ordering, secret-free interruption diagnostics, conventional exit
codes, and token preservation on failed removal or sibling-check uncertainty.
Existing successful-provisioning and Kasm compensation tests remain applicable.

## Rollback

Revert the change as a unit, including both CLIs and their shared module.
Reverting restores the previous Guacamole interruption gap. No database schema,
API contract, image or configuration migration is required.

## Known limitations

This is best-effort compensation, not a durable transaction or host failover.
SIGKILL, power loss, and signals outside protected cleanup calls cannot guarantee
cleanup. An API may commit a resource before an interrupted response reaches the
client; not every such resource has an undo registered yet. Inspect and reconcile
leftovers rather than treating an interrupted request as proof nothing happened.
The existing reaper and grading-token TTL remain backstops; a live Docker/Guacamole
recovery drill is still needed to validate deployment behavior. Concurrent
provisioning for the same student is not made transactional by this change.
