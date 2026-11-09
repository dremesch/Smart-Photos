"""Decoder pool: deadlines, kill, respawn and two-strikes quarantine (issue #11, S2-D8, D4).

Pool-level tests drive ``run_isolated`` with stdlib callables only (``eval`` of a short
expression that returns, sleeps or aborts), so product code carries no test seam (D5).
Pipeline-level tests use the POC's file-name markers on synthetic JPEGs, which D5 allows
for the throwaway POC. Deadlines are shortened so the whole module runs in seconds.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC))

from fpc_poc import config, db, decode, pipeline  # noqa: E402
from fpc_poc.config import Paths  # noqa: E402
from fpc_poc.decode import run_isolated, worker_error  # noqa: E402

HANG = "__import__('time').sleep(3600)"
CRASH = "__import__('os').abort()"
DEADLINE, GRACE = 0.5, 0.5
ERROR_KEYS = {"status", "error_class", "error_code"}


def _children() -> int:
    return len(multiprocessing.active_children())


# ---------------------------------------------------------------- the pool


def test_worker_error_is_class_and_code_only():
    e = worker_error(OSError("cannot open /home/someone/IMG_0001.JPG"))
    assert e == {"status": "error", "error_class": "OSError", "error_code": "E_WORKER"}
    assert worker_error(ValueError("x"), "E_DECODE")["error_code"] == "E_DECODE"
    t = worker_error("DeadlineExceeded", "E_TIMEOUT")
    assert t == {"status": "timeout", "error_class": "DeadlineExceeded", "error_code": "E_TIMEOUT"}
    assert worker_error("WorkerDied", "E_CRASH")["status"] == "crashed"
    assert worker_error("TwoStrikes", "E_QUARANTINED")["status"] == "quarantined"


def test_hang_is_killed_after_deadline_plus_grace_and_worker_respawned():
    jobs = [("hang", (HANG,))] + [(i, (f"{i} * 2",)) for i in range(4)]
    started, finished, live = {}, {}, []

    def on_start(k):
        started[k] = time.monotonic()

    def on_result(k, r):
        finished[k] = time.monotonic()
        live.append(_children())

    res = run_isolated(eval, jobs, workers=1, on_result=on_result, on_start=on_start,
                       deadline=DEADLINE, grace=GRACE)
    assert res["hang"] == {"status": "timeout", "error_class": "DeadlineExceeded",
                           "error_code": "E_TIMEOUT"}
    took = finished["hang"] - started["hang"]
    assert DEADLINE + GRACE <= took < DEADLINE + GRACE + 2.0, took
    # one worker: the rest can only complete on a respawned worker
    assert [res[i] for i in range(4)] == [0, 2, 4, 6]
    assert max(live) <= 1  # the killed worker is reaped before its replacement starts
    assert _children() == 0


def test_hang_does_not_block_other_workers():
    jobs = [("hang", (HANG,))] + [(i, (f"{i}",)) for i in range(20)]
    order = []
    res = run_isolated(eval, jobs, workers=2, on_result=lambda k, r: order.append(k),
                       deadline=DEADLINE, grace=GRACE)
    assert res["hang"]["status"] == "timeout"
    assert all(res[i] == i for i in range(20))
    assert order[-1] == "hang"  # the others finished on the second worker meanwhile


def test_result_inside_grace_is_accepted():
    res = run_isolated(eval, [(1, ("__import__('time').sleep(0.7) or 'late'",))], workers=1,
                       deadline=0.3, grace=1.5)
    assert res[1] == "late"


def test_hard_crash_is_isolated():
    jobs = [(1, ("1",)), (2, (CRASH,)), (3, ("3",)), (4, (CRASH,)), (5, ("5",))]
    res = run_isolated(eval, jobs, workers=2, deadline=5, grace=1)
    assert res[2] == res[4] == {"status": "crashed", "error_class": "WorkerDied",
                                "error_code": "E_CRASH"}
    assert (res[1], res[3], res[5]) == (1, 3, 5)
    assert _children() == 0


def test_exception_in_task_is_class_and_code_only():
    res = run_isolated(eval, [(1, ("open('/nonexistent/CANARY_7Q3/IMG.JPG')",))], workers=1)
    assert res[1] == {"status": "error", "error_class": "FileNotFoundError", "error_code": "E_WORKER"}


def test_on_start_runs_before_dispatch_once_per_item():
    calls = []
    res = run_isolated(eval, [(k, (f"{k}",)) for k in range(6)], workers=2,
                       on_start=lambda k: calls.append(("start", k)),
                       on_result=lambda k, r: calls.append(("done", k)))
    assert len(res) == 6
    for k in range(6):
        assert calls.count(("start", k)) == 1 and calls.count(("done", k)) == 1
        assert calls.index(("start", k)) < calls.index(("done", k))


def test_on_result_raising_propagates_and_is_not_called_again():
    seen = []

    class Boom(Exception):
        pass

    def on_result(k, r):
        seen.append(k)
        if k == 2:
            raise Boom

    with pytest.raises(Boom):
        run_isolated(eval, [(k, (f"{k}",)) for k in range(5)], workers=1, on_result=on_result)
    assert seen.count(2) == 1  # not re-recorded as E_WORKER and handed back to on_result
    assert _children() == 0  # workers are cleaned up on the way out


def test_no_jobs_starts_no_workers():
    assert run_isolated(eval, [], workers=4) == {}
    assert _children() == 0


def test_throughput_no_regression_vs_process_pool_executor():
    """Many tiny tasks: the per-task overhead of the new pool stays close to the stdlib pool."""
    jobs = [(k, (f"{k} + 1",)) for k in range(300)]
    t = time.perf_counter()
    res = run_isolated(eval, jobs, workers=2)
    ours = time.perf_counter() - t
    assert all(res[k] == k + 1 for k in range(300))
    t = time.perf_counter()
    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as ex:
        ref = list(ex.map(eval, [a[0] for _, a in jobs]))
    base = time.perf_counter() - t
    assert ref[-1] == 300
    assert ours <= base * 1.5 + 0.5, (round(ours, 2), round(base, 2))


# ---------------------------------------------------------------- the pipeline ledger


def _jpegs(folder: Path, names: list[str]) -> None:
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    for k, n in enumerate(names):  # synthetic, no metadata
        Image.new("RGB", (320, 240), (40 * k % 255, 120, 200)).save(folder / n, quality=85)


NAMES = ["IMG_0001.JPG", "IMG_0002.JPG", "IMG_0003.JPG", "IMG_0004_HANGME.JPG", "IMG_0005_CRASHME.JPG"]


def _status(con, name: str) -> tuple[str, str | None]:
    r = con.execute("SELECT status, error FROM photos WHERE rel LIKE ?", (f"%{name}%",)).fetchone()
    return r[0], r[1]


def _attempts(con, name: str) -> list:
    return [r[0] for r in con.execute(
        "SELECT a.outcome FROM attempts a JOIN photos p ON p.id=a.photo_id "
        "WHERE a.stage='ingest' AND p.rel LIKE ? ORDER BY a.id", (f"%{name}%",))]


@pytest.fixture
def small_event(tmp_path, monkeypatch, capsys):
    ev = tmp_path / "event"
    _jpegs(ev, NAMES)
    monkeypatch.setattr(config, "DECODE_DEADLINE_S", DEADLINE)
    monkeypatch.setattr(config, "KILL_GRACE_S", GRACE)
    monkeypatch.setenv("FPC_POC_HANG_MARKER", "HANGME")
    monkeypatch.setenv("FPC_POC_CRASH_MARKER", "CRASHME")
    paths = Paths(tmp_path / "data")
    con = db.connect(paths.db)

    def run():
        pipeline.scan(con, "T", [ev])
        c = pipeline.ingest(con, paths, "T", 2)
        capsys.readouterr()  # progress lines
        return c

    return con, run, monkeypatch


def test_two_strikes_quarantine_and_skip_until_decoder_version_changes(small_event):
    con, run, mp = small_event
    c1 = run()
    assert c1["ok"] == 3 and c1["timeout"] == 1 and c1["crashed"] == 1
    assert _status(con, "HANGME") == ("timeout", "timeout:E_TIMEOUT:DeadlineExceeded")
    assert _status(con, "CRASHME") == ("crashed", "crashed:E_CRASH:WorkerDied")

    c2 = run()  # one strike each: retried, and the second strike quarantines
    assert c2["quarantined"] == 2 and c2["ok"] == 0  # ok items are not re-run
    for name in ("HANGME", "CRASHME"):
        st, err = _status(con, name)
        assert st == "quarantined" and err.startswith("quarantined:E_QUARANTINED:")
    assert _attempts(con, "HANGME") == ["timeout", "timeout"]
    assert _attempts(con, "CRASHME") == ["crashed", "crashed"]
    assert _attempts(con, "IMG_0001.JPG") == ["ok"]

    c3 = run()  # quarantined: skipped, never dispatched
    assert c3["quarantined"] == 2
    assert len(_attempts(con, "HANGME")) == 2 and len(_attempts(con, "CRASHME")) == 2
    s = pipeline.summary(con, "T")
    assert s["photos"]["quarantined"] == 2 and s["errors"]["E_QUARANTINED"] == 2

    mp.setattr(decode, "DECODER_VERSION", decode.DECODER_VERSION + "-next")
    mp.delenv("FPC_POC_HANG_MARKER")
    mp.delenv("FPC_POC_CRASH_MARKER")
    c4 = run()  # a new decoder version gets a fresh chance
    assert c4["ok"] == 2
    assert _status(con, "HANGME") == ("ok", None) and _status(con, "CRASHME") == ("ok", None)


def test_summary_and_status_count_timeouts_with_codes_only(small_event, capsys):
    from fpc_poc.__main__ import main

    import socket

    con, run, mp = small_event
    for name in ("socket", "create_connection", "getaddrinfo"):  # main() installs the guard;
        mp.setattr(socket, name, getattr(socket, name))  # restore it for later test modules
    run()
    s = pipeline.summary(con, "T")
    assert s["photos"]["timeout"] == 1 and s["photos"]["crashed"] == 1
    assert s["errors"]["E_TIMEOUT"] == 1 and s["errors"]["E_CRASH"] == 1
    assert "other" not in s["errors"]
    data = Path(con.execute("PRAGMA database_list").fetchone()[2]).parent
    assert main(["--data", str(data), "status", "--event", "T"]) == 0
    out = capsys.readouterr().out
    assert '"timeout": 1' in out and "HANGME" not in out


@pytest.mark.skipif(sys.platform == "win32", reason="delivers SIGINT with os.kill")
def test_graceful_interrupt_is_not_a_strike(small_event):
    con, run, mp = small_event
    mp.setattr(config, "DECODE_DEADLINE_S", 60)
    mp.delenv("FPC_POC_CRASH_MARKER")
    timer = threading.Timer(3.0, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            run()
    finally:
        timer.cancel()
    assert _attempts(con, "HANGME") == ["interrupted"]
    assert multiprocessing.active_children() == []
    mp.setattr(config, "DECODE_DEADLINE_S", DEADLINE)
    run()
    assert _status(con, "HANGME")[0] == "timeout"  # one strike, not two


# ---------------------------------------------------------------- the parent dies mid-task

PARENT = (
    "import sys\n"
    "from pathlib import Path\n"
    "from fpc_poc import db, decode, pipeline\n"
    "from fpc_poc.config import Paths\n"
    "ev, data, version = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]\n"
    "decode.DECODER_VERSION = version\n"
    "paths = Paths(data)\n"
    "con = db.connect(paths.db)\n"
    "pipeline.scan(con, 'T', [ev])\n"
    "pipeline.ingest(con, paths, 'T', 2)\n"
)


def _parent(ev: Path, data: Path, version: str, deadline: float, **kw) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(POC) + os.pathsep + env.get("PYTHONPATH", "")
    env["FPC_POC_HANG_MARKER"] = "HANGME"
    env["FPC_POC_DECODE_DEADLINE_S"] = str(deadline)
    env["FPC_POC_KILL_GRACE_S"] = str(GRACE)
    return subprocess.Popen([sys.executable, "-c", PARENT, str(ev), str(data), version], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def _query(path: Path, sql: str) -> list:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()
    except sqlite3.OperationalError:
        return []  # not created yet


def test_parent_killed_mid_task_still_counts_a_strike(tmp_path):
    ev, data = tmp_path / "event", tmp_path / "data"
    _jpegs(ev, [n for n in NAMES if "CRASHME" not in n])
    dbp = data / "poc.db"
    kw = {"start_new_session": True} if sys.platform != "win32" else {}
    p = _parent(ev, data, "v1", deadline=600, **kw)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:  # wait: the hanger is in flight, the rest are done
            rows = _query(dbp, "SELECT p.rel, a.outcome FROM attempts a JOIN photos p ON p.id=a.photo_id")
            if (len(rows) == 4 and sum(o == "ok" for _, o in rows) == 3
                    and any("HANGME" in r and o is None for r, o in rows)):
                break
            time.sleep(0.1)
        else:
            pytest.fail("the hanging item never went in flight")
        p.kill()  # TerminateProcess on Windows: no cleanup runs in the parent
        p.wait(timeout=30)
        if sys.platform != "win32":  # the orphaned, hung worker exits with its parent
            gone = time.monotonic() + 15
            while time.monotonic() < gone:
                try:
                    os.killpg(p.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                os.killpg(p.pid, signal.SIGKILL)
                pytest.fail("a worker outlived its killed parent")
    finally:
        if p.poll() is None:
            p.kill()

    con = sqlite3.connect(dbp)
    assert _attempts(con, "HANGME") == [None]  # the attempt-started row survived the kill
    con.close()

    # Rerun with a short deadline: the dead parent's attempt is strike one, this timeout is
    # strike two. Only the pending item is processed; finished items are not re-attempted.
    assert _parent(ev, data, "v1", deadline=DEADLINE).wait(timeout=60) == 0
    con = sqlite3.connect(dbp)
    assert _status(con, "HANGME")[0] == "quarantined"
    assert _attempts(con, "HANGME") == [None, "timeout"]
    for n in NAMES[:3]:
        assert _attempts(con, n) == ["ok"] and _status(con, n)[0] == "ok"
    con.close()

    assert _parent(ev, data, "v1", deadline=DEADLINE).wait(timeout=60) == 0  # skipped
    con = sqlite3.connect(dbp)
    assert len(_attempts(con, "HANGME")) == 2
    con.close()
