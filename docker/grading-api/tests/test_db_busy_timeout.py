"""
Lock-contention regressions (issue #70).

WAL removes reader/writer collisions but writer/writer contention remains
(a cohort submitting within the same second). Two properties pinned here:

  (1) get_connection() explicitly advertises busy_timeout=5000 -- the
      guarantee must not silently ride on CPython's sqlite3.connect(
      timeout=5.0) default (invisible defaults are one refactor away
      from timeout=0 and instant student-facing 500s);
  (2) a real second writer WAITS for the first writer's commit instead
      of raising -- with a NEGATIVE CONTROL: an identical write against
      an identical lock, with the timeout lowered to 20ms, must raise
      promptly. Without the control, (2) could pass vacuously (e.g. if
      the "held" lock were not actually exclusive).

Timeline (explicit, no hope-based sleeps):
    t=0.00  holder: BEGIN IMMEDIATE + INSERT (uncommitted)
    t=0.00  positive writer (busy_timeout 5000) starts   -> must block
    t=0.15  control writer  (busy_timeout   20) starts   -> must raise ~20ms
    t=0.40  holder.commit()                              -> positive ends
asserts: positive elapsed in [0.3, 5.0); control raised OperationalError
with elapsed < 0.25. Loaded CI runners stretch only SLOWER, never faster,
so the lower bounds are the safe direction.
"""

import sqlite3
import threading
import time

from app import db as db_module


def _writer_result(slot, student_id, busy_timeout_ms=None):
    """Run one INSERT through the app's own get_connection(), timing it."""
    conn = db_module.get_connection()
    if busy_timeout_ms is not None:
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    started = time.monotonic()
    try:
        conn.execute(
            "INSERT INTO progress (student_id, stage, case_order_index,"
            " case_assigned_at) VALUES (?, 'learning', 0, 0.0)",
            (student_id,),
        )
        conn.commit()
        slot.update(ok=True, error=None)
    except sqlite3.OperationalError as exc:
        slot.update(ok=False, error=exc)
    slot["elapsed"] = time.monotonic() - started
    conn.close()


def test_get_connection_advertises_explicit_busy_timeout(client):
    # `client` fixture points db_module.DB_PATH at a fresh tmp file and
    # runs init_db; the read-back must show the EXPLICIT 5000.
    conn = db_module.get_connection()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


def test_second_writer_waits_for_the_first_commit(client):
    holder = db_module.get_connection()
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute(
            "INSERT INTO progress (student_id, stage, case_order_index,"
            " case_assigned_at) VALUES ('holder', 'learning', 0, 0.0)"
        )

        positive = {}
        control = {}
        t0 = time.monotonic()
        positive_thread = threading.Thread(
            target=_writer_result, args=(positive, "waiter_positive")
        )
        positive_thread.start()

        # give the positive writer time to actually reach the lock
        while time.monotonic() - t0 < 0.15:
            time.sleep(0.01)
        control_thread = threading.Thread(
            target=_writer_result,
            args=(control, "waiter_control", 20),
        )
        control_thread.start()
        control_thread.join(timeout=2)

        while time.monotonic() - t0 < 0.40:
            time.sleep(0.01)
        holder.execute("COMMIT")

        positive_thread.join(timeout=10)
        assert not positive_thread.is_alive(), "positive writer never finished"
    finally:
        holder.close()

    # the control proves the lock was real and exclusive: same lock, same
    # table, short timeout -> immediate OperationalError.
    assert control and control["ok"] is False
    assert isinstance(control["error"], sqlite3.OperationalError)
    assert "locked" in str(control["error"]).lower()
    assert control["elapsed"] < 0.25

    # the app default waited it out: started at ~0.0, commit at ~0.4.
    assert positive["ok"] is True, f"positive writer failed: {positive['error']!r}"
    assert positive["elapsed"] >= 0.3, positive["elapsed"]
    assert positive["elapsed"] < 5.0, positive["elapsed"]
