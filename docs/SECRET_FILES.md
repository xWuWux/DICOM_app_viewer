# Mounted secrets: decision and operating procedure (issue #216)

This is an incremental migration. Only the coordinator key has an implemented
file contract in this change. Issue #216 remains open for the other consumers.
Environment mode remains compatible, with its existing metadata exposure.

## Decisions

| Secret / consumer | Decision and remaining work |
| --- | --- |
| Coordinator key: grading API and session CLI tools | Opt-in file mode implemented, including provisioning rollback, teardown, reaper and demo launcher. No export of the file contents. |
| Orthanc password | Defer migration until RegisteredUsers JSON, health check and nginx are changed together. Orthanc supports mounted JSON configuration; a password file alone does not configure RegisteredUsers. |
| nginx Orthanc Basic Auth | Current entrypoint exports the derived ORTHANC_AUTH_B64. Reading a password file then exporting this value would preserve credential exposure. Render a private config without an exported credential and test the running image. |
| Guacamole Postgres password | Defer. The official Postgres entrypoint's file_env reads POSTGRES_PASSWORD_FILE and exports POSTGRES_PASSWORD. Native FILE support alone does not meet the process-environment criterion. Test a wrapper against the pinned image before adopting it. Changing this file also does not rotate an existing database user's password. |
| Guacamole JDBC password | Defer pending pinned 1.6.0 image test. Its startup enables environment properties, but file environment properties require separate enablement. Verify startup and reconnect before migration. |
| Per-session GRADING_TOKEN and VNC_PASSWORD | Defer until the complete launcher/container consumer chain can use files with atomic creation, rollback cleanup and teardown cleanup. Merely creating a VNC password file leaves the original environment value exposed. |
| Kasm API credentials | Existing environment configuration remains; not covered by the coordinator migration. |

Sources reviewed: [Orthanc Docker configuration](https://orthanc.uclouvain.be/book/users/docker-orthancteam.html),
[Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/),
[Postgres entrypoint](https://github.com/docker-library/postgres/blob/master/docker-entrypoint.sh),
[Guacamole 1.6.0 startup](https://github.com/apache/guacamole-client/blob/1.6.0/guacamole-docker/entrypoint.d/100-generate-guacamole-home.sh),
and [GUACAMOLE-2119](https://issues.apache.org/jira/browse/GUACAMOLE-2119).
Upstream source support is not a substitute for a running pinned-image test.

## Coordinator file contract

- Set GRADING_COORDINATOR_KEY_FILE for the API or operator scripts. This is a path
  in that process's filesystem, not the secret value.
- A nonempty GRADING_COORDINATOR_KEY together with FILE is an error. An explicitly
  selected bad file never falls back to environment mode.
- Use a regular file, mode 0400 or 0600, readable by the process owner. File input
  is bounded to 4096 bytes, printable ASCII without whitespace, at least 32
  characters. One terminal LF or CRLF is accepted. Legacy environment validation
  is unchanged.
- Errors contain setting names, never file paths or values. Contents are read
  into memory, never copied into os.environ or child-process arguments.
- Optional teardown/reaper revocation remains optional when neither source is
  configured. A configured but invalid file stops the tool before side effects.

## Enable on a Linux Docker host

Keep files outside the checkout and container images. Local Compose secrets are
bind mounts: their host ownership and permissions matter. Do not make the file
world-readable or assume Compose uid/mode settings repair its ownership.

Example, for a new installation (use the existing key when migrating):

```sh
install -d -m 0700 "$HOME/.config/dicom-viewer"
(umask 077; openssl rand -hex 32 > "$HOME/.config/dicom-viewer/coordinator")
sudo install -d -m 0700 /etc/dicom-viewer/secrets
sudo install -o 10001 -g 10001 -m 0600 \
  "$HOME/.config/dicom-viewer/coordinator" /etc/dicom-viewer/secrets/coordinator
unset GRADING_COORDINATOR_KEY
export GRADING_COORDINATOR_SECRET_PATH=/etc/dicom-viewer/secrets/coordinator
docker compose -f docker-compose.yml -f docker-compose.coordinator-secret.yml \
  up -d --build --force-recreate grading-api
export GRADING_COORDINATOR_KEY_FILE="$HOME/.config/dicom-viewer/coordinator"
```

Keep the other required settings, including ORTHANC_PASSWORD, configured as
before. Remove the old coordinator value from .env and any demo --kasm-env file.
The overlay clears the container's value even if the base env file contains it;
operator tools intentionally reject competing sources. The operator copy and
server copy must contain the same key. Do not overwrite an existing key with
this generation example during an upgrade.

For a remote host use docker-compose.remote-host.yml as the base. Append the
secret overlay last. GRADING_COORDINATOR_SECRET_PATH is the **Docker host** path;
GRADING_COORDINATOR_KEY_FILE for a CLI is the **operator machine** path.
Provision these files separately over your existing secure deployment channel.
Do not pass the key as a CLI argument or export a shell substitution of its contents.

## Failure, rotation and rollback

The API reads the key at startup. A bad mount, mode or key fails closed and leaves
it unhealthy; there is no automatic fallback to a default or previous key.
Stage and check both copies before a maintenance restart. Retain the previous
private file until verification succeeds. Atomically replacing a host file can
leave an existing single-file bind mount on the old inode: **force-recreate** the
API container after replacement, rather than relying on a restart alone.

Rollback by restoring the previous private file and force-recreating the API,
then restore matching operator configuration. Do not delete the grading database
or volumes. Existing session tokens are independent of the coordinator key;
rotation does not revoke them. Provisioning compensation still revokes a newly
minted token if subsequent Guacamole setup fails. Teardown does not delete the
shared operator-owned coordinator file.

File mode reduces inspect/process-environment exposure; it does not protect
against host root, Docker socket access, application memory disclosure or crash
dumps. Other secrets listed above still have their existing exposure. This is
not a complete secrets-hardening or high-availability claim.

## Verification

Unit tests cover file parsing, permissions, missing/unreadable files, source
conflicts, sanitized errors, unchanged environment and API startup validation.
Provisioning tests cover file-key minting and rollback revocation alongside legacy
environment mode. Existing API and session-management regression suites remain.

`python3 scripts/test-coordinator-secret.py` builds a disposable API container,
mints a session, reads a case, rejects a wrong key, repeats after restart and
checks a random sentinel is absent from inspect, every running process's
environment/arguments and logs. It publishes no host ports and removes only its
own container and anonymous volume. The smoke CI job runs it. Passing unit tests
alone does not establish these Docker-level properties; this check must pass
before merging. Full viewer/Kasm/Guacamole regressions remain CI requirements.
