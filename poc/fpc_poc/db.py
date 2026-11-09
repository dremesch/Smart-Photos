"""SQLite schema for the POC. Deliberately flat; see spec v1.1 §14 for the real model."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events(name TEXT PRIMARY KEY, roots TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS photos(
  id INTEGER PRIMARY KEY, event TEXT, path TEXT, rel TEXT, ext TEXT, size INT, mtime REAL,
  cloud_state TEXT, status TEXT, sha256 TEXT, width INT, height INT, fmt TEXT,
  capture_ts TEXT, utc_offset TEXT, ts_source TEXT, device TEXT, has_gps INT, has_icc INT,
  orientation_rule TEXT, companion TEXT, error TEXT, UNIQUE(event, path));
CREATE TABLE IF NOT EXISTS features(photo_id INT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY, event TEXT, kind TEXT, ambiguous INT);
CREATE TABLE IF NOT EXISTS members(group_id INT, photo_id INT, rank INT, PRIMARY KEY(group_id, photo_id));
CREATE TABLE IF NOT EXISTS scores(photo_id INT PRIMARY KEY, score REAL, reasons TEXT, recommended INT, rec_order INT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, photo_id INT, action TEXT,
  source TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP, undoes INT);
CREATE TABLE IF NOT EXISTS crops(photo_id INT PRIMARY KEY, data TEXT, risk TEXT, manual INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS exports(photo_id INT, out_path TEXT, status TEXT, detail TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS timings(event TEXT, stage TEXT, seconds REAL, PRIMARY KEY(event, stage));
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, photo_id INT, stage TEXT, version TEXT,
  outcome TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS attempts_item ON attempts(stage, version, photo_id);
"""

# Attempt ledger (issue #11, S2-D8). A row is committed before each task is dispatched, with
# outcome NULL. The result handler sets the outcome in the same transaction as the result.
# A strike is a timeout, a crash, or a row still NULL from a parent that died mid-task.
STRIKE_OUTCOMES = ("timeout", "crashed")


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def review_state(con: sqlite3.Connection, event: str) -> dict[int, str]:
    """Project the append-only decision log into current state (approved/rejected/unreviewed)."""
    state: dict[int, str] = {}
    undone = {r[0] for r in con.execute("SELECT undoes FROM decisions WHERE undoes IS NOT NULL")}
    rows = con.execute(
        "SELECT d.id, d.photo_id, d.action FROM decisions d JOIN photos p ON p.id=d.photo_id "
        "WHERE p.event=? AND d.action IN ('approve','reject','clear') ORDER BY d.id",
        (event,),
    )  # reason tags ("reason:BLINK") and undo rows are events, not states
    for r in rows:
        if r["id"] in undone:
            continue
        state[r["photo_id"]] = r["action"]  # approve / reject / clear
    return {k: v for k, v in state.items() if v != "clear"}


def decide(con: sqlite3.Connection, photo_id: int, action: str, source: str = "user") -> int:
    cur = con.execute(
        "INSERT INTO decisions(photo_id, action, source) VALUES(?,?,?)", (photo_id, action, source)
    )
    con.commit()
    return cur.lastrowid


def undo_last(con: sqlite3.Connection, event: str) -> int | None:
    undone = {r[0] for r in con.execute("SELECT undoes FROM decisions WHERE undoes IS NOT NULL")}
    for r in con.execute(
        "SELECT d.id, d.photo_id FROM decisions d JOIN photos p ON p.id=d.photo_id "
        "WHERE p.event=? AND d.action!='undo' ORDER BY d.id DESC",
        (event,),
    ):
        if r["id"] not in undone:
            con.execute(
                "INSERT INTO decisions(photo_id, action, source, undoes) VALUES(?,?,?,?)",
                (r["photo_id"], "undo", "user", r["id"]),
            )
            con.commit()
            return r["photo_id"]
    return None


REASON_CODES = ("BLINK", "AWAY", "SOFT", "BG", "CROP", "BETTER", "OTHER", "MISSED")  # keys 1-8, S2-D3


def reasons(con: sqlite3.Connection, photo_id: int) -> list[str]:
    """Reason codes tagged on a photo, oldest first, minus any that were undone."""
    undone = {r[0] for r in con.execute("SELECT undoes FROM decisions WHERE undoes IS NOT NULL")}
    return [r["action"].split(":", 1)[1] for r in con.execute(
        "SELECT id, action FROM decisions WHERE photo_id=? AND action LIKE 'reason:%' ORDER BY id",
        (photo_id,)) if r["id"] not in undone]


def record_timing(con: sqlite3.Connection, event: str, stage: str, seconds: float) -> None:
    con.execute("INSERT OR REPLACE INTO timings(event, stage, seconds) VALUES(?,?,?)",
                (event, stage, round(seconds, 3)))
    con.commit()


def features(con: sqlite3.Connection, photo_id: int) -> dict:
    r = con.execute("SELECT data FROM features WHERE photo_id=?", (photo_id,)).fetchone()
    return json.loads(r[0]) if r else {}


def attempt_start(con: sqlite3.Connection, photo_id: int, stage: str, version: str) -> int:
    cur = con.execute("INSERT INTO attempts(photo_id, stage, version) VALUES(?,?,?)",
                      (photo_id, stage, version))
    con.commit()  # durable before dispatch: a parent killed mid-task still leaves the row
    return cur.lastrowid


def attempt_end(con: sqlite3.Connection, attempt_id: int, outcome: str) -> None:
    """Set the outcome. No commit: the caller commits it together with the item's result."""
    con.execute("UPDATE attempts SET outcome=? WHERE id=?", (outcome, attempt_id))


def strikes(con: sqlite3.Connection, stage: str, version: str, photo_id: int | None = None) -> dict[int, int]:
    q = ("SELECT photo_id, COUNT(*) FROM attempts WHERE stage=? AND version=? AND "
         f"(outcome IS NULL OR outcome IN ({','.join('?' * len(STRIKE_OUTCOMES))}))")
    args: list = [stage, version, *STRIKE_OUTCOMES]
    if photo_id is not None:
        q += " AND photo_id=?"
        args.append(photo_id)
    return {r[0]: r[1] for r in con.execute(q + " GROUP BY photo_id", args)}
