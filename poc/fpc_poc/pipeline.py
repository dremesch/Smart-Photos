"""Stage orchestration. Main process is the single DB writer; heavy work runs in workers.

Everything returned or printed from here is aggregate-only (issue #13, D6): counts and
timings, never paths, file names, capture times, hashes or exception messages. Source
details stay in the DB; the export folder carries output names and opaque IDs only (D7).
"""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

import numpy as np

from . import config, db, decode, features, grouping, inventory, ranking
from . import crop as cropmod
from .config import Paths
from .decode import run_isolated, sha256_file, task_ingest, worker_error
from .export import task_export


def log(msg: str) -> None:
    """Progress line. Callers pass counts only; no wall-clock time (it reads as a timestamp)."""
    print(f"[fpc] {msg}", flush=True)


def error_field(m: dict) -> str:
    """The value stored in photos.error: status, code and exception class. Never the message."""
    return f"{m.get('status')}:{m.get('error_code', 'E_UNKNOWN')}:{m.get('error_class', 'Unknown')}"


class _Ledger:
    """Attempt rows for one stage run (issue #11, S2-D8). Every item gets a committed
    attempt-started row before it is dispatched; its outcome is written with its result."""

    def __init__(self, con: sqlite3.Connection, stage: str, version: str):
        self.con, self.stage, self.version = con, stage, version
        self.started: list[int] = []
        self.open: dict[int, int] = {}

    def quarantined(self) -> set[int]:
        """Items with two strikes under the current version: skipped, never dispatched."""
        return {pid for pid, n in db.strikes(self.con, self.stage, self.version).items()
                if n >= config.STRIKES_TO_QUARANTINE}

    def start(self, pid: int) -> None:
        aid = db.attempt_start(self.con, pid, self.stage, self.version)
        self.started.append(aid)
        self.open[pid] = aid

    def end(self, pid: int, res):
        """Record the outcome (uncommitted). A second strike turns the result into quarantined."""
        outcome = res.get("status", "ok") if isinstance(res, dict) else "ok"
        db.attempt_end(self.con, self.open.pop(pid), outcome)
        if outcome in db.STRIKE_OUTCOMES and db.strikes(self.con, self.stage, self.version, pid).get(
                pid, 0) >= config.STRIKES_TO_QUARANTINE:
            return {**res, "status": "quarantined", "error_code": "E_QUARANTINED"}
        return res

    def run(self, fn, jobs, workers, on_result, deadline: float) -> dict:
        try:
            return run_isolated(fn, jobs, workers, on_result, on_start=self.start, deadline=deadline)
        except KeyboardInterrupt:
            # A graceful Ctrl+C is not the items' fault: close their attempts without a strike.
            # A hard kill or power loss never gets here, so those rows stay open and count.
            self.con.rollback()
            if self.started:
                self.con.execute(
                    f"UPDATE attempts SET outcome='interrupted' WHERE outcome IS NULL AND id IN "
                    f"({','.join('?' * len(self.started))})", self.started)
                self.con.commit()
            raise


# ---------------------------------------------------------------- 1. inventory pass 1


def scan(con: sqlite3.Connection, event: str, roots: list[Path]) -> Counter:
    t = time.time()
    con.execute("INSERT OR REPLACE INTO events(name, roots) VALUES(?,?)",
                (event, json.dumps([str(r.resolve()) for r in roots])))
    entries = inventory.scan(roots)
    seen = set()
    c: Counter = Counter()
    for e in entries:
        seen.add(e.path)
        if e.kind != "photo":
            status = "excluded"
        elif not inventory.is_openable(e.cloud_state):
            status = "unavailable"
        else:
            status = "discovered"
        c[f"{e.kind}:{e.cloud_state}"] += 1
        old = con.execute("SELECT id, size, mtime, status FROM photos WHERE event=? AND path=?",
                          (event, e.path)).fetchone()
        if old is None:
            con.execute(
                "INSERT INTO photos(event,path,rel,ext,size,mtime,cloud_state,status,companion) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (event, e.path, e.rel, e.ext, e.size, e.mtime, e.cloud_state, status, e.companion))
        else:
            changed = old["size"] != e.size or old["mtime"] != e.mtime
            keep = old["status"] in KEEP_STATUSES and not changed
            con.execute(
                "UPDATE photos SET rel=?, size=?, mtime=?, cloud_state=?, status=?, companion=? WHERE id=?",
                (e.rel, e.size, e.mtime, e.cloud_state, old["status"] if keep else status, e.companion,
                 old["id"]))  # rel refreshed so DBs written before the POSIX fix converge
    for r in con.execute("SELECT id, path FROM photos WHERE event=?", (event,)).fetchall():
        if r["path"] not in seen:
            con.execute("UPDATE photos SET status='missing' WHERE id=?", (r["id"],))
            c["missing"] += 1
    con.commit()
    db.record_timing(con, event, "scan", time.time() - t)
    return c


# ---------------------------------------------------------------- 2. ingest (pass 2) + decode


# Statuses a rescan keeps for an unchanged file, and the ones ingest (re)tries. A timeout or
# crash is one strike and is retried; two strikes means quarantined, which is retried only
# under a new decoder version (issue #11).
KEEP_STATUSES = ("ok", "import_error", "timeout", "crashed", "quarantined")
INGEST_STATUSES = ("discovered", "unavailable", "timeout", "crashed", "quarantined")


def ingest(con: sqlite3.Connection, paths: Paths, event: str, workers: int | None = None) -> Counter:
    rows = con.execute(
        f"SELECT id, path, status FROM photos WHERE event=? AND status IN "
        f"({','.join('?' * len(INGEST_STATUSES))})", (event, *INGEST_STATUSES)).fetchall()
    ledger = _Ledger(con, "ingest", decode.DECODER_VERSION)
    quarantined = ledger.quarantined()
    c: Counter = Counter()
    jobs = []
    for r in rows:  # cheap attribute re-check before handing to workers
        if r["id"] in quarantined:
            c["quarantined"] += 1
            if r["status"] != "quarantined":  # e.g. a strike left by a parent that was killed
                con.execute("UPDATE photos SET status='quarantined', error=? WHERE id=?",
                            (error_field(worker_error("TwoStrikes", "E_QUARANTINED")), r["id"]))
            continue
        st = inventory.current_state(r["path"])
        if inventory.is_openable(st):
            jobs.append((r["id"], (r["path"], str(paths.root / "cache"))))
        else:
            con.execute("UPDATE photos SET status='unavailable', cloud_state=? WHERE id=?", (st, r["id"]))
    con.commit()
    n = len(jobs)
    done = 0

    def on_result(pid: int, m: dict) -> None:
        nonlocal done
        m = ledger.end(pid, m)
        st = m.get("status")
        c[st] += 1
        c["cache_hit"] += bool(m.get("cache_hit"))
        if st == "ok":
            con.execute(
                "UPDATE photos SET status='ok', sha256=?, width=?, height=?, fmt=?, capture_ts=?, "
                "utc_offset=?, ts_source=?, device=?, has_gps=?, has_icc=?, orientation_rule=?, error=NULL "
                "WHERE id=?",
                (m["sha256"], m["width"], m["height"], m["fmt"], m["capture_ts"], m["utc_offset"],
                 m["ts_source"], m["device"], int(m["has_gps"]), int(m["has_icc"]),
                 m["orientation_rule"], pid))
        elif st == "unavailable":
            con.execute("UPDATE photos SET status='unavailable', cloud_state=? WHERE id=?",
                        (m.get("cloud_state"), pid))
        elif st in ("timeout", "crashed", "quarantined"):
            con.execute("UPDATE photos SET status=?, error=? WHERE id=?", (st, error_field(m), pid))
        else:
            if m.get("error_code", "E_WORKER") == "E_WORKER":  # generic code only: the ingest
                m = {**m, "error_code": "E_DECODE"}  # task is the decoder; keep specific codes
            con.execute("UPDATE photos SET status='import_error', error=? WHERE id=?",
                        (error_field(m), pid))
        con.commit()  # result and attempt outcome in one transaction = resumable
        done += 1
        if done % 25 == 0 or done == n:
            log(f"ingest {done}/{n}")

    t = time.time()
    ledger.run(task_ingest, jobs, workers, on_result, config.DECODE_DEADLINE_S)
    c["seconds"] = round(time.time() - t, 2)
    db.record_timing(con, event, "ingest", time.time() - t)
    return c


# ---------------------------------------------------------------- 3. features


def analyze(con: sqlite3.Connection, paths: Paths, event: str, workers: int | None = None) -> Counter:
    rows = con.execute(
        "SELECT p.id, p.sha256 FROM photos p LEFT JOIN features f ON f.photo_id=p.id "
        "WHERE p.event=? AND p.status='ok' AND f.photo_id IS NULL", (event,)).fetchall()
    ledger = _Ledger(con, "analyze", features.VERSION)
    quarantined = ledger.quarantined()
    jobs = [(r["id"], (str(paths.deriv / f"{r['sha256']}.jpg"),)) for r in rows
            if r["id"] not in quarantined]
    c: Counter = Counter()
    if len(jobs) < len(rows):
        c["quarantined"] = len(rows) - len(jobs)

    def on_result(pid: int, f: dict) -> None:
        f = ledger.end(pid, f)
        if "phash" in f:
            con.execute("INSERT OR REPLACE INTO features(photo_id, data) VALUES(?,?)", (pid, json.dumps(f)))
            c["ok"] += 1
        else:
            c[f.get("status", "error")] += 1
        con.commit()

    t = time.time()
    ledger.run(features.compute, jobs, workers, on_result, config.FEATURE_DEADLINE_S)
    c["seconds"] = round(time.time() - t, 2)
    db.record_timing(con, event, "analyze", time.time() - t)
    return c


# ---------------------------------------------------------------- 4. group, rank, recommend


def group_and_rank(con: sqlite3.Connection, event: str, target: int | None = None) -> dict:
    t0 = time.time()
    rows = con.execute(
        "SELECT p.*, f.data FROM photos p JOIN features f ON f.photo_id=p.id "
        "WHERE p.event=? AND p.status='ok'", (event,)).fetchall()
    import imagehash

    feats = {r["id"]: json.loads(r["data"]) for r in rows}
    ps = [grouping.P(
        id=r["id"], sha=r["sha256"], ts=grouping.parse_ts(r["capture_ts"]), device=r["device"],
        phash=imagehash.hex_to_hash(feats[r["id"]]["phash"]),
        emb=ranking.as_array(feats[r["id"]]["emb"]), portrait=r["height"] > r["width"],
    ) for r in rows]
    groups = grouping.build(ps)

    con.execute("DELETE FROM members WHERE group_id IN (SELECT id FROM groups WHERE event=?)", (event,))
    con.execute("DELETE FROM groups WHERE event=?", (event,))
    scores = {}
    for r in rows:
        scores[r["id"]] = ranking.score(feats[r["id"]], r["width"], r["height"],
                                        bool(r["capture_ts"]), bool(r["device"]))
    group_of: dict[int, int] = {}
    non_top: set[int] = set()
    for g in groups:
        ranked = sorted(g.members, key=lambda i: -scores[i][0])
        amb = len(ranked) > 1 and abs(scores[ranked[0]][0] - scores[ranked[1]][0]) < 0.03
        gid = con.execute("INSERT INTO groups(event, kind, ambiguous) VALUES(?,?,?)",
                          (event, g.kind, int(amb))).lastrowid
        for k, i in enumerate(ranked):
            con.execute("INSERT INTO members VALUES(?,?,?)", (gid, i, k))
            group_of[i] = gid
            if k:
                non_top.add(i)

    state = db.review_state(con, event)
    approved = {i for i, s in state.items() if s == "approve"}
    rejected = {i for i, s in state.items() if s == "reject"}
    cands = [{
        "id": r["id"], "score": scores[r["id"]][0], "emb": ranking.as_array(feats[r["id"]]["emb"]),
        "day": (r["capture_ts"] or "unknown")[:10], "group": group_of.get(r["id"]),
        "people": bool(feats[r["id"]]["faces"]),
    } for r in rows if r["id"] not in non_top or r["id"] in approved]
    target = target or max(10, round(len(rows) * 0.2))
    rec = ranking.recommend(cands, target, approved, rejected)
    order = {pid: k for k, pid in enumerate(rec)}
    con.execute("DELETE FROM scores WHERE photo_id IN (SELECT id FROM photos WHERE event=?)", (event,))
    for pid, (s, reasons) in scores.items():
        con.execute("INSERT INTO scores VALUES(?,?,?,?,?)",
                    (pid, s, json.dumps(reasons), int(pid in order), order.get(pid)))
    con.commit()
    db.record_timing(con, event, "rank", time.time() - t0)
    kinds = Counter(g.kind for g in groups)
    return {"photos": len(rows), "groups": dict(kinds), "grouped_alternatives": len(non_top),
            "recommended": len(rec), "target": target}


# ---------------------------------------------------------------- 5. crops


def propose_crops(con: sqlite3.Connection, event: str) -> Counter:
    t = time.time()
    approved = [i for i, s in db.review_state(con, event).items() if s == "approve"]
    c: Counter = Counter()
    for pid in approved:
        r = con.execute("SELECT width, height FROM photos WHERE id=?", (pid,)).fetchone()
        ex = con.execute("SELECT manual FROM crops WHERE photo_id=?", (pid,)).fetchone()
        if ex and ex["manual"]:
            c["manual_kept"] += 1
            continue
        f = db.features(con, pid)
        cp = cropmod.propose(r["width"], r["height"], f["faces"], f["pets"], tuple(f["saliency"]))
        con.execute("INSERT OR REPLACE INTO crops(photo_id, data, risk, manual) VALUES(?,?,?,0)",
                    (pid, json.dumps(cp.to_dict()), cp.risk))
        c[cp.risk] += 1
    con.commit()
    db.record_timing(con, event, "crops", time.time() - t)
    return c


# ---------------------------------------------------------------- 6. export


def _onedrive_roots() -> list[Path]:
    keys = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
    return [Path(os.environ[k]).resolve() for k in keys if os.environ.get(k)]


def _under(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


CROP_MANIFEST_KEYS = ("target", "mode", "oriented_size", "px", "norm", "risk")
ITEM_KEYS = ("status", "verified", "problems", "warnings", "size")


def export(con: sqlite3.Connection, event: str, out_root: Path, workers: int | None = None,
           allow_onedrive: bool = False) -> dict:
    """Write print-ready files plus manifests. Returns counts only, not the folder path."""
    t = time.time()
    out_root = out_root.resolve()
    roots = [Path(r) for r in json.loads(con.execute("SELECT roots FROM events WHERE name=?",
                                                     (event,)).fetchone()[0])]
    prefix = (re.sub(r"[^A-Za-z0-9]", "", event).upper() or "EVENT")[:8]
    base = out_root / re.sub(r"[^\w\-]", "_", event)
    # Any overlap, either direction: the export must never write or delete inside a source.
    if any(_under(out_root, r) or _under(r, out_root) or _under(r, base) for r in roots):
        raise SystemExit("refused: the export folder overlaps a source folder")
    if not allow_onedrive and any(_under(out_root, r) for r in _onedrive_roots()):
        raise SystemExit("export folder is inside OneDrive; pass --allow-onedrive to confirm")

    approved = [i for i, s in db.review_state(con, event).items() if s == "approve"]
    rows = con.execute(
        f"SELECT p.id, p.path, p.sha256, p.capture_ts, c.data FROM photos p JOIN crops c ON c.photo_id=p.id "
        f"WHERE p.id IN ({','.join('?' * len(approved))}) ORDER BY p.capture_ts, p.path", approved
    ).fetchall() if approved else []
    (base / "print_ready").mkdir(parents=True, exist_ok=True)
    (base / "manifests").mkdir(exist_ok=True)
    (base / "reports").mkdir(exist_ok=True)
    # An interrupted export can leave partial .tmp files. Remove only names this export
    # writes ({prefix}_NNNN.tmp in print_ready), nothing else.
    tmp_removed = 0
    own_tmp = re.compile(rf"{re.escape(prefix)}_\d{{4}}\.tmp")
    for tmp in (base / "print_ready").glob("*.tmp"):
        if own_tmp.fullmatch(tmp.name) and tmp.is_file():
            tmp.unlink()
            tmp_removed += 1
    ledger = _Ledger(con, "export", decode.DECODER_VERSION)
    quarantined = ledger.quarantined()
    results: dict[int, dict] = {}

    def record(pid: int, res: dict) -> None:  # full detail, including the source link, stays in the DB
        results[pid] = res
        con.execute("INSERT INTO exports(photo_id, out_path, status, detail) VALUES(?,?,?,?)",
                    (pid, str(names[pid]), res.get("status"), json.dumps(res)))
        con.commit()  # result and attempt outcome in one transaction

    jobs, names = [], {}
    for k, r in enumerate(rows, 1):
        out = base / "print_ready" / f"{prefix}_{k:04d}.jpg"
        names[r["id"]] = out
        if r["id"] in quarantined:
            record(r["id"], worker_error("TwoStrikes", "E_QUARANTINED"))
        else:
            jobs.append((r["id"], (r["path"], json.loads(r["data"]), str(out), r["sha256"])))
    ledger.run(task_export, jobs, workers, lambda pid, res: record(pid, ledger.end(pid, res)),
               config.EXPORT_DEADLINE_S)
    # D7: the export folder goes to the print lab. Output names and opaque IDs only:
    # no source path, file name, SHA-256 or capture time.
    with open(base / "manifests" / "selection.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["output", "photo_id", "status", "verified"])
        for r in rows:
            res = results[r["id"]]
            w.writerow([names[r["id"]].name, r["id"], res.get("status"), res.get("verified")])
    crops = {}
    for r in rows:
        c = json.loads(r["data"])
        crops[str(r["id"])] = {"output": names[r["id"]].name, **{k: c[k] for k in CROP_MANIFEST_KEYS}}
    (base / "manifests" / "crop_decisions.json").write_text(json.dumps(crops, indent=1))
    summary = Counter(r.get("status") for r in results.values())
    summary["verified"] = sum(bool(r.get("verified")) for r in results.values())
    summary["warnings"] = sum(len(r.get("warnings", [])) for r in results.values())
    items = {str(pid): {"output": names[pid].name, **{k: res[k] for k in ITEM_KEYS if k in res}}
             for pid, res in results.items()}
    (base / "reports" / "export_summary.json").write_text(json.dumps(
        {"summary": summary, "items": items}, indent=1))
    db.record_timing(con, event, "export", time.time() - t)
    return {**summary, "tmp_removed": tmp_removed}


# ---------------------------------------------------------------- integrity


def integrity(con: sqlite3.Connection, event: str, report_dir: Path | None = None) -> dict:
    """Re-hash originals. Returns counts; the list of changed items goes to report_dir
    (inside the data directory, which agents are denied), never to the console."""
    t = time.time()
    rows = con.execute("SELECT path, sha256 FROM photos WHERE event=? AND status='ok'", (event,)).fetchall()
    changed = [r["path"] for r in rows if not os.path.exists(r["path"]) or sha256_file(r["path"]) != r["sha256"]]
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "integrity_changed.json").write_text(json.dumps(changed, indent=1))
    db.record_timing(con, event, "integrity", time.time() - t)
    return {"checked": len(rows), "changed": len(changed)}


# ---------------------------------------------------------------- summary (aggregate-only)

PHOTO_STATUSES = ("discovered", "excluded", "unavailable", "ok", "import_error", "missing",
                  "timeout", "crashed", "quarantined")
ERROR_CODES = ("E_DECODE", "E_CRASH", "E_SOURCE_CHANGED", "E_WORKER", "E_TIMEOUT", "E_QUARANTINED")
GROUP_KINDS = ("exact", "near", "burst")
CROP_RISKS = ("likely_safe", "needs_review", "crop_conflict")
EXPORT_STATUSES = ("done", "skipped_existing", "error", "unavailable", "crashed", "timeout",
                   "quarantined")
STAGES = ("scan", "ingest", "analyze", "rank", "crops", "export", "integrity")
# ranking.score reasons are free text; map them onto a closed set of codes
REASON_CODES = (("face(s)", "FACES"), ("soft focus", "SOFT"), ("exposure", "EXPOSURE"),
                ("low resolution", "LOW_RES"), ("incomplete metadata", "INCOMPLETE_METADATA"),
                ("pet", "PET"))


def _closed(counts: Counter, vocab: tuple[str, ...], other: str = "other") -> dict:
    """Keep known keys; fold anything else into `other`, so keys come from a fixed allowlist."""
    out = {k: int(counts.get(k, 0)) for k in vocab}
    extra = sum(v for k, v in counts.items() if k not in vocab)
    if extra:
        out[other] = int(extra)
    return out


def _reason_code(text: str) -> str:
    for needle, code in REASON_CODES:
        if needle in text:
            return code
    return "OTHER"


def summary(con: sqlite3.Connection, event: str) -> dict:
    """One aggregate JSON for agents: counts and timings only, every key from an allowlist."""
    photos = Counter(r[0] for r in con.execute("SELECT status FROM photos WHERE event=?", (event,)))
    errors = Counter((r[0] or "").split(":")[1] if (r[0] or "").count(":") >= 2 else "other"
                     for r in con.execute("SELECT error FROM photos WHERE event=? AND error IS NOT NULL",
                                          (event,)))
    groups = Counter(r[0] for r in con.execute("SELECT kind FROM groups WHERE event=?", (event,)))
    ambiguous = con.execute("SELECT COUNT(*) FROM groups WHERE event=? AND ambiguous=1",
                            (event,)).fetchone()[0]
    score_rows = con.execute("SELECT s.reasons, s.recommended FROM scores s JOIN photos p "
                             "ON p.id=s.photo_id WHERE p.event=?", (event,)).fetchall()
    reasons = Counter(_reason_code(t) for r in score_rows for t in json.loads(r[0] or "[]"))
    state = Counter(db.review_state(con, event).values())
    log_rows = Counter(r[0] for r in con.execute(
        "SELECT d.action FROM decisions d JOIN photos p ON p.id=d.photo_id WHERE p.event=?", (event,)))
    crops = con.execute("SELECT c.risk, c.manual FROM crops c JOIN photos p ON p.id=c.photo_id "
                        "WHERE p.event=?", (event,)).fetchall()
    risk = Counter(r[0] for r in crops)
    exp_rows = con.execute(
        "SELECT e.status, e.detail FROM exports e JOIN photos p ON p.id=e.photo_id WHERE p.event=? "
        "AND e.rowid IN (SELECT MAX(rowid) FROM exports GROUP BY photo_id)", (event,)).fetchall()
    exports = Counter(r[0] for r in exp_rows)
    details = [json.loads(r[1] or "{}") for r in exp_rows]
    timings = {f"{r[0]}_s": float(r[1]) for r in con.execute(
        "SELECT stage, seconds FROM timings WHERE event=?", (event,)) if r[0] in STAGES}
    return {
        "photos": {"total": int(sum(photos.values())), **_closed(photos, PHOTO_STATUSES)},
        "errors": _closed(errors, ERROR_CODES),
        "groups": {**_closed(groups, GROUP_KINDS), "ambiguous": int(ambiguous)},
        "ranking": {"scored": len(score_rows), "recommended": sum(int(r[1]) for r in score_rows)},
        "reason_codes": _closed(reasons, tuple(c for _, c in REASON_CODES), other="OTHER"),
        "decisions": {"approve": int(state.get("approve", 0)), "reject": int(state.get("reject", 0)),
                      "log_rows": int(sum(log_rows.values())), "undo": int(log_rows.get("undo", 0))},
        "crop_risk": {**_closed(risk, CROP_RISKS), "manual": sum(int(r[1] or 0) for r in crops)},
        "exports": {**_closed(exports, EXPORT_STATUSES),
                    "verified": sum(bool(d.get("verified")) for d in details),
                    "warnings": sum(len(d.get("warnings", [])) for d in details)},
        "timings": timings,
    }


def emb_cos(a: list[float], b: list[float]) -> float:
    return float(np.dot(a, b))
