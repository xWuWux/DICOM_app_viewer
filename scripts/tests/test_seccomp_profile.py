"""
Regression tests for docker/guacamole-weasis/seccomp/profile.json (issue
#52): the narrow seccomp profile that replaced blanket
`seccomp=unconfined` for the Guacamole PoC's per-student container.

Static checks only -- no Docker needed. The real proof that this profile
actually lets epiphany's bwrap sandbox initialize (and still blocks
bpf/keyctl/io_uring/userfaultfd/perf_event_open) was done by hand against
a live container; see build-profile.py's own docstring for exactly how.
These tests guard the two ways that verification could silently go stale:
someone hand-edits profile.json out of sync with build-profile.py, or
someone widens the delta to include a syscall that was never verified.
"""
import json
import os
import subprocess
import sys

_SECCOMP_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "docker", "guacamole-weasis", "seccomp"
)
_PROFILE_PATH = os.path.join(_SECCOMP_DIR, "profile.json")
_BUILD_SCRIPT = os.path.join(_SECCOMP_DIR, "build-profile.py")

# Independently declared here, not imported from build-profile.py's own
# ADDED_SYSCALLS -- same reasoning as scripts/tests/test_copy_lockdown.py's
# EXPECTED_DISABLED_KEYS: importing it would make a silent widening of the
# delta invisible to this test.
EXPECTED_ADDED_SYSCALLS = {
    "unshare",
    "clone",
    "clone3",
    "setns",
    "mount",
    "umount2",
    "pivot_root",
    "chroot",
    "sethostname",
}

# Never allowed unconditionally by this profile -- confirmed empirically
# (build-profile.py's docstring) that each still returns EPERM under it.
# Not exhaustive, just the syscalls issue #52's own report named as the
# actual widened-blast-radius risk from seccomp=unconfined.
NEVER_UNCONDITIONALLY_ALLOWED = {
    "bpf",
    "keyctl",
    "add_key",
    "request_key",
    "ptrace",
    "perf_event_open",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "userfaultfd",
    "kexec_load",
    "init_module",
    "delete_module",
}


def _load_profile():
    with open(_PROFILE_PATH) as f:
        return json.load(f)


def _unconditional_allow_names(profile):
    """Syscall names allowed with no args/caps/arches restriction at all --
    the only kind of rule that means "always allowed, full stop"."""
    names = set()
    for group in profile["syscalls"]:
        if group.get("action") != "SCMP_ACT_ALLOW":
            continue
        if group.get("args") or group.get("includes") or group.get("excludes"):
            continue
        names.update(group.get("names", []))
    return names


def test_profile_is_generated_from_the_current_build_script():
    """Catches profile.json being hand-edited (or left stale after
    build-profile.py itself changes) without regenerating it.

    build-profile.py overwrites profile.json in place -- back up and
    restore the committed bytes directly (not via git, which would need
    the file already committed/tracked to have anything to check out
    from) so this test has no lasting side effect on the working tree
    regardless of git state.
    """
    with open(_PROFILE_PATH, "rb") as f:
        committed_bytes = f.read()

    try:
        result = subprocess.run(
            [sys.executable, _BUILD_SCRIPT], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

        with open(_PROFILE_PATH, "rb") as f:
            regenerated_bytes = f.read()
    finally:
        with open(_PROFILE_PATH, "wb") as f:
            f.write(committed_bytes)

    assert regenerated_bytes == committed_bytes, (
        "profile.json does not match what build-profile.py currently "
        "generates -- run `python3 docker/guacamole-weasis/seccomp/"
        "build-profile.py` and commit the result"
    )


def test_profile_still_defaults_to_deny():
    profile = _load_profile()
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"


def test_profile_allows_exactly_the_expected_namespace_and_mount_syscalls():
    profile = _load_profile()
    allowed = _unconditional_allow_names(profile)
    missing = EXPECTED_ADDED_SYSCALLS - allowed
    assert not missing, f"expected these to be unconditionally allowed: {missing}"


def test_profile_never_unconditionally_allows_the_dangerous_syscalls():
    profile = _load_profile()
    allowed = _unconditional_allow_names(profile)
    leaked = NEVER_UNCONDITIONALLY_ALLOWED & allowed
    assert not leaked, (
        f"these syscalls must stay blocked (or capability-gated), not "
        f"unconditionally allowed: {leaked}"
    )
