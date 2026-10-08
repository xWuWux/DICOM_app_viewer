# Review record — issue #104 (`scripts/create-session.py` orphaned token + unverified TLS)

Traceability record for PR #134 (DoD 19 / DoD 45), committed so the outcome
lives with the code rather than on one machine. Three rounds, all read-only
reviews; no reviewer amended or force-pushed anything.

- **Round 1 and Round 2** — a second agent in the same workspace, before the PR
  existed, on the branch as it stood then. Both verdicts "approve with nits", no
  blockers; both re-ran the suites rather than trusting the report, and both ran
  mutation tests (a mutation is the only evidence that a test guards something).
  Their findings were then fixed in two follow-up commits — including one real
  regression the first fix itself introduced (`signal.signal(SIGHUP, ...)`
  overwriting an inherited `SIG_IGN`, i.e. breaking `nohup`), which is how Round
  2 found it.
- **CR (Claude, AI-assisted; human reviewer @xWuWux)** — on PR #134, verdict
  **APPROVE WITH SHOULD-FIXES, no blockers**. Reproduced below verbatim.

The mutation tables below are the strongest claim in this record: several tests
were shown to fail when the guard they exist for was removed, and the only
surviving mutants were equivalent ones. Where a review said "out of scope", the
scope note in the PR body is what was implemented — no silent drift.

---

## CR on PR #134 (verbatim)

**CR (Claude, AI-assisted; human reviewer: @xWuWux): APPROVE WITH SHOULD-FIXES. No blockers.**

### What I verified (not just read)
- Checked out the PR head in a clean worktree: `docker/grading-api` **132 passed**, `scripts/tests` **51 passed**, `ruff check` clean, `bandit -r app` no findings. (CI had 2 jobs still pending when I looked; wait for green before merge.)
- Ran 3 **mutations** myself, each caught by named tests: (1) disable the revoke-on-failure branch -> 7 tests fail; (2) make TLS verification default off (`_create_unverified_context`) -> 4 fail; (3) remove the coordinator-key check from `/session/revoke` -> 2 fail. The tests pin the behaviour, not just the call.
- Ran a probe with a faked network: mint OK, `request_kasm` raises a read timeout -> the token **is** revoked (`/api/session/revoke` called) and the token value is never printed. Compensation works on the path the ticket is about.
- Design reasoning is sound: compensation instead of reordering (token must exist before `request_kasm` injects it), revoke only while `kasm_id is None`, no auto-revoke-by-student on an ambiguous mint, `/session/revoke` is coordinator-gated, always 200 on unknown token, manual validation to avoid echoing a token in a 422, TLS verification default with `--ca-bundle` appended to (not replacing) the system store, `--insecure` + `--ca-bundle` rejected.

### Should fix
1. **Audit trail for revocation (DoD 40, ties to #98).** `/session/revoke` success path writes no log line. Add `logger.info("session_revoked", extra={"by": "token"|"student_id", "count": n})` (never the token). Revocation is a security-relevant action performed with the coordinator key; today it leaves no server-side trace.
2. **Raw traceback on read timeouts / bad JSON.** In my probe a `TimeoutError` raised while reading the Kasm response escapes `api_call` (only `HTTPError`/`URLError` are mapped), so the operator gets a Python traceback and exit code 1 after the compensation has already run. Same for `json.loads` on a non-JSON 200 (`ValueError`). Map `TimeoutError`/`ConnectionError`/`ValueError` (JSONDecodeError) to `ApiError(..., ambiguous=True)` and add a test. Compensation is correct either way; this is about a clean message.
3. **Orphan Kasm container (ticket needed).** You list it as out of scope: agreed, but please file the ticket now (revoking the token while `request_kasm` may have started a container leaves a running, token-less session consuming one of the scarce session slots). Also file the `provision-guacamole-session.py` twin you mention. I'd label both `tier: 4, dod-audit`.

### Nits
- The docstring in `SessionRevokeBody` says constrained fields would echo `input` in the 422 body; since #93 the handler returns a generic body and sanitises the log, so the *reason* is now only "defence in depth". Harmless; reword so nobody trusts the old claim.
- `main.py` header comment references `#99` for the shared pattern; fine, but the revoke path has no test for `student_id` with a trailing newline (`"abc\n"`). `re.fullmatch` rejects it, I checked; a one-line test would pin it.
- Not run (as you stated): `scripts/lint.sh`, `build-test.sh`, `smoke-test.sh` need Docker/Kasm; CI covers lint/build.

### Process note
The PR body cites reviews stored in `/home/lk/reviews/issue-104-review.md`, a path not in the repo. For traceability (DoD 19/45), paste the review outcome into the PR or commit it under `docs/history/`.

Ready to merge after (1) and (2) are fixed or consciously deferred, and CI is green. Docs changes (README, LOCAL_SETUP_GUIDE, PROXMOX_DEPLOYMENT) read correctly to me; I did not re-test the documented commands against a live Kasm.


---

## Rounds 1 and 2 (pre-PR, second agent)

# Review — issue #104 (`fix/104-token-orphan-tls-verify`)

Reviewer: pi w4:p1. Review only; nothing in the repo was modified.
Reviewed commit: **`94afb47`** (HEAD of branch = origin). The brief names `b851e89`;
that commit was amended (message-only: `git diff b851e89 94afb47` is empty) and the
branch on origin is already at `94afb47`.

## VERDICT

**approve z nitami.** No blocking defects. The one design decision (compensation
instead of reordering) is sound.

Independently re-run (not taken on trust), with PYTHONDONTWRITEBYTECODE=1 and
no pytest cache so the worktree stayed clean:

- `docker/grading-api`: `pytest -q` → 127 passed; `ruff check` clean;
  `ruff format --check` → 18 files already formatted; `bandit -q -r app` → no findings.
- `scripts/tests`: `pytest -q` → 24 passed.
- diff = 7 files, 785+/94-; no `.venv-test`/`__pycache__` in the commit.
- grep of the diff for hard-coded secrets: none (tests use `tok-AAAA…` fixtures).

## BLOCKING

Brak.

## NITS

1. **SIGTERM / SIGHUP are not handled.** `except BaseException` only catches what
   Python turns into an exception. `kill`, closing the terminal, CI job
   cancellation (SIGTERM) end the process with no compensation → orphan token,
   the same bug class as Ctrl-C. Cheap fix: `signal.signal(SIGTERM/SIGHUP,
   lambda *_: sys.exit(...))` before the mint (or raise KeyboardInterrupt).
   SIGKILL/`os._exit` cannot be covered; TTL is the backstop and should be
   named in the docs.
2. **Second Ctrl-C during revoke.** `revoke_token` catches `Exception`, so a
   KeyboardInterrupt during the compensation call escapes `compensate()` with a
   traceback and the token still live. Low value to fix; document or catch
   BaseException there and print the "NOT revoked" hint.
3. **Ambiguous mint outcome.** If `POST /session` times out *after* the server
   committed, `grading_token` is None → no compensation and no hint at all.
   Print the same "revoke by student_id" hint on that path (do **not**
   auto-revoke by student_id: if the mint never happened, that would kill a
   prior live session of that student).
4. **Orphan Kasm container.** When `request_kasm` returns 200 without `kasm_id`
   (or times out after Kasm started it), the token is revoked but a Kasm
   container may still exist. Out of scope for #104, but worth one sentence in
   the ticket for the sibling script (destroy_kasm).
5. **`json.dumps(created, indent=2)` in the "no kasm_id" ApiError is not
   truncated** and is printed whole, while `_MAX_ERROR_BODY_CHARS` and its comment
   claim bounded error text. The 200-without-kasm_id response can carry
   `kasm_url`/other fields. Apply the same cap (and ideally drop `kasm_url`).
6. **One `ctx` for two hosts.** `--ca-bundle` replaces the system store for
   both Kasm and grading-api, so "Kasm on a public CA + grading-api on a private
   CA" cannot be expressed. Fine today (grading-api default is
   `http://localhost`), but note it in the docs. Related, pre-existing: the
   coordinator key goes in clear if `GRADING_API_URL` is `http://` and
   non-loopback; consider a warning.
7. Docs: `README.md` advertises `--insecure` in an example line; fine for the
   lab case, but keep `--ca-bundle` first (it is, in the later paragraph).

## ODPOWIEDZI NA 6 PYTAŃ

1. **`except BaseException` + `compensate()`.** Right shape. `sys.exit` from a
   library call is caught (SystemExit ⊂ BaseException). What can still bypass it:
   `os._exit`, SIGKILL, **SIGTERM/SIGHUP** (nit 1), a second KeyboardInterrupt
   inside the revoke (nit 2), interpreter crash. `compensate()` reads
   `grading_token`/`kasm_id` via closure at call time (correct — not captured
   at def time). `raise` for unknown exceptions re-raises after compensation: good.
2. **Revoke by `student_id`.** Acceptable, keep. It deletes *all* tokens of
   that student, including a live session's — but `POST /session` already does
   exactly that (main.py:196), so it adds no new power, and it is
   coordinator-gated with the same compare_digest check. The script never uses
   it; it is an operator tool for the lost-token case. If you want least
   privilege, drop it — but then the printed "NOT revoked" hint has to change.
   My preference: keep, and don't auto-call it from the script (nit 3).
3. **`200 revoked:false` vs 404.** Keep 200. Compensation callers must be
   idempotent; the body already says `revoked:false, count:0`, which is what an
   operator needs to notice a typo. Optionally log `revoked=false` server-side.
4. **`--insecure` surviving.** Defensible: AC says "TLS verification on by
   default (CA via parameter)", not "no escape hatch". Conditions are met: must
   be typed (no env var), warns every run, refused with `--ca-bundle`, cert
   failure message points to `--ca-bundle`. Tests pin each.
5. **Scope (`provision-guacamole-session.py`).** Right call to keep out of this
   diff (its tests and failure semantics differ), but the gap is real and is
   now a one-line fix. Open the follow-up ticket *now* and link it from #104;
   the commit message currently only promises it.
6. **`TLS_CA_BUNDLE` not in `.env.example`.** Acceptable given that file is
   known-stale and the variable is read by an operator script, not by
   compose services; it is documented in README/docs. A one-line commented
   entry would cost nothing, though.

## CO PRZEWIDZIAŁY TESTY

Tests are not decorative. Mutation check on a throw-away copy in /tmp
(repo untouched):

| mutation | result |
|---|---|
| drop `and not kasm_id` (always revoke) | `test_interrupted_after_kasm_exists_does_not_revoke` FAILS |
| `except Exception` instead of `BaseException` | KeyboardInterrupt escapes pytest — run aborts (4 passed then crash) |
| print the token in the "NOT revoked" warning | `test_failed_revoke_warns_without_leaking_the_token` FAILS |
| `ctx=None` for the `request_kasm` call | `test_happy_path_…never_revokes` FAILS (every request must carry a CERT_REQUIRED context) |

So the "po kasm_id nie revoke'ujemy" test is real (it reaches `time.sleep` via
status "building" and would fail if the guard is removed). Not covered by any
test: SIGTERM/SIGHUP (nit 1), second Ctrl-C during revoke (nit 2), ambiguous
mint timeout (nit 3), and 5xx/timeout of `/session/revoke` on the server side
beyond the 12 endpoint tests. The #93 invariant (422 never echoes the token)
is covered in `test_session_revoke.py`; I did not find a path that logs the
token server-side.

---

# REVIEW 2 (c0b3cf6)

Reviewer: pi w4:p1. Review only; repo untouched (mutations ran on a /tmp copy, removed).
Scope: `git show c0b3cf6` (create-session.py, its tests, README), plain push on top of `94afb47`.

## VERDICT

**approve z nitami.** The previous nits are addressed correctly. One new
behavioural regression (nit N1: `nohup`) is small but real and worth fixing
before merge; it is a 3-line change, not a redesign.

Re-run independently: `scripts/tests` 37 passed (24 in test_create_session.py),
`docker/grading-api` 127 passed, `ruff check` clean, `ruff format --check` 18 files
formatted, `bandit -q -r app` no findings.

## BLOCKING

Brak.

## NITS

- **N1 (fix before merge): handler overrides an inherited SIG_IGN.**
  `signal.signal(SIGHUP, _interrupt)` replaces `SIG_IGN`. Verified:
  under `trap '' HUP` (what `nohup` does) `getsignal(SIGHUP)` is `1` before
  `install_termination_handlers()` and the `_interrupt` function after. So
  `nohup create-session.py … &` used to survive terminal close and now dies
  (with compensation, so no orphan, but the run is lost). Fix: for each signal,
  `if signal.getsignal(sig) == signal.SIG_IGN: continue` (same for SIGTERM).
  Add a test: set `SIG_IGN`, call install, assert still `SIG_IGN`.
- **N2: ambiguous-mint hint fires on definite failures too.** `mint_attempted and
  not grading_token` is true for a clean `HTTP 401/403/400` from POST /session
  (wrong coordinator key, bad student_id) — the most common error — and prints
  "did not return a token … check whether a token exists" with a `!!` prefix.
  Those responses are unambiguous (nothing was minted). Suggest: print the hint
  only when the failure is not an `HTTPError` (timeout/URLError/connection
  reset/200 without token). Test: 401 → no hint.
- **N3: loopback detection is a hard-coded tuple.** `127.0.0.2`, `127.1.2.3`,
  `[::ffff:127.0.0.1]` warn falsely; `HTTP://` upper-case scheme is missed.
  Use `urllib.parse.urlparse(url).scheme.lower()` and
  `ipaddress.ip_address(host).is_loopback` (with `localhost` special-cased).
  `0.0.0.0` / `::` as *destination* are not loopback in the strict sense, but
  treating them as local is harmless.
- **N4: SIGHUP is not pinned by any test** (mutation D, below, survives).
  Parametrize `test_sigterm_handler_maps_to_keyboard_interrupt` over both signals.
- **N5: exit code.** `sys.exit("interrupted")` gives status 1 for SIGTERM/SIGHUP;
  conventional 143/129 would let CI distinguish a cancellation from a failure.
  Optional.
- **N6: `load_verify_locations` of a non-PEM file** raises `ssl.SSLError`
  before the `try:` → plain traceback. No secret in it (no token exists yet),
  but `parser.error(...)` would be consistent with the `isfile` check above it.

## ODPOWIEDZI NA TRZY PYTANIA

**(a) Swallowing `BaseException` in `revoke_token` vs re-raising.** Swallowing is
the right call *here*: `compensate()` runs inside main()'s own `except`, and
`main()` ends in `sys.exit`/`raise` regardless, so nothing is lost by absorbing
the second signal — what you buy is that the "NOT revoked" hint is always
printed. Costs: a user who mashes Ctrl-C waits up to one 15 s request, and the
hint is conservative (the revoke may in fact have completed server-side).
Two small refinements, neither required: (1) remember the first
`KeyboardInterrupt`/`SystemExit` and re-raise it after the hint so the exit
status reflects it; (2) `GeneratorExit`/`MemoryError` are also swallowed —
irrelevant in practice.

**(b) Handlers in `main()`.** Installing in `main()` (not at import) and before the
first request is correct; a signal that lands between the mint returning and
`grading_token` being assigned is covered by the new `mint_attempted` branch.
Interactive Ctrl-C is unaffected (SIGINT already was KeyboardInterrupt). Risk
for bash/CI: (i) `nohup`/`trap '' HUP` regression → N1; (ii) `ValueError` from
non-main thread is correctly swallowed; (iii) a wrapper that sends SIGTERM and
expects an immediate exit now waits for at most one 15 s revoke request — fine.
Windows: `SIGHUP` guarded by `getattr`. OK.

**(c) Signal test via spy + direct `_interrupt(...)`, without `os.kill`.**
Sufficient for what it pins, soft for what it doesn't:
- real: removing the `install_termination_handlers()` call fails
  `test_main_installs_the_handlers_before_minting` (mutation A);
  SIGTERM→KeyboardInterrupt mapping and compensation path are pinned;
- soft: no test sets `SIGHUP` (mutation D survives); no test proves the OS-level
  delivery reaches `_interrupt` (a typo in the registered handler or an
  earlier `signal.signal` call elsewhere would pass). Cheap upgrade without
  risk to the pytest process: after `install_termination_handlers()` call
  `signal.raise_signal(signal.SIGTERM)` inside a `pytest.raises(KeyboardInterrupt)`
  (the autouse `_restore_signals` fixture already restores handlers) — real
  delivery, in-process, no `os.kill` on pytest. I'd add it together with N4.

## MUTACJE (kopia w /tmp, repo nietknięte)

| mutation | result |
|---|---|
| A: remove `install_termination_handlers()` call | FAILS `test_main_installs_the_handlers_before_minting` |
| B: back to `create_default_context(cafile=…)` | FAILS `test_ca_bundle_adds_to_the_system_store_instead_of_replacing_it` |
| C: `except Exception` in `revoke_token` | FAILS `test_ctrl_c_during_the_revoke_call_still_reports` (leaked KeyboardInterrupt, cleanly asserted) |
| D: drop SIGHUP from the loop | **SURVIVES** (24 passed) → N4 |
| E: remove ambiguous-mint hint | FAILS 2 tests |
| F: no cap on the no-kasm_id text | FAILS `test_no_kasm_id_error_text_is_bounded` |
| G: auto-revoke by student_id on ambiguous mint | FAILS both ambiguous-mint tests |
| H (probe, not mutation): inherited SIG_IGN on SIGHUP | handler overrides it → N1 |

## GOTOWE TYTUŁY TICKETÓW

1. `[Tier 4] provision-guacamole-session.py: odwołaj token (POST /api/session/revoke) przy błędzie po mincie; ta sama kompensacja co w create-session.py (#104)`
2. `[Tier 4] create-session.py / provision-guacamole-session.py: sprzątanie osieroconego kontenera Kasm (destroy_kasm) przy 200 bez kasm_id lub timeoucie po starcie request_kasm`
3. `[Tier 4] create-session.py: nie nadpisuj odziedziczonego SIG_IGN dla SIGHUP/SIGTERM (nohup); dodaj test SIGHUP i realne dostarczenie sygnału (raise_signal)` — tylko jeśli N1/N4 nie wejdą do tego PR
4. `[Tier 4] create-session.py: podpowiedź "ambiguous mint" tylko dla błędów nie-HTTP; wykrywanie loopback przez ipaddress; kody wyjścia 143/129 przy sygnałach` — zbiór N2/N3/N5/N6, niski priorytet
5. (opcjonalnie) `[Tier 4] docs: dopisać TLS_CA_BUNDLE do .env.example przy okazji odświeżenia tego pliku`

