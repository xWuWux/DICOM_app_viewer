# Runbook: grading.db schema migration (#96) and changing a ground truth

Status: DRAFT, MVP stage. Real-data rollout needs the owner's WRITTEN go (CLAUDE.md: destructive DB operations).

## A. Rolling out a schema migration to an existing database
1. Written go from the owner (who, when, which host).
2. Independent backup first (issue #85; the service also writes `<db>.pre-migration-<UTC>.sqlite.bak` itself, same volume, not a substitute).
3. Copy the database to a safe place with the online API (never `cp` of a live WAL file):
   `sqlite3 grading.db ".backup /safe/copy.db"`
4. Rehearse on the copy: `python3 scripts/rehearse-migration.py --db /safe/copy.db`
   Must end with `REHEARSAL PASSED`. Attach the output to the rollout record. Any FAIL = stop.
5. Deploy; watch logs for `pre_migration_backup_written` and `ground_truth_backfilled`.
6. Verify: `PRAGMA integrity_check; PRAGMA foreign_key_check;` on the live file, one real `/submit` in a test account.
7. Rollback: stop the service, restore the `.pre-migration-*.bak` (or the #85 backup), redeploy the previous image.
8. Note for analysis: rows with `gt_backfilled=1` carry the ground truth from CURRENT cases (inferred, not proven).

## B. Changing a case's ground truth after students answered
Never `UPDATE cases` (a trigger rejects it). Create a new version:
```
python3 scripts/new-case-version.py --db /data/grading.db --stage test --order 0 --category 4B --reason "<why, ticket>"          # dry-run
python3 scripts/new-case-version.py --db /data/grading.db --stage test --order 0 --category 4B --reason "<why, ticket>" --apply
```
- Already-answered doctors keep their frozen ground truth and `/results`; new doctors get the new version.
- The script backs up first and runs in one transaction; the reason is printed as an AUDIT line (copy it into the change record until the audit log of #98 exists).
- Doctors currently mid-way on the old version: their next `/submit` for the old case id is answered per the PR #120 behaviour; check before running during a live session.
