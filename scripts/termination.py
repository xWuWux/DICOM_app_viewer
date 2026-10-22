"""Shared, import-side-effect-free termination handling for provisioning CLIs."""

import signal


class TerminationSignal(KeyboardInterrupt):
    """A KeyboardInterrupt that remembers which signal caused it (issue #142).

    Subclassing KeyboardInterrupt keeps every existing `except KeyboardInterrupt`
    path -- above all the compensation in main() -- working unchanged; main()
    only reads `signum` at the very end to pick the conventional exit code."""

    def __init__(self, signum: int):
        super().__init__()
        self.signum = signum


def _interrupt(signum, frame):
    """SIGTERM/SIGHUP -> KeyboardInterrupt, so the compensation in main()
    actually runs (review nit on issue #104, CR w4:p1). The default action for
    both is "die silently", which orphaned a token exactly like Ctrl-C did --
    CI job cancellation and a closed terminal are the ordinary ways this script
    gets killed in practice, not Ctrl-C.

    SIGKILL and os._exit() stay uncoverable; the token's own TTL
    (GRADING_TOKEN_TTL_SECONDS, capped at 7 days by app/config.py) is the
    backstop, which is why README.md names the TTL rather than this handler as
    the last line of defense.

    Issue #142: the raised exception carries the signal number, so main() can
    exit 128 + signum (143 SIGTERM, 129 SIGHUP) instead of 1 -- a cancelled
    run and a failed one must look different to CI and to the operator."""
    raise TerminationSignal(signum)


def install_termination_handlers() -> None:
    """Only ever called from main() -- importing this module must never install
    handlers as a side effect (a test or a future import would inherit them).
    ValueError from a non-main thread is deliberately swallowed: no handler is
    better than a crash for a caller that cannot have one.

    An inherited SIG_IGN is left exactly as it is (review nit N1): that is what
    `nohup` and `trap '' HUP` mean, and overriding it would turn a run that used
    to survive its terminal closing into one that aborts mid-flight. Ignoring a
    signal is its caller's deliberate choice; terminating on one is the default
    this handler exists to improve on."""
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            if signal.getsignal(sig) == signal.SIG_IGN:
                continue
            signal.signal(sig, _interrupt)
        except ValueError:
            pass
