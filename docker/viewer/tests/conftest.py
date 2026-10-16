"""Shared pytest options for the viewer test suite (docker/viewer/tests).

--update-a11y-baseline (issue #238 E): regenerate
a11y_baseline.json from the current run instead of gating on it, and print
the radiologist-facing table. Run deliberately and review the diff -- the
baseline is a policy artifact the human owns, not something a green run
should silently refresh.
"""


def pytest_addoption(parser):
    parser.addoption(
        "--update-a11y-baseline",
        action="store_true",
        default=False,
        help="rewrite a11y_baseline.json from this run's axe findings and print "
             "the radiologist-facing table (human-owned policy artifact -- review "
             "the diff, never refresh it reflexively)",
    )
