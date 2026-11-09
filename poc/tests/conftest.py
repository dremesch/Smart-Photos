"""Shared POC fixtures. Synthetic data only (AGENTS.md: no personal photos)."""

from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
TOOLS = POC / "tools"
sys.path.insert(0, str(POC))

# The review UI is tested headless, on CI and locally (issue #25).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def synthetic_event(tmp_path_factory):
    """The synthetic event run end to end once per session: scan, ingest, analyse, rank,
    auto-approve 8, propose crops, export, integrity. One file crashes its decoder and one
    hangs it (issue #11), so the event also carries `crashed` and `timeout` photos. Shared by test_poc and the
    review-mode tests, which work on copies of its database."""
    pytest.importorskip("skimage")
    sys.path.insert(0, str(TOOLS))
    import make_sample_event
    from fpc_poc.__main__ import main

    root = tmp_path_factory.mktemp("e2e")
    event_dir, data, out = root / "event", root / "data", root / "prints"
    with contextlib.redirect_stdout(io.StringIO()):  # the generator prints its output path
        make_sample_event.main(event_dir)
    mp = pytest.MonkeyPatch()
    mp.setenv("FPC_POC_CRASH_MARKER", "CRASHME")
    mp.setenv("FPC_POC_HANG_MARKER", "HANGME")  # one file hangs its decoder (issue #11)
    from fpc_poc import config

    mp.setattr(config, "DECODE_DEADLINE_S", 2.0)
    mp.setattr(config, "KILL_GRACE_S", 0.5)
    rc = main(["--data", str(data), "run", "--event", "Test", str(event_dir),
               "--auto-approve", "8", "--out", str(out), "--workers", "2"])
    mp.undo()
    assert rc == 0
    con = sqlite3.connect(data / "poc.db")
    con.row_factory = sqlite3.Row
    return {"con": con, "event_dir": event_dir, "data": data, "out": out, "event": "Test"}
