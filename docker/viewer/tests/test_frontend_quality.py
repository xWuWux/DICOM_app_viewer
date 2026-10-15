"""Console-error and accessibility gates for both panel pages (issue #238 E).

Where test_visual_regression.py pins what the panel LOOKS like, this pins
what it SOUNDS like to a browser and to an assistive technology:

  * console: no `console.error` from our own page code, no uncaught page
    exception, while the panel reaches its first rendered state.
    watermark.html embeds Orthanc's Explorer2 in an iframe (a different
    origin, :8043 -- see watermark.html's orthanc_url); third-party console
    noise is NOT ours to gate, so captured messages are filtered to the
    served page's own origin (the base-url the page itself was opened from).
    `pageerror` needs no filter: it only fires for the main frame.

  * axe-core: WCAG-conformance rules only -- runOnly the wcag tags, NOT
    axe's full rule set (its best-practice rules, e.g. `region`, are
    advisory guidance; gating on them would mix a policy choice into a
    conformance gate). Explicit include-scope over the student-facing
    surface: grading-panel.html whole (the panel IS the page),
    watermark.html #grading-panel only (the Explorer2 iframe beside it is
    third-party markup we do not own; pixel-scope precedent: the
    panel_locator fixture in test_visual_regression.py). The scanner is
    VENDORED (docker/viewer/tests/vendor/, sha256 + npm provenance in its
    README, #67 rules) -- never CDN-fetched at test time.

    The axe gate is BASELINE-BOUNDED (human decision 2026-10-15): the
    findings measured on the real harness are committed as
    a11y_baseline.json; any violation NOT in the baseline fails the run,
    while baseline entries that disappeared only PRINT -- the baseline is
    a human-owned policy artifact discussed with the radiologist
    (deliberately-dim watermark and stage chips vs genuine contrast bugs),
    it shrinks deliberately via --update-a11y-baseline, never reflexively.

The suite shares the VR harness (scripts/test-visual-regression.sh
provisions the stack, this file is plain pytest next to the snapshots) so
both gates see exactly what a visual snapshot sees. Student IDs carry an
EQ_ prefix to never collide with VR_ baselines in a shared grading-db.
"""

import json
import os
import pathlib

import pytest
from test_visual_regression import (
    _advance_to_next_case,
    _mint_token,
    _open,
    _pin_exam_timer,
    _submit_learning,
    _submit_structured,
)

AXE_SCRIPT = pathlib.Path(__file__).parent / "vendor" / "axe-core-4.11.4.min.js"
BASELINE_FILE = pathlib.Path(__file__).parent / "a11y_baseline.json"


def _watch(page, base_url):
    """Attach BEFORE navigation. -> () -> list of (kind, text, url) for OUR page."""
    captured = []

    def on_console(msg):
        if msg.type == "error":
            captured.append(("console.error", msg.text, msg.location.get("url", "")))

    page.on("console", on_console)
    page.on("pageerror", lambda exc: captured.append(("pageerror", str(exc), "")))

    def ours():
        return [c for c in captured if not c[2] or str(c[2]).startswith(base_url)]

    return ours


def _desc(errors):
    return "; ".join(f"{kind}: {text[:140]} @ {url}" for kind, text, url in errors)


def _target_str(node):
    target = node.get("target")
    return " >> ".join(target) if isinstance(target, list) else str(target)


def _is_deliberate(node):
    """Watermark text and INACTIVE stage chips are dimmed ON PURPOSE: the
    forensic watermark must not compete with the diagnostic image (and is
    mandatory per CLAUDE.md), and dimming IS the inactive chip's state.
    Everything else is a genuine contrast-bug candidate for the radiologist
    review."""
    target = _target_str(node)
    html = node.get("html") or ""
    if "watermark" in target or "watermark" in html[:200]:
        return True
    return "stage-chip" in html and "current" not in html and "current" not in target


def _walk_states(page, base_url, page_path, student_id, token, on_state):
    """Walk the full student journey, calling on_state(name) at each
    visually distinct panel state -- the same sequence
    test_visual_regression snapshots (including the pinned exam timer)."""
    _open(page, base_url, page_path, token, student_id, "eq-axe")
    _pin_exam_timer(page)
    on_state("learning-form")
    _submit_learning(page)
    on_state("learning-feedback")
    _advance_to_next_case(page)
    on_state("assessment-form")
    _submit_structured(page)
    on_state("assessment-feedback")
    _advance_to_next_case(page)
    on_state("test-form")
    _submit_structured(page)
    on_state("test-feedback")
    page.click("#next-btn")
    page.wait_for_selector("#approve-btn", timeout=15000)
    on_state("review")
    page.click("#approve-btn")
    page.wait_for_selector("#results-title", timeout=15000)
    on_state("results")


def _axe_run(page, scope):
    page.add_script_tag(path=str(AXE_SCRIPT))
    # NB: an explicitly-passed undefined context is REJECTED by axe.run
    # ("arguments are invalid" -- 4.11.4); whole-document audits pass the
    # document node as the context instead. Learned the empirical way.
    return page.evaluate(
        """(scope) => window.axe.run(
            scope ? {include: [scope], exclude: ["#watermark-rotator"]}
                  : {exclude: ["#watermark-rotator"]},
            {runOnly: {type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]}}
        )""",
        scope,
    )


def _collect(page, base_url, page_path, scope, student_id, token):
    findings = []

    def on_state(state):
        result = _axe_run(page, scope)
        for v in result.get("violations", []):
            by_target = {}
            for node in v["nodes"]:
                t = _target_str(node)
                by_target.setdefault(t, []).append(node)
            for t, nodes in sorted(by_target.items()):
                group = (
                    "deliberate-candidate"
                    if all(_is_deliberate(n) for n in nodes)
                    else "ui-bug-candidate"
                )
                findings.append({
                    "page": page_path,
                    "state": state,
                    "rule": v["id"],
                    "impact": v.get("impact"),
                    "target": t,
                    "count": len(nodes),
                    "group": group,
                })

    _walk_states(page, base_url, page_path, student_id, token, on_state)
    return findings


def _key(f):
    return f"{f['page']}|{f['state']}|{f['rule']}|{f['target']}"


def _table_text(findings, header):
    lines = [header,
             f"{'page':<20} {'state':<20} {'rule':<16} {'impact':<9} {'cnt':<4} {'group':<20} target"]
    for f in findings:
        lines.append(f"{f['page']:<20} {f['state']:<20} {f['rule']:<16} {str(f['impact']):<9} "
                     f"{f['count']:<4} {f['group']:<20} {f['target'][:44]}")
    return "\n".join(lines)


def _print_table(findings, header):
    print(_table_text(findings, header))


def _load_baseline():
    if not BASELINE_FILE.is_file():
        return {}
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return {e["key"]: e for e in data["entries"]}


def _merge_write_baseline(request, page_path, findings):
    """Regenerate the whole file from this SESSION's runs: each param only
    sees its own page, so merge in-run and write once, keeping the other
    page's committed entries when a single page is run in isolation."""
    store = getattr(request.session, "_a11y_merge", None)
    if store is None:
        store = {k: {kk: v for kk, v in e.items() if kk != "key"} | {"key": k}
                 for k, e in _load_baseline().items()}
    for f in findings:
        store[_key(f)] = dict(f, key=_key(f))
    # drop this page's entries that no longer occur, keep the other page's
    for k in [k for k in store if k.startswith(page_path + "|")]:
        if k not in {_key(f) for f in findings}:
            del store[k]
    request.session._a11y_merge = store
    # immediate merged write (NOT a function-scoped finalizer: that fired
    # after the FIRST parametrization and lost the second page's findings --
    # caught in the first real run). Last writer holds the full union.
    payload = {
        "_policy": "human-owned baseline (issue #238 E): the gate fails on NEW keys "
                   "only; regenerate ONLY via --update-a11y-baseline in a deliberate "
                   "run and review the diff with the radiologist",
        "entries": sorted(store.values(), key=lambda e: e["key"]),
    }
    BASELINE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("page_path", ["watermark.html", "grading-panel.html"])
def test_no_console_errors_while_panel_renders(page, base_url, page_path):
    """Reaching the first rendered state exercises token validation,
    /api/session and /api/case -- the paths a student walks on open."""
    student_id = f"EQ_CONSOLE_{page_path.split('.')[0].replace('-', '_')}"
    token = _mint_token(student_id, "eq")
    ours = _watch(page, base_url)
    _open(page, base_url, page_path, token, student_id, "eq")
    page.wait_for_timeout(300)  # late microtask/console fallout after first paint
    assert ours() == [], _desc(ours())


@pytest.mark.parametrize(
    "page_path,axe_scope",
    [("watermark.html", "#grading-panel"), ("grading-panel.html", None)],
)
def test_axe_accessibility_baseline_gate(page, base_url, page_path, axe_scope, request):
    """Fails on violations NOT in a11y_baseline.json; disappeared baseline
    entries print (fixes visible, baseline shrinks only on purpose)."""
    if not AXE_SCRIPT.is_file():
        raise AssertionError(f"vendored axe-core missing: {AXE_SCRIPT} (see vendor/README.md)")
    student_id = f"EQ_AXE_{page_path.split('.')[0].replace('-', '_')}"
    token = _mint_token(student_id, "axe")
    ours = _watch(page, base_url)
    findings = _collect(page, base_url, page_path, axe_scope, student_id, token)
    assert ours() == [], _desc(ours())  # the a11y walk itself must stay silent too

    update = bool(request.config.getoption("--update-a11y-baseline"))
    table = _table_text(findings, f"axe WCAG findings -- {page_path} (scope: {axe_scope or 'whole page'})")
    print(table)
    if update:
        # pytest -q captures stdout; keep the radiologist table as a file
        # artifact too (gitignored next to snapshot failures is wrong -- it
        # lives in the run's workdir instead of the repo):
        report = pathlib.Path(os.environ.get("A11Y_REPORT_FILE", "/tmp/a11y_report.txt"))
        with report.open("a", encoding="utf-8") as fh:
            fh.write(table + "\n")
        _merge_write_baseline(request, page_path, findings)
        return

    baseline = _load_baseline()
    if not baseline:
        raise AssertionError(
            f"a11y_baseline.json missing/empty but {len(findings)} finding(s) exist -- "
            "generate it with a deliberate --update-a11y-baseline run (human decision 2026-10-15)"
        )
    fresh = [f for f in findings if _key(f) not in baseline]
    seen = {_key(f) for f in findings}
    for k in sorted(k for k in baseline if k.startswith(page_path + "|") and k not in seen):
        print(f"baseline entry no longer violated (shrink it on the next deliberate update): {k}")
    if fresh:
        _print_table(fresh, "NEW axe violations (not in a11y_baseline.json) -- these fail the gate")
        raise AssertionError(f"{len(fresh)} new WCAG violation(s) on {page_path}; see table above")
