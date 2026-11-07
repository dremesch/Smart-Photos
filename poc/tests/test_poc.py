"""POC tests. Run from repo root:  pytest poc/tests  (needs poc/requirements.txt)."""

from __future__ import annotations

import json
import random
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpc_poc import crop, db, inventory  # noqa: E402
from fpc_poc.__main__ import main  # noqa: E402

TOOLS = Path(__file__).resolve().parents[1] / "tools"


# ---------------------------------------------------------------- crop geometry


@pytest.mark.parametrize("seed", range(200))
def test_crop_invariant_random(seed):
    rnd = random.Random(seed)
    w, h = rnd.randint(300, 9000), rnd.randint(300, 9000)
    faces = [{"box": [rnd.random() * 0.8, rnd.random() * 0.8, 0.1, 0.1], "conf": 1.0}
             for _ in range(rnd.randint(0, 3))]
    c = crop.propose(w, h, faces, [], (0.5, 0.5))
    x, y, cw, ch = c.px
    crop.validate(w, h, x, y, cw, ch)
    assert c.target == ("3:2" if w >= h else "2:3")


def test_spec_v10_example_is_rejected():
    # v1.0 §10.3 example on a 4032x3024 source is 1.6:1, not 3:2
    with pytest.raises(ValueError):
        crop.validate(4032, 3024, 81, 302, round(0.96 * 4032), round(0.80 * 3024))


def test_iphone_43_max_crop():
    cw, ch, axis = crop.max_rect(4032, 3024)
    assert (cw, ch, axis) == (4032, 2688, "y")
    cw, ch, axis = crop.max_rect(3024, 4032)
    assert (cw, ch, axis) == (2688, 4032, "x")


def test_face_beats_pet_when_both_cannot_fit():
    face = {"box": [0.45, 0.02, 0.1, 0.12], "conf": 1.0}  # head at top edge
    pet = {"box": [0.4, 0.80, 0.2, 0.18]}  # pet at bottom edge
    c = crop.propose(4032, 3024, [face], [pet], (0.5, 0.5))
    assert c.px[1] == 0 and c.risk == "crop_conflict"


# ---------------------------------------------------------------- cloud attributes


@pytest.mark.parametrize(("attrs", "state"), [
    (None, "local"),
    (0x20, "local"),
    (inventory.RECALL_ON_DATA_ACCESS, "online_only"),
    (inventory.RECALL_ON_OPEN, "online_only"),
    (inventory.OFFLINE, "online_only"),
    (inventory.UNPINNED, "local_unpinned"),
    (inventory.PINNED | 0x20, "local"),
])
def test_cloud_state(attrs, state):
    assert inventory.cloud_state(attrs) == state


# ---------------------------------------------------------------- decision log


def test_decisions_append_only_with_undo(tmp_path):
    con = db.connect(tmp_path / "t.db")
    con.execute("INSERT INTO photos(id, event, path) VALUES(1,'e','a'),(2,'e','b')")
    db.decide(con, 1, "approve")
    db.decide(con, 2, "reject")
    db.decide(con, 1, "reject")
    assert db.review_state(con, "e") == {1: "reject", 2: "reject"}
    db.undo_last(con, "e")
    assert db.review_state(con, "e") == {1: "approve", 2: "reject"}
    db.undo_last(con, "e")
    assert db.review_state(con, "e") == {1: "approve"}
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 5  # nothing deleted


# ---------------------------------------------------------------- end to end


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    pytest.importorskip("skimage")
    sys.path.insert(0, str(TOOLS))
    import make_sample_event

    root = tmp_path_factory.mktemp("e2e")
    event_dir, data, out = root / "event", root / "data", root / "prints"
    make_sample_event.main(event_dir)
    mp = pytest.MonkeyPatch()
    mp.setenv("FPC_POC_CRASH_MARKER", "CRASHME")
    rc = main(["--data", str(data), "run", "--event", "Test", str(event_dir),
               "--auto-approve", "8", "--out", str(out), "--workers", "2"])
    mp.undo()
    assert rc == 0
    con = sqlite3.connect(data / "poc.db")
    con.row_factory = sqlite3.Row
    return con, event_dir, out


def test_e2e_hostile_files_isolated(e2e):
    con, *_ = e2e
    rows = {r["rel"].split("/")[-1]: r for r in con.execute("SELECT * FROM photos")}
    assert rows["IMG_3998_CRASHME.JPG"]["error"].startswith("crashed")
    assert rows["IMG_3999_corrupt.JPG"]["status"] == "import_error"
    assert rows["Screenshot 2024-04-04.png"]["status"] == "excluded"
    assert rows["IMG_2200.HEIC"]["status"] == "ok"
    assert rows["IMG_2200.HEIC"]["companion"].endswith("IMG_2200.MOV")


def test_e2e_orientation(e2e):
    con, *_ = e2e
    r = con.execute("SELECT width, height FROM photos WHERE rel LIKE '%IMG_1010.JPG'").fetchone()
    assert (r["width"], r["height"]) == (1512, 2016)  # EXIF 6 applied once


def test_e2e_burst_blink_not_chosen(e2e):
    con, *_ = e2e
    g = con.execute("SELECT group_id FROM members m JOIN photos p ON p.id=m.photo_id "
                    "WHERE p.rel LIKE '%IMG_1003.JPG'").fetchone()[0]
    ranked = [r[0].split("/")[-1] for r in con.execute(
        "SELECT p.rel FROM members m JOIN photos p ON p.id=m.photo_id WHERE group_id=? ORDER BY rank", (g,))]
    assert ranked[-1] == "IMG_1004.JPG"  # the closed-eyes frame ranks last
    assert set(ranked[:2]) == {"IMG_1003.JPG", "IMG_2001.JPG"}


def test_e2e_exact_and_messaging_copies_grouped(e2e):
    con, *_ = e2e
    kinds = [r[0] for r in con.execute("SELECT kind FROM groups")]
    assert "exact" in kinds
    whatsapp_group = con.execute(
        "SELECT g.kind FROM groups g JOIN members m ON m.group_id=g.id JOIN photos p ON p.id=m.photo_id "
        "WHERE p.rel LIKE '%WhatsApp%'").fetchone()
    assert whatsapp_group is not None


def test_e2e_exports_verified_and_stripped(e2e):
    con, _, out = e2e
    from PIL import Image

    files = sorted((out / "Test" / "print_ready").glob("*.jpg"))
    assert len(files) == 8
    for f in files:
        with Image.open(f) as im:
            assert sorted(im.size) == [1200, 1800]
            assert not im.getexif().get_ifd(0x8825)
            assert im.info.get("icc_profile")
    summary = json.loads((out / "Test" / "reports" / "export_summary.json").read_text())
    assert summary["summary"]["verified"] == 8


def test_e2e_originals_untouched(e2e):
    con, *_ = e2e
    from fpc_poc.decode import sha256_file

    for r in con.execute("SELECT path, sha256 FROM photos WHERE status='ok'"):
        assert sha256_file(r["path"]) == r["sha256"]


def test_e2e_export_refuses_source_folder(e2e):
    con, event_dir, _ = e2e
    from fpc_poc import pipeline

    with pytest.raises(SystemExit):
        pipeline.export(con, "Test", event_dir / "Day1")


# ---------------------------------------------------------------- Windows regressions (PR #39 CI)


def _synthetic_jpeg(path: Path) -> dict:
    from PIL import Image

    Image.new("RGB", (900, 600), (120, 160, 200)).save(path, quality=90)  # synthetic, no metadata
    return crop.bordered(900, 600).to_dict()


def _require_writable_fd(fd: int) -> None:
    """Windows os.fsync calls _commit, which fails on a read-only handle. Emulate that on
    every platform: a zero-byte write fails with EBADF on a handle not opened for writing."""
    import os

    os.write(fd, b"")


def test_export_fsyncs_through_a_writable_handle(tmp_path, monkeypatch):
    import os

    from fpc_poc import export

    src = tmp_path / "src.jpg"
    c = _synthetic_jpeg(src)
    out = tmp_path / "out" / "TEST_0001.jpg"
    out.parent.mkdir()
    calls = []
    real = os.fsync

    def checked_fsync(fd):
        _require_writable_fd(fd)
        calls.append(fd)
        real(fd)

    monkeypatch.setattr(os, "fsync", checked_fsync)
    res = export.task_export(str(src), c, str(out), "0" * 64)
    assert res["status"] == "done" and res["verified"]
    assert calls  # the data was fsynced before the rename
    assert not list(out.parent.glob("*.tmp"))


def test_failed_export_removes_its_own_tmp(tmp_path, monkeypatch):
    import os

    from fpc_poc import export

    src = tmp_path / "src.jpg"
    c = _synthetic_jpeg(src)
    out = tmp_path / "out" / "TEST_0001.jpg"
    out.parent.mkdir()

    def failing_fsync(fd):
        raise OSError(9, "Bad file descriptor")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError):
        export.task_export(str(src), c, str(out), "0" * 64)
    assert not out.exists()
    assert not list(out.parent.glob("*.tmp"))


def test_rel_is_posix_even_for_windows_paths():
    from pathlib import PureWindowsPath

    rel = inventory.rel_posix(PureWindowsPath(r"D:\OneDrive\Trip\Day1\IMG_1004.JPG"),
                              PureWindowsPath(r"D:\OneDrive\Trip"))
    assert rel == "Day1/IMG_1004.JPG"


def test_e2e_rel_has_no_backslash(e2e):
    con, *_ = e2e
    rels = [r[0] for r in con.execute("SELECT rel FROM photos")]
    assert rels and not any("\\" in r for r in rels)
    assert any("/" in r for r in rels)
