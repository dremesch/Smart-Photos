"""Minimal PySide6 review UI.

Keys: A approve · X reject · U clear · Ctrl+Z undo · C show group · B bordered/borderless ·
R re-rank · P propose crops for approved · arrows/Home/End navigate.
Drag the crop rectangle in the preview to override it (stays a valid 3:2 / 2:3 crop).
"""

from __future__ import annotations

import json
import sqlite3

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from . import crop as cropmod
from . import db, pipeline
from .config import Paths

FILTERS = ["Recommended", "All photos", "Approved", "Crop review", "Group of selected", "Import problems"]


class Preview(QLabel):
    """Shows the derivative with face/pet boxes and the crop; drag to move the crop."""

    def __init__(self, win: "Main"):
        super().__init__()
        self.win = win
        self.setMinimumSize(520, 420)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pix: QPixmap | None = None
        self.target = QRectF()
        self.drag: QPointF | None = None

    def show_photo(self, pix: QPixmap | None) -> None:
        self.pix = pix
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        super().paintEvent(ev)
        if not self.pix:
            return
        p = QPainter(self)
        s = min(self.width() / self.pix.width(), self.height() / self.pix.height())
        w, h = self.pix.width() * s, self.pix.height() * s
        self.target = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        p.drawPixmap(self.target, self.pix, QRectF(self.pix.rect()))
        pid = self.win.current_id()
        f = db.features(self.win.con, pid) if pid else {}
        t = self.target

        def box(b, color, width=2, style=Qt.PenStyle.SolidLine):
            p.setPen(QPen(QColor(color), width, style))
            p.drawRect(QRectF(t.x() + b[0] * t.width(), t.y() + b[1] * t.height(),
                              b[2] * t.width(), b[3] * t.height()))

        for fc in f.get("faces", []):
            box(fc["box"], "#2ecc71" if fc["eyes_open"] else "#f39c12")
        for pt in f.get("pets", []):
            box(pt["box"], "#3498db")
        c = self.win.crop_of(pid)
        if c:
            # dim outside the crop
            n = c["norm"]
            r = QRectF(t.x() + n[0] * t.width(), t.y() + n[1] * t.height(), n[2] * t.width(), n[3] * t.height())
            p.setBrush(QColor(0, 0, 0, 110))
            p.setPen(Qt.PenStyle.NoPen)
            for rr in (QRectF(t.left(), t.top(), t.width(), r.top() - t.top()),
                       QRectF(t.left(), r.bottom(), t.width(), t.bottom() - r.bottom()),
                       QRectF(t.left(), r.top(), r.left() - t.left(), r.height()),
                       QRectF(r.right(), r.top(), t.right() - r.right(), r.height())):
                p.drawRect(rr)
            p.setBrush(Qt.BrushStyle.NoBrush)
            col = {"likely_safe": "#ffffff", "needs_review": "#f1c40f", "crop_conflict": "#e74c3c"}[c["risk"]]
            p.setPen(QPen(QColor(col), 2, Qt.PenStyle.DashLine))
            p.drawRect(r)
        p.end()

    def mousePressEvent(self, ev):  # noqa: N802
        self.drag = ev.position()

    def mouseMoveEvent(self, ev):  # noqa: N802
        pid = self.win.current_id()
        c = self.win.crop_of(pid)
        if self.drag is None or not c or c["mode"] != "borderless" or self.target.width() == 0:
            return
        W, H = c["oriented_size"]
        x, y, cw, ch = c["px"]
        d = ev.position() - self.drag
        self.drag = ev.position()
        if cw < W:
            x = int(min(max(0, x + d.x() / self.target.width() * W), W - cw))
        else:
            y = int(min(max(0, y + d.y() / self.target.height() * H), H - ch))
        cropmod.validate(W, H, x, y, cw, ch)  # invariant on every edit
        c["px"] = [x, y, cw, ch]
        c["norm"] = [x / W, y / H, cw / W, ch / H]
        c["risk"], c["reasons"] = "likely_safe", ["manual override"]
        self.win._pending_manual = (pid, c)
        self.update()

    def mouseReleaseEvent(self, ev):  # noqa: N802
        self.drag = None
        if getattr(self.win, "_pending_manual", None):
            pid, c = self.win._pending_manual
            self.win.con.execute("INSERT OR REPLACE INTO crops(photo_id, data, risk, manual) VALUES(?,?,?,1)",
                                 (pid, json.dumps(c), c["risk"]))
            self.win.con.commit()
            self.win._pending_manual = None
            self.win.refresh_details()


class Main(QMainWindow):
    def __init__(self, con: sqlite3.Connection, paths: Paths, event: str):
        super().__init__()
        self.con, self.paths, self.ev_name = con, paths, event
        self._pending_manual = None
        self.setWindowTitle(f"Family Photo Curator POC — {event}")
        self.resize(1500, 900)

        self.filter = QComboBox()
        self.filter.addItems(FILTERS)
        self.filter.currentIndexChanged.connect(self.reload)
        self.status = QLabel()
        rerank = QPushButton("Re-rank (R)")
        rerank.clicked.connect(self.rerank)
        crops = QPushButton("Propose crops (P)")
        crops.clicked.connect(self.crops)
        top = QHBoxLayout()
        for w in (QLabel("View:"), self.filter, rerank, crops, self.status):
            top.addWidget(w)
        top.addStretch()

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(200, 200))
        self.grid.setGridSize(QSize(220, 250))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setWordWrap(True)
        self.grid.currentItemChanged.connect(self._on_select)

        self.preview = Preview(self)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.preview)
        right.addWidget(self.details)
        right.setSizes([600, 250])
        split = QSplitter()
        split.addWidget(self.grid)
        split.addWidget(right)
        split.setSizes([850, 650])

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.addLayout(top, 0)
        lay.addWidget(split, 1)
        self.setCentralWidget(root)

        for key, fn in [("A", lambda: self.decide("approve")), ("X", lambda: self.decide("reject")),
                        ("U", lambda: self.decide("clear")), ("Ctrl+Z", self.undo),
                        ("C", lambda: self.filter.setCurrentIndex(4)), ("B", self.toggle_border),
                        ("R", self.rerank), ("P", self.crops)]:
            act = QAction(self)
            act.setShortcut(QKeySequence(key))
            act.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            act.triggered.connect(fn)
            self.addAction(act)
        self._group_anchor: int | None = None
        self._last_selected: int | None = None
        self.reload()

    def _on_select(self, *_):
        if FILTERS[self.filter.currentIndex()] != "Group of selected":
            self._last_selected = self.current_id()
        self.refresh_details()

    # ---------------------------------------------------------------- data
    def rows(self) -> list[sqlite3.Row]:
        view = FILTERS[self.filter.currentIndex()]
        base = ("SELECT p.*, s.score, s.recommended, s.rec_order, s.reasons, c.risk FROM photos p "
                "LEFT JOIN scores s ON s.photo_id=p.id LEFT JOIN crops c ON c.photo_id=p.id WHERE p.event=? ")
        state = db.review_state(self.con, self.ev_name)
        if view == "Recommended":
            q = base + "AND s.recommended=1 ORDER BY s.rec_order"
        elif view == "Import problems":
            q = base + "AND p.status IN ('import_error','unavailable','missing','timeout','crashed','quarantined') ORDER BY p.rel"
        elif view == "Crop review":
            q = base + "AND c.risk IN ('needs_review','crop_conflict') ORDER BY c.risk, p.capture_ts"
        elif view == "Group of selected" and self._group_anchor:
            q = (base + "AND p.id IN (SELECT photo_id FROM members WHERE group_id IN "
                 f"(SELECT group_id FROM members WHERE photo_id={int(self._group_anchor)})) ORDER BY s.score DESC")
        else:
            q = base + "AND p.status='ok' ORDER BY p.capture_ts, p.rel"
        rows = self.con.execute(q, (self.ev_name,)).fetchall()
        if view == "Approved":
            rows = [r for r in rows if state.get(r["id"]) == "approve"]
        return rows

    def current_id(self) -> int | None:
        it = self.grid.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def crop_of(self, pid):
        if not pid:
            return None
        r = self.con.execute("SELECT data FROM crops WHERE photo_id=?", (pid,)).fetchone()
        return json.loads(r[0]) if r else None

    def label(self, r, state) -> str:
        s = state.get(r["id"])
        mark = {"approve": "✓ ", "reject": "✗ "}.get(s, "")
        star = "★ " if r["recommended"] else ""
        sc = f"{r['score']:.2f}" if r["score"] is not None else r["status"]
        risk = {"needs_review": " ⚠crop", "crop_conflict": " ⛔crop"}.get(r["risk"], "")
        return f"{mark}{star}{r['rel'].split('/')[-1]}\n{sc}{risk}"

    def reload(self) -> None:
        self._group_anchor = self._last_selected
        keep = self.current_id()
        self.grid.clear()
        state = db.review_state(self.con, self.ev_name)
        for r in self.rows():
            it = QListWidgetItem(self.label(r, state))
            thumb = self.paths.thumb / f"{r['sha256']}.jpg"
            if r["sha256"] and thumb.exists():
                it.setIcon(QIcon(str(thumb)))
            it.setData(Qt.ItemDataRole.UserRole, r["id"])
            if state.get(r["id"]) == "approve":
                it.setBackground(QColor("#d5f5e3"))
            elif state.get(r["id"]) == "reject":
                it.setBackground(QColor("#f5d5d5"))
            self.grid.addItem(it)
            if r["id"] == keep:
                self.grid.setCurrentItem(it)
        if self.grid.currentItem() is None and self.grid.count():
            self.grid.setCurrentRow(0)
        c = {k: v for k, v in zip(("approve", "reject"), (0, 0))}
        for v in state.values():
            c[v] = c.get(v, 0) + 1
        self.status.setText(f"   {self.grid.count()} shown · {c['approve']} approved · {c['reject']} rejected")

    def refresh_details(self) -> None:
        pid = self.current_id()
        if not pid:
            self.preview.show_photo(None)
            self.details.clear()
            return
        r = self.con.execute("SELECT p.*, s.score, s.reasons FROM photos p LEFT JOIN scores s ON s.photo_id=p.id "
                             "WHERE p.id=?", (pid,)).fetchone()
        deriv = self.paths.deriv / f"{r['sha256']}.jpg"
        self.preview.show_photo(QPixmap(str(deriv)) if r["sha256"] and deriv.exists() else None)
        g = self.con.execute("SELECT g.kind, g.ambiguous, m.rank, (SELECT COUNT(*) FROM members WHERE group_id=g.id) n "
                             "FROM members m JOIN groups g ON g.id=m.group_id WHERE m.photo_id=?", (pid,)).fetchone()
        c = self.crop_of(pid)
        lines = [
            f"<b>{r['rel']}</b> — {r['width']}×{r['height']} {r['fmt'] or ''} · {r['status']}",
            f"Captured {r['capture_ts'] or 'unknown'} {r['utc_offset'] or ''} ({r['ts_source'] or 'no timestamp'}) · {r['device'] or 'unknown device'}",
            f"Score {r['score']}: {', '.join(json.loads(r['reasons'] or '[]')) or '—'}",
            (f"Group: {g['kind']}, rank {g['rank'] + 1} of {g['n']}{' (ambiguous)' if g['ambiguous'] else ''} — press C to compare"
             if g else "Unique photo (no group)"),
            f"Crop: {c['mode']} {c['target']} → <b>{c['risk']}</b> ({'; '.join(c['reasons'])})" if c else "Crop: none (approve, then P)",
        ]
        if r["error"]:
            lines.append(f"<span style='color:#c0392b'>Error: {r['error']}</span>")
        if r["has_gps"]:
            lines.append("Has GPS (will be stripped on export)")
        self.details.setHtml("<br>".join(lines))

    # ---------------------------------------------------------------- actions
    def decide(self, action: str) -> None:
        pid = self.current_id()
        if not pid:
            return
        db.decide(self.con, pid, action)
        row = self.grid.currentRow()
        self.reload()
        self.grid.setCurrentRow(min(row + 1, self.grid.count() - 1))

    def undo(self) -> None:
        pid = db.undo_last(self.con, self.ev_name)
        self.reload()
        if pid:
            for i in range(self.grid.count()):
                if self.grid.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                    self.grid.setCurrentRow(i)

    def toggle_border(self) -> None:
        pid = self.current_id()
        c = self.crop_of(pid)
        if not c:
            return
        W, H = c["oriented_size"]
        if c["mode"] == "borderless":
            new = cropmod.bordered(W, H).to_dict()
        else:
            f = db.features(self.con, pid)
            new = cropmod.propose(W, H, f["faces"], f["pets"], tuple(f["saliency"])).to_dict()
        self.con.execute("INSERT OR REPLACE INTO crops(photo_id, data, risk, manual) VALUES(?,?,?,1)",
                         (pid, json.dumps(new), new["risk"]))
        self.con.commit()
        self.reload()

    def rerank(self) -> None:
        pipeline.group_and_rank(self.con, self.ev_name)
        self.reload()

    def crops(self) -> None:
        pipeline.propose_crops(self.con, self.ev_name)
        self.reload()


def run_ui(con: sqlite3.Connection, paths: Paths, event: str, screenshot: str | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    w = Main(con, paths, event)
    w.show()
    if screenshot:
        app.processEvents()
        w.grab().save(screenshot)
        return 0
    return app.exec()
