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
"""


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
        "WHERE p.event=? AND d.action!='undo' ORDER BY d.id",
        (event,),
    )
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


def features(con: sqlite3.Connection, photo_id: int) -> dict:
    r = con.execute("SELECT data FROM features WHERE photo_id=?", (photo_id,)).fetchone()
    return json.loads(r[0]) if r else {}
