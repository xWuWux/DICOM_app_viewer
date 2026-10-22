"""Test-only fault injection. Never copied into the production image."""

import asyncio
import os
import sqlite3
import time
from pathlib import Path

from app import db, main

marker = Path(os.environ["TEST_MARKER"])
original_init = db.init_db
original_advance = main._advance_progress
original_connect = db.get_connection
keeper = None


def pause():
    marker.touch()
    time.sleep(60)  # parent kills this process; bounded if the parent fails


def advance(conn, student_id, *args):
    if student_id == os.environ.get("TEST_PAUSE_STUDENT"):
        pause()  # submission INSERT executed; transaction has not committed
    return original_advance(conn, student_id, *args)


def connection():
    conn = original_connect()
    if os.environ.get("TEST_MIGRATION_PAUSE"):

        def trace(sql):
            if sql.strip().startswith(os.environ["TEST_MIGRATION_PAUSE"]):
                pause()  # cases_new populated, original not dropped, transaction open

        conn.set_trace_callback(trace)
    return conn


def init():
    global keeper
    if os.environ.get("TEST_REFUSE_INIT"):
        marker.touch()
        raise AssertionError("init_db must not run after failed integrity gate")
    original_init()
    if os.environ.get("TEST_KEEP_WAL"):
        keeper = sqlite3.connect(db.DB_PATH)
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("SELECT count(*) FROM cases").fetchone()


db.init_db = init
db.get_connection = connection
main._advance_progress = advance


async def app(scope, receive, send):
    if (
        scope["type"] == "http"
        and scope["path"] == "/submit"
        and (b"x-test-hold", b"1") in scope["headers"]
    ):
        marker.touch()
        while not marker.with_suffix(".release").exists():
            await asyncio.sleep(0.01)
    await main.app(scope, receive, send)
