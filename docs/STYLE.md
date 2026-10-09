# Documentation and text style

Status: adopted 2026-10-09 (owner decisions). Applies to every tracked text file in this repository.
Scope: repository files. The language of new GitHub issues, pull request descriptions and comments is a separate
decision that has not been made yet; this document will be extended when it is.

## 1. Language

- Write documentation, code comments, scripts, configuration comments, test names and commit messages in **English**.
- Polish is kept only where Polish is the intended content:
  - text shown to the doctors: the UI pages in `docker/viewer/` (including the expired-session message) and the
    Polish strings asserted by their Playwright tests and snapshots;
  - the Lung-RADS category labels and the placeholder reference reports in `docker/grading-api/app/db.py`
    (from the radiologist's specification);
  - the historical review and audit documents in `docs/history/` (they stay Polish; only errors are fixed).
- Quoting a Polish UI message inside English prose is allowed. Put it in quotes or backticks.
- Do not mix Polish and English inside one sentence.

## 2. Characters

- No emoji or pictographs anywhere (check marks, crosses, stars, icons). Use words: `Yes`, `No`, `PASS`, `FAIL`,
  `Warning:`, `4/5`. A task list uses the Markdown form `- [ ]`.
- No CJK characters. They appear by accident in generated text; remove them and re-read the sentence.
- Code comments, scripts and configuration use ASCII punctuation (a spaced hyphen ` - `, three dots, straight quotes).
  Prose documents (`.md`, `.tex`, `.txt`) may use the em dash.

## 3. Writing style (technical documentation)

- Short sentences, active voice. Instructions are imperative: "Run", "Set", "Check".
- One idea per paragraph. Put the most important fact first.
- Name things exactly: file paths, commands, environment variables and identifiers in backticks.
- Give numbers with units (`2.8 GiB`, `0.88 core`, `15 s`).
- Separate what was verified from what was not: use "Verified:" and "Not verified:" (or "Not covered").
- Explain a term or abbreviation at its first use. Avoid marketing language and filler.
- Use headings for structure, numbered lists for ordered steps, tables for comparisons.

## 4. Document structure

| Document | Sections |
|---|---|
| Procedure or runbook | Purpose, Scope, Prerequisites, Steps, Verification, Rollback, Known limitations |
| Design or decision record | Context, Decision, Alternatives considered, Consequences |
| Pull request description (current practice) | Summary, Changes, Verification, Not covered |
| Issue (current practice) | Problem, Scope, Acceptance criteria |

## 5. Glossary

Terms that appear in the product (Polish) and their English names in documentation. Identifiers in code keep their names.

| Polish (product or history) | English (documentation) |
|---|---|
| nauka / ocena / test | learning / assessment / test (the three stages) |
| przypadek | case |
| badanie | study |
| opis referencyjny | reference report |
| prawdziwa diagnoza, wzorzec | ground truth |
| kategoria Lung-RADS, modyfikator S | Lung-RADS category, modifier S |
| znak wodny | watermark |
| sesja, link do sesji | session, session link |
| koordynator | coordinator |
| zakres | scope |
| kryteria akceptacji | acceptance criteria |
| `zgłoszenie` | issue |

The code uses `student_id` for the person who takes the exam (a doctor or radiologist); keep the identifier, write "user" or "doctor" in prose.

## 6. Automated check

`python3 scripts/check-text-style.py` (run by `scripts/lint.sh`, and therefore by the `lint` job in CI) reads the files that
git tracks and fails on:

1. `PICTOGRAPH`: any emoji or pictograph character;
2. `CJK`: any CJK character;
3. `DASH`: an em or en dash in code or configuration (a few lines of Polish product text are allowlisted);
4. `POLISH`: Polish letters outside the allowed paths and outside quoted strings.

The allowlists are constants at the top of the script (`POLISH_ALLOWED_PATHS`, `PRODUCT_DASH_LINES`). Extend them only for product
content or historical records, and say why in the pull request.

## 7. AI-assisted writing

Generated text must be proofread before it is committed or posted. Run the check, look for stray non-Latin characters and for
sentences that mix two languages, and verify every number and file path against the code.
