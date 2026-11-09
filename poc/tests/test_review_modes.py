"""Review modes (issue #25, A-23; S2-D3, S2-D4): the model-free `--manual` fallback.

Runs the synthetic event through both modes, headless (QT_QPA_PLATFORM=offscreen, set in
conftest.py). Every test works on a copy of the shared database, so decisions made here
never reach the other suites. Assertions print counts, never names or paths (#13).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from fpc_poc import crop, db, pipeline, ui  # noqa: E402
from fpc_poc.config import Paths  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# Anything the ranking or the detectors produce. None of it may appear in manual mode.
SCORE_TEXT = re.compile(r"\b[01]\.\d\d\b|Score|rank \d|ambiguous|recommend", re.IGNORECASE)
AI_REASONS = ("face(s)", "soft focus", "exposure", "low resolution", "incomplete metadata",
              "pet", "protected region", "low-confidence face", "frame edge")
AI_MARKS = ("★", "⚠", "⛔")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def review(synthetic_event, tmp_path, app):
    """A private copy of the synthetic event's database plus the shared derivative cache."""
    con = db.connect(tmp_path / "review.db")
    synthetic_event["con"].backup(con)  # WAL-safe copy
    paths = Paths(synthetic_event["data"])
    windows = []

    def open_ui(mode: str) -> ui.Main:
        w = ui.Main(con, paths, synthetic_event["event"], mode=mode)
        w.show()
        app.processEvents()
        windows.append(w)
        return w

    yield con, paths, open_ui
    for w in windows:
        w.close()
        w.deleteLater()
    app.processEvents()


def _items(w: ui.Main) -> list[tuple[int, str]]:
    return [(w.grid.item(i).data(Qt.ItemDataRole.UserRole), w.grid.item(i).text())
            for i in range(w.grid.count())]


def _visible_text(w: ui.Main) -> str:
    """Everything the curator can read for every photo in every view."""
    parts = [w.windowTitle(), w.status.text()]
    for name in w.filter_names():
        w.filter.setCurrentText(name)
        for i in range(w.grid.count()):
            w.grid.setCurrentRow(i)
            parts.append(w.grid.item(i).text())
            parts.append(w.details.toPlainText())
    return "\n".join(parts)


# ---------------------------------------------------------------- manual mode shows no AI


def test_manual_mode_shows_no_ranking_score_or_reason(review):
    _, _, open_ui = review
    w = open_ui("manual")
    assert w.mode == "manual"
    assert "Recommended" not in w.filter_names() and "Crop review" not in w.filter_names()
    text = _visible_text(w)
    assert not SCORE_TEXT.search(text), "score, rank or recommendation text shown"
    assert not [r for r in AI_REASONS if r in text], "an AI reason is shown"
    assert not [m for m in AI_MARKS if m in text], "a best/risk marker is shown"
    assert not w.rerank_btn.isVisible() and not w.crops_btn.isVisible()


def test_manual_mode_ignores_detector_overlays(review):
    con, _, open_ui = review
    w = open_ui("manual")
    with_faces = [r[0] for r in con.execute("SELECT photo_id, data FROM features")
                  if json.loads(r[1])["faces"] or json.loads(r[1])["pets"]]
    assert with_faces, "the synthetic event has detector hits to hide"
    assert all(w.overlay_boxes(pid) == [] for pid in with_faces)
    a = open_ui("assisted")
    assert any(a.overlay_boxes(pid) for pid in with_faces)  # control: assisted still draws them


def test_assisted_mode_still_shows_scores(review):
    _, _, open_ui = review  # control, so the manual test above cannot pass vacuously
    w = open_ui("assisted")
    assert "Recommended" in w.filter_names()
    assert SCORE_TEXT.search(_visible_text(w))


def test_manual_mode_is_capture_time_order_and_stacks_are_plain(review):
    con, _, open_ui = review
    w = open_ui("manual")
    w.filter.setCurrentText("All photos")
    shown = [pid for pid, _ in _items(w)]
    expected = [r[0] for r in con.execute(
        "SELECT id FROM photos WHERE event='Test' AND status='ok' "
        "ORDER BY capture_ts IS NULL, capture_ts, rel")]  # undated photos last
    assert shown == expected
    dated = [pid for pid in shown if con.execute(
        "SELECT capture_ts FROM photos WHERE id=?", (pid,)).fetchone()[0]]
    assert shown[:len(dated)] == dated
    # The burst: the stack lists its members in capture order, not by score.
    gid = con.execute("SELECT group_id FROM members GROUP BY group_id ORDER BY COUNT(*) DESC "
                      "LIMIT 1").fetchone()[0]
    members = [r[0] for r in con.execute(
        "SELECT p.id FROM members m JOIN photos p ON p.id=m.photo_id WHERE m.group_id=? "
        "ORDER BY p.capture_ts IS NULL, p.capture_ts, p.rel", (gid,))]
    by_rank = [r[0] for r in con.execute(
        "SELECT photo_id FROM members WHERE group_id=? ORDER BY rank", (gid,))]
    assert members != by_rank, "fixture: the burst's rank order differs from capture order"
    w.grid.setCurrentRow(shown.index(members[2]))
    QTest.keyClick(w.grid, Qt.Key.Key_C)
    assert w.filter.currentText() == "Group of selected"
    assert [pid for pid, _ in _items(w)] == members


# ---------------------------------------------------------------- switching keeps decisions


def _log(con: sqlite3.Connection) -> list[tuple]:
    return [tuple(r) for r in con.execute("SELECT id, photo_id, action, source, ts, undoes "
                                          "FROM decisions ORDER BY id")]


def test_switching_modes_keeps_every_decision(review):
    con, _, open_ui = review
    before = _log(con)
    w = open_ui("assisted")
    w.filter.setCurrentText("All photos")
    w.grid.setCurrentRow(0)
    QTest.keyClick(w.grid, Qt.Key.Key_A)
    QTest.keyClick(w.grid, Qt.Key.Key_X)
    state_assisted = db.review_state(con, "Test")
    QTest.keyClick(w.grid, Qt.Key.Key_M)  # live switch
    assert w.mode == "manual"
    assert db.review_state(con, "Test") == state_assisted
    shown = dict(_items(w))
    assert all(shown[pid].startswith({"approve": "✓", "reject": "✗"}[s])
               for pid, s in state_assisted.items() if pid in shown)
    QTest.keyClick(w.grid, Qt.Key.Key_A)
    QTest.keyClick(w.grid, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)  # undo is an event
    QTest.keyClick(w.grid, Qt.Key.Key_X)
    QTest.keyClick(w.grid, Qt.Key.Key_M)
    assert w.mode == "assisted"
    state_both = db.review_state(con, "Test")
    w.close()
    m = open_ui("manual")  # and across a restart in the other mode
    assert db.review_state(con, "Test") == state_both
    assert m.status.text() == w.status.text()
    after = _log(con)
    assert after[:len(before)] == before, "an earlier decision row changed (ADR-0008)"
    assert len(after) == len(before) + 5  # A, X, A, undo, X: append-only


def test_reason_codes_are_append_only_events_in_both_modes(review):
    con, _, open_ui = review
    for mode in ("manual", "assisted"):
        w = open_ui(mode)
        w.filter.setCurrentText("All photos")
        w.grid.setCurrentRow(3)
        pid = w.current_id()
        state = db.review_state(con, "Test")
        QTest.keyClick(w.grid, Qt.Key.Key_1)  # BLINK
        assert w.current_id() == pid  # a tag does not advance
        assert "BLINK" in db.reasons(con, pid)
        assert db.review_state(con, "Test") == state  # a reason is not a decision
        assert "BLINK" in w.details.toPlainText()
        QTest.keyClick(w.grid, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert "BLINK" not in db.reasons(con, pid)
        w.close()  # the app has one window; two would make every shortcut ambiguous


# ---------------------------------------------------------------- crops: bordered by default


def test_manual_mode_crop_defaults_to_bordered_with_borderless_toggle(review):
    con, _, open_ui = review
    w = open_ui("manual")
    w.filter.setCurrentText("All photos")
    w.grid.setCurrentRow(0)
    pid = w.current_id()
    c = w.crop_of(pid)
    assert c["mode"] == "bordered" and c["norm"] == [0.0, 0.0, 1.0, 1.0]
    assert "bordered" in w.details.toPlainText().lower()
    QTest.keyClick(w.grid, Qt.Key.Key_B)
    c = w.crop_of(pid)
    W, H = c["oriented_size"]
    x, y, cw, ch = c["px"]
    assert c["mode"] == "borderless"
    crop.validate(W, H, x, y, cw, ch)
    assert c["protected"] == []  # Haar faces ignored: a plain centred crop to drag
    assert abs((x + cw / 2) - W / 2) <= 1 and abs((y + ch / 2) - H / 2) <= 1
    assert con.execute("SELECT manual FROM crops WHERE photo_id=?", (pid,)).fetchone()[0] == 1
    QTest.keyClick(w.grid, Qt.Key.Key_B)
    assert w.crop_of(pid)["mode"] == "bordered"


def test_manual_mode_hides_unreviewed_proposed_crops(review):
    con, _, open_ui = review
    pid = con.execute("SELECT photo_id FROM crops WHERE manual=0 AND data LIKE '%borderless%'"
                      ).fetchone()[0]
    w = open_ui("manual")
    assert w.crop_of(pid)["mode"] == "bordered"  # what export will print (S2-D4)


@pytest.mark.parametrize(("data", "manual", "mode"), [
    (None, None, "bordered"),  # approved, never proposed
    (crop.propose(4032, 3024, [], [], (0.5, 0.5)).to_dict(), 0, "bordered"),  # nobody reviewed
    (crop.propose(4032, 3024, [], [], (0.5, 0.5)).to_dict(), 1, "borderless"),  # reviewed
    (crop.bordered(4032, 3024).to_dict(), 1, "bordered"),
])
def test_unreviewed_crops_fall_back_to_bordered(data, manual, mode):
    c = crop.for_export(json.dumps(data) if data else None, manual, 4032, 3024)
    assert c["mode"] == mode
    assert list(c["oriented_size"]) == [4032, 3024]


def test_manual_mode_bordered_export_verifies_and_keeps_originals(review, synthetic_event, tmp_path):
    con, _, open_ui = review
    originals = {r["path"]: Path(r["path"]).read_bytes()  # synthetic fixture only
                 for r in con.execute("SELECT path FROM photos WHERE status='ok'")}
    w = open_ui("manual")
    w.filter.setCurrentText("All photos")
    w.grid.setCurrentRow(0)
    for _ in range(6):  # keys only; nobody proposes or reviews a crop
        QTest.keyClick(w.grid, Qt.Key.Key_A)
    approved = [p for p, s in db.review_state(con, "Test").items() if s == "approve"]
    res = pipeline.export(con, "Test", tmp_path / "prints", workers=2)
    assert res["done"] == len(approved) and res["verified"] == len(approved)
    assert res["bordered_fallback"] == len(approved)
    assert all(isinstance(v, int) for v in res.values())  # aggregate-only (#13)
    base = tmp_path / "prints" / "Test"
    crops = json.loads((base / "manifests" / "crop_decisions.json").read_text())
    assert {c["mode"] for c in crops.values()} == {"bordered"}
    for f in (base / "print_ready").glob("*.jpg"):
        with Image.open(f) as im:  # our own synthetic export, not a user photo
            assert sorted(im.size) == [1200, 1800]
    assert all(Path(p).read_bytes() == b for p, b in originals.items())


# ---------------------------------------------------------------- CLI


def test_cli_review_manual_flag(synthetic_event, monkeypatch):
    from fpc_poc.__main__ import main

    seen = {}
    monkeypatch.setattr(ui, "run_ui", lambda con, paths, event, mode="assisted": seen.update(
        mode=mode) or 0)
    data = str(synthetic_event["data"])
    assert main(["--data", data, "review", "--event", "Test", "--manual"]) == 0
    assert seen["mode"] == "manual"
    assert main(["--data", data, "review", "--event", "Test"]) == 0
    assert seen["mode"] == "assisted"


# ---------------------------------------------------------------- 400 photos, keys only


@pytest.fixture
def batch400(tmp_path, app):
    """400 synthetic photos in 80 stacks of 5, with scores and auto crops present, built
    straight into the database (no decoding), plus tiny derivatives and thumbnails."""
    paths = Paths(tmp_path / "data")
    con = db.connect(paths.db)
    deriv = tmp_path / "d.jpg"
    Image.new("RGB", (1024, 683), (90, 120, 150)).save(deriv, quality=80)
    deriv_bytes = deriv.read_bytes()
    thumbs = []
    for k in range(5):
        p = tmp_path / f"t{k}.jpg"
        Image.new("RGB", (256, 171), (40 * k, 100, 200 - 30 * k)).save(p, quality=80)
        thumbs.append(p.read_bytes())
    for i in range(400):
        sha = f"{i:064x}"
        (paths.deriv / f"{sha}.jpg").write_bytes(deriv_bytes)
        (paths.thumb / f"{sha}.jpg").write_bytes(thumbs[i % 5])
        ts = f"2024-04-0{2 + i // 150}T{8 + (i % 150) // 15:02d}:{(i * 4) % 60:02d}:{i % 60:02d}"
        con.execute("INSERT INTO photos(id, event, path, rel, status, sha256, width, height, "
                    "capture_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                    (i + 1, "Batch", f"/synthetic/{i}.JPG", f"Day/IMG_{i:04d}.JPG", "ok", sha,
                     4032, 3024, ts))
        con.execute("INSERT INTO scores VALUES(?,?,?,?,?)",
                    (i + 1, 0.5 + (i % 7) / 20, json.dumps(["soft focus"]), int(i % 3 == 0), i))
        if i % 5 == 0:
            gid = con.execute("INSERT INTO groups(event, kind, ambiguous) VALUES('Batch','near',0)"
                              ).lastrowid
        con.execute("INSERT INTO members VALUES(?,?,?)", (gid, i + 1, 4 - i % 5))
    con.commit()
    w = ui.Main(con, paths, "Batch", mode="manual")
    w.show()
    app.processEvents()
    yield con, w
    w.close()
    w.deleteLater()
    app.processEvents()


def test_400_photo_batch_decided_with_keys_only_without_reloads(batch400, app):
    con, w = batch400
    assert w.grid.count() == 400
    QTest.keyClick(w.grid, Qt.Key.Key_End)
    assert w.grid.currentRow() == 399
    QTest.keyClick(w.grid, Qt.Key.Key_Home)
    assert w.grid.currentRow() == 0
    reloads = w.reload_count
    t0 = time.perf_counter()
    for i in range(400):
        QTest.keyClick(w.grid, Qt.Key.Key_A if i % 3 else Qt.Key.Key_X)
        app.processEvents()
    total = time.perf_counter() - t0
    assert w.reload_count == reloads, "a decision rebuilt the whole grid"
    state = db.review_state(con, "Batch")
    assert len(state) == 400 and sum(v == "reject" for v in state.values()) == 134
    t = w.key_timing()
    # Aggregate timing only. The RDP target is 1 s per group (S2-D3); a key gets 250 ms here.
    print(f"\n[ux25] 400 keys in {total:.2f}s; per key p50 {t['p50_ms']:.1f} ms, "
          f"p95 {t['p95_ms']:.1f} ms, max {t['max_ms']:.1f} ms")
    assert t["count"] == 400 and t["p95_ms"] < 250
    w.close()
    stages = {r[0] for r in con.execute("SELECT stage FROM timings WHERE event='Batch'")}
    assert {"ui_key_p95", "ui_key_max"} <= stages  # logged for the VM's RDP check
