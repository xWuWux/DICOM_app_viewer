#!/usr/bin/env python3
"""
Generates profile.json from upstream-docker-default.json plus a small,
explicit syscall delta -- the fix for issue #52 ("seccomp=unconfined
widens kernel syscall surface for Guacamole PoC's per-student container").

Background: docker/guacamole-weasis/launch-session.sh runs epiphany
(WebKitGTK) for the grading panel. WebKitGTK sandboxes its own renderer
process with bubblewrap (bwrap) internally, which needs a handful of
namespace/mount syscalls Docker's own default seccomp profile blocks for
an unprivileged container -- confirmed the hard way (see that script's own
comment): without *some* relaxation, epiphany crashed outright. The
existing fix for that was `--security-opt seccomp=unconfined`
(scripts/provision-guacamole-session.py), which disables seccomp filtering
entirely -- a much bigger hole than bubblewrap actually needs, flagged as
a known gap in issue #52 itself.

A concrete narrower profile was proposed in
https://github.com/xWuWux/DICOM_app_viewer/issues/52#issuecomment-5888230780
(external contributor, not an Anthropic/Claude Code account) -- credited
here, but independently verified empirically before adopting it, not
trusted on the comment's authority alone:

1. Built the real docker/guacamole-weasis image and launched it three
   ways, comparing `ps aux` inside each:
   - `seccomp=unconfined` (current baseline): epiphany's `bwrap`-wrapped
     xdg-dbus-proxy AND WebKitWebProcess both start and stay alive.
   - Docker's real default profile, no override: epiphany becomes a
     `<defunct>` zombie within seconds -- reproduces the documented crash.
   - This generated profile.json: epiphany's `bwrap`-wrapped processes
     start and stay alive, matching the `unconfined` baseline's process
     topology exactly.
2. Directly probed the syscalls this profile deliberately does NOT add
   (bpf, keyctl, add_key, io_uring_setup, userfaultfd, perf_event_open) via
   raw `libc.syscall()` calls inside a running container on this profile --
   all returned EPERM (blocked at the seccomp layer, before the kernel
   even sees the arguments). The same probe under `seccomp=unconfined`
   reached the kernel itself (EINVAL/EFAULT from bad arguments, not
   EPERM) -- confirming this profile is a real narrowing, not just
   "already safe anyway."

The delta itself, matching the linked comment's own two groupings:
  - Namespace creation: unshare, clone, clone3, setns (clone itself is
    already allowed unprivileged in the upstream base profile via an arg-
    mask rule -- included again here as a plain, unconditional allow
    anyway, since bwrap's exact clone() flags aren't worth re-deriving by
    hand when the broader, still-narrow allow is simpler and was the one
    actually tested above).
  - Filesystem isolation inside bwrap's own unprivileged user namespace:
    mount, umount2, pivot_root, chroot, sethostname. Safe to allow
    outright here for the same reason the comment gives: this container
    has no CAP_SYS_ADMIN in the initial user namespace, so the kernel
    itself restricts what these can actually do once bwrap has entered
    its own unprivileged CLONE_NEWUSER (tmpfs/proc/devpts/bind-mounts
    only) -- seccomp isn't the only backstop here, and this profile only
    ever needs to be as narrow as "what bwrap itself needs," not as
    narrow as each syscall's absolute theoretical minimum.

Upstream base pinned to a specific commit, not "whatever's on GitHub's
master branch today": moby/moby@c74ba95583051d0d49280d242b3b72bc3e54693d
(2025-07-24), daemon/pkg/oci/fixtures/default.json -- fetched once via the
GitHub API and committed verbatim as upstream-docker-default.json in this
same directory. Re-run this script against a newer fetch of that same
upstream path if Docker's own default profile ever needs to be re-synced;
don't hand-edit profile.json directly, it's a generated file.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM_PATH = os.path.join(_HERE, "upstream-docker-default.json")
OUTPUT_PATH = os.path.join(_HERE, "profile.json")

# Independent of any single syscall's exact arg-mask -- see this module's
# own docstring for why a plain, unconditional allow was chosen over
# reverse-engineering bwrap's precise clone()/mount() flag values.
ADDED_SYSCALLS = sorted(
    {
        # Namespace creation
        "unshare",
        "clone",
        "clone3",
        "setns",
        # Filesystem isolation inside bwrap's own unprivileged userns
        "mount",
        "umount2",
        "pivot_root",
        "chroot",
        "sethostname",
    }
)


def build():
    with open(UPSTREAM_PATH) as f:
        profile = json.load(f)

    profile["syscalls"].append(
        {
            "names": ADDED_SYSCALLS,
            "action": "SCMP_ACT_ALLOW",
            "args": [],
        }
    )

    with open(OUTPUT_PATH, "w") as f:
        json.dump(profile, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    build()
