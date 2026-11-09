"""Minimal PySide6 review UI, in two modes.

assisted (default): ranked views, scores and reasons, detector overlays, proposed crops.
manual (``review --manual``, issue #25): the model-free fallback. Capture-time order, groups
as plain stacks in capture order with no "best" marker, no scores or reasons, detector
(Haar) signals ignored, and every print bordered unless the curator picks borderless.

Keys: A approve · X reject · U clear · Ctrl+Z undo · C show group · B bordered/borderless ·
1-8 reason tag (BLINK AWAY SOFT BG CROP BETTER OTHER MISSED) · M switch mode ·
R re-rank · P propose crops (assisted only) · arrows/Home/End/PgUp/PgDn navigate.
Drag the crop rectangle in the preview to override it (stays a valid 3:2 / 2:3 crop).

Both modes read and write the same append-only decision log (ADR-0008), so switching
modes, or restarting in the other one, keeps every decision. A crop nobody reviewed
exports bordered (S2-D4, see crop.for_export).
"""

from __future__ import annotations

import json
import sqlite3
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import crop as cropmod
from . import db, pipeline
from .config import Paths

MODES = ("assisted", "manual")
GROUP_VIEW = "Group of selected"
FILTERS = {
    "assisted": ["Recommended", "All photos", "Approved", "Crop review", GROUP_VIEW, "Import problems"],
    "manual": ["All photos", "Approved", GROUP_VIEW, "Import problems"],
}
REASON_KEYS = {str(k): code for k, code in enumerate(db.REASON_CODES, 1)}


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
        t = self.target

        def box(b, color, width=2, style=Qt.PenStyle.SolidLine):
            p.setPen(QPen(QColor(color), width, style))
            p.drawRect(QRectF(t.x() + b[0] * t.width(), t.y() + b[1] * t.height(),
                              b[2] * t.width(), b[3] * t.height()))

        for b, color in self.win.overlay_boxes(pid):
            box(b, color)
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
            if self.win.mode == "manual":
                col = "#ffffff"  # no risk colours: they come from the detectors
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
            self.win.save_crop(pid, c)
            self.win._pending_manual = None
            self.win.refresh_details()


class Main(QMainWindow):
    def __init__(self, con: sqlite3.Connection, paths: Paths, event: str, mode: str = "assisted"):
        super().__init__()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.con, self.paths, self.ev_name, self.mode = con, paths, event, mode
        self._pending_manual = None
        self._icons: dict[str, QIcon] = {}  # thumbnails load once, not on every reload
        self._key_ms: list[float] = []  # per-keypress handling time, logged on close
        self.reload_count = 0
        self.resize(1500, 900)

        self.filter = QComboBox()
        self.filter.currentIndexChanged.connect(self.reload)
        self.status = QLabel()
        self.mode_btn = QPushButton()
        self.mode_btn.clicked.connect(self.toggle_mode)
        self.rerank_btn = QPushButton("Re-rank (R)")
        self.rerank_btn.clicked.connect(self.rerank)
        self.crops_btn = QPushButton("Propose crops (P)")
        self.crops_btn.clicked.connect(self.crops)
        top = QHBoxLayout()
        for w in (QLabel("View:"), self.filter, self.mode_btn, self.rerank_btn, self.crops_btn, self.status):
            top.addWidget(w)
        top.addStretch()

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(200, 200))
        self.grid.setGridSize(QSize(220, 250))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setWordWrap(True)
        self.grid.setUniformItemSizes(True)
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

        keys = [("A", lambda: self.decide("approve")), ("X", lambda: self.decide("reject")),
                ("U", lambda: self.decide("clear")), ("Ctrl+Z", self.undo),
                ("C", lambda: self.filter.setCurrentText(GROUP_VIEW)), ("B", self.toggle_border),
                ("M", self.toggle_mode), ("R", self.rerank), ("P", self.crops)]
        keys += [(k, lambda _checked=False, code=code: self.tag(code)) for k, code in REASON_KEYS.items()]
        for key, fn in keys:
            act = QAction(self)
            act.setShortcut(QKeySequence(key))
            act.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            act.triggered.connect(fn)
            self.addAction(act)
        self._group_anchor: int | None = None
        self._last_selected: int | None = None
        self._apply_mode()

    # ---------------------------------------------------------------- mode
    def _apply_mode(self, keep_view: str | None = None) -> None:
        manual = self.mode == "manual"
        self.setWindowTitle(f"Family Photo Curator POC — {self.ev_name} — "
                            f"{'manual review' if manual else 'assisted review'}")
        self.mode_btn.setText("Assisted mode (M)" if manual else "Manual mode (M)")
        self.rerank_btn.setVisible(not manual)
        self.crops_btn.setVisible(not manual)
        views = FILTERS[self.mode]
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItems(views)
        self.filter.setCurrentText(keep_view if keep_view in views else views[0])
        self.filter.blockSignals(False)
        self.reload()

    def set_mode(self, mode: str) -> None:
        """Switch modes in place. Only presentation changes; decisions are untouched."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode != self.mode:
            keep = self.filter.currentText()
            self.mode = mode
            self._apply_mode(keep)

    def toggle_mode(self) -> None:
        self.set_mode("assisted" if self.mode == "manual" else "manual")

    def filter_names(self) -> list[str]:
        return [self.filter.itemText(i) for i in range(self.filter.count())]

    def _on_select(self, *_):
        if self.filter.currentText() != GROUP_VIEW:
            self._last_selected = self.current_id()
        self.refresh_details()

    # ---------------------------------------------------------------- data
    def rows(self) -> list[sqlite3.Row]:
        view = self.filter.currentText()
        base = ("SELECT p.*, s.score, s.recommended, s.rec_order, s.reasons, c.risk, c.manual FROM photos p "
                "LEFT JOIN scores s ON s.photo_id=p.id LEFT JOIN crops c ON c.photo_id=p.id WHERE p.event=? ")
        state = db.review_state(self.con, self.ev_name)
        if view == "Recommended":
            q = base + "AND s.recommended=1 ORDER BY s.rec_order"
        elif view == "Import problems":
            q = base + "AND p.status IN ('import_error','unavailable','missing','timeout','crashed','quarantined') ORDER BY p.rel"
        elif view == "Crop review":
            q = base + "AND c.risk IN ('needs_review','crop_conflict') ORDER BY c.risk, p.capture_ts"
        elif view == GROUP_VIEW and self._group_anchor:
            order = "p.capture_ts IS NULL, p.capture_ts, p.rel" if self.mode == "manual" else "s.score DESC"
            q = (base + "AND p.id IN (SELECT photo_id FROM members WHERE group_id IN "
                 f"(SELECT group_id FROM members WHERE photo_id={int(self._group_anchor)})) ORDER BY {order}")
        else:
            q = base + "AND p.status='ok' ORDER BY p.capture_ts IS NULL, p.capture_ts, p.rel"  # undated last
        rows = self.con.execute(q, (self.ev_name,)).fetchall()
        if view == "Approved":
            rows = [r for r in rows if state.get(r["id"]) == "approve"]
        return rows

    def current_id(self) -> int | None:
        it = self.grid.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _stored_crop(self, pid) -> sqlite3.Row | None:
        return self.con.execute("SELECT data, manual FROM crops WHERE photo_id=?", (pid,)).fetchone()

    def crop_of(self, pid):
        """The crop to show. Assisted: the stored crop, reviewed or not. Manual: what export
        will print, so bordered unless the curator chose borderless (S2-D4)."""
        if not pid:
            return None
        r = self._stored_crop(pid)
        if self.mode == "assisted":
            return json.loads(r["data"]) if r else None
        p = self.con.execute("SELECT width, height FROM photos WHERE id=?", (pid,)).fetchone()
        if not p or not p["width"]:
            return None
        return cropmod.for_export(r["data"] if r else None, r["manual"] if r else 0, p["width"], p["height"])

    def overlay_boxes(self, pid) -> list[tuple[list[float], str]]:
        """Detector boxes to draw. None in manual mode: Haar false faces must not steer it."""
        if not pid or self.mode == "manual":
            return []
        f = db.features(self.con, pid)
        return ([(fc["box"], "#2ecc71" if fc["eyes_open"] else "#f39c12") for fc in f.get("faces", [])]
                + [(pt["box"], "#3498db") for pt in f.get("pets", [])])

    def _stack_sizes(self) -> dict[int, int]:
        return {r[0]: r[1] for r in self.con.execute(
            "SELECT m.photo_id, (SELECT COUNT(*) FROM members WHERE group_id=m.group_id) "
            "FROM members m JOIN groups g ON g.id=m.group_id WHERE g.event=?", (self.ev_name,))}

    def label(self, r, state) -> str:
        s = state.get(r["id"])
        mark = {"approve": "✓ ", "reject": "✗ "}.get(s, "")
        name = r["rel"].split("/")[-1]  # local display for the curator only (#13 covers output)
        if self.mode == "manual":
            n = self._stacks.get(r["id"], 1)
            second = f"stack of {n}" if n > 1 else ("" if r["status"] == "ok" else r["status"])
            return f"{mark}{name}\n{second}"
        star = "★ " if r["recommended"] else ""
        sc = f"{r['score']:.2f}" if r["score"] is not None else r["status"]
        risk = {"needs_review": " ⚠crop", "crop_conflict": " ⛔crop"}.get(r["risk"], "")
        return f"{mark}{star}{name}\n{sc}{risk}"

    def _icon(self, sha: str | None) -> QIcon | None:
        if not sha:
            return None
        if sha not in self._icons:
            thumb = self.paths.thumb / f"{sha}.jpg"
            self._icons[sha] = QIcon(str(thumb)) if thumb.exists() else QIcon()
        return self._icons[sha]

    def _paint_item(self, it: QListWidgetItem, state: dict[int, str]) -> None:
        s = state.get(it.data(Qt.ItemDataRole.UserRole))
        if s == "approve":
            it.setBackground(QColor("#d5f5e3"))
        elif s == "reject":
            it.setBackground(QColor("#f5d5d5"))
        else:
            it.setBackground(QColor(0, 0, 0, 0))

    def _update_status(self, state: dict[int, str]) -> None:
        c = {"approve": 0, "reject": 0}
        for v in state.values():
            c[v] = c.get(v, 0) + 1
        self.status.setText(f"   {self.grid.count()} shown · {c['approve']} approved · {c['reject']} rejected")

    def reload(self) -> None:
        """Rebuild the grid. Only for view and mode changes, never for a single decision."""
        self.reload_count += 1
        self._group_anchor = self._last_selected
        keep = self.current_id()
        self.grid.setUpdatesEnabled(False)
        self.grid.clear()
        state = db.review_state(self.con, self.ev_name)
        self._stacks = self._stack_sizes() if self.mode == "manual" else {}
        self._rowdata: dict[int, sqlite3.Row] = {}
        for r in self.rows():
            self._rowdata[r["id"]] = r
            it = QListWidgetItem(self.label(r, state))
            icon = self._icon(r["sha256"])
            if icon is not None:
                it.setIcon(icon)
            it.setData(Qt.ItemDataRole.UserRole, r["id"])
            self._paint_item(it, state)
            self.grid.addItem(it)
            if r["id"] == keep:
                self.grid.setCurrentItem(it)
        self.grid.setUpdatesEnabled(True)
        if self.grid.currentItem() is None and self.grid.count():
            self.grid.setCurrentRow(0)
        self._update_status(state)

    def _refresh_item(self, row: int, state: dict[int, str]) -> None:
        it = self.grid.item(row)
        pid = it.data(Qt.ItemDataRole.UserRole)
        r = self._rowdata.get(pid)
        if r is not None:
            it.setText(self.label(r, state))
        self._paint_item(it, state)

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
        ]
        if self.mode == "manual":
            lines.append(f"In a stack of {g['n']} similar photos — press C to see the stack"
                         if g and g["n"] > 1 else "Not in a stack")
            if c and c["mode"] == "borderless":
                lines.append("Print: <b>borderless</b> 4×6, your crop — drag to adjust, B for bordered")
            elif c:
                lines.append("Print: <b>bordered</b> fit, the whole photo with white borders — "
                             "B for borderless")
        else:
            st = self._stored_crop(pid)
            unreviewed = " — <i>not reviewed: exports bordered unless you press B or drag</i>"
            lines += [
                f"Score {r['score']}: {', '.join(json.loads(r['reasons'] or '[]')) or '—'}",
                (f"Group: {g['kind']}, rank {g['rank'] + 1} of {g['n']}{' (ambiguous)' if g['ambiguous'] else ''}"
                 " — press C to compare" if g else "Unique photo (no group)"),
                (f"Crop: {c['mode']} {c['target']} → <b>{c['risk']}</b> ({'; '.join(c['reasons'])})"
                 f"{'' if st and st['manual'] else unreviewed}" if c else "Crop: none (approve, then P)"),
            ]
        tags = db.reasons(self.con, pid)
        if tags:
            lines.append(f"Your tags: {', '.join(tags)}")
        if r["error"]:
            lines.append(f"<span style='color:#c0392b'>Error: {r['error']}</span>")
        if r["has_gps"]:
            lines.append("Has GPS (will be stripped on export)")
        self.details.setHtml("<br>".join(lines))

    # ---------------------------------------------------------------- actions
    def _timed(self, t0: float) -> None:
        self._key_ms.append((time.perf_counter() - t0) * 1000)

    def key_timing(self) -> dict:
        """Aggregate per-key handling time, in ms. Counts and numbers only."""
        ms = sorted(self._key_ms)
        if not ms:
            return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
        return {"count": len(ms), "p50_ms": ms[len(ms) // 2],
                "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))], "max_ms": ms[-1]}

    def decide(self, action: str) -> None:
        """Append a decision, update the one tile in place, advance. No grid rebuild."""
        pid = self.current_id()
        if not pid:
            return
        t0 = time.perf_counter()
        db.decide(self.con, pid, action)
        state = db.review_state(self.con, self.ev_name)
        row = self.grid.currentRow()
        if self.filter.currentText() == "Approved" and state.get(pid) != "approve":
            self.grid.takeItem(row)
            self.grid.setCurrentRow(min(row, self.grid.count() - 1))
        else:
            self._refresh_item(row, state)
            self.grid.setCurrentRow(min(row + 1, self.grid.count() - 1))
        self._update_status(state)
        self._timed(t0)

    def undo(self) -> None:
        t0 = time.perf_counter()
        pid = db.undo_last(self.con, self.ev_name)
        state = db.review_state(self.con, self.ev_name)
        rows = {self.grid.item(i).data(Qt.ItemDataRole.UserRole): i for i in range(self.grid.count())}
        if self.filter.currentText() == "Approved":
            self.reload()  # the undone photo may need to come back into this view
            rows = {self.grid.item(i).data(Qt.ItemDataRole.UserRole): i for i in range(self.grid.count())}
        elif pid in rows:
            self._refresh_item(rows[pid], state)
        if pid in rows:
            self.grid.setCurrentRow(rows[pid])
        self._update_status(state)
        self.refresh_details()
        self._timed(t0)

    def tag(self, code: str) -> None:
        """Reason code (S2-D3): an append-only event on the current photo. Does not advance."""
        pid = self.current_id()
        if not pid:
            return
        t0 = time.perf_counter()
        db.decide(self.con, pid, f"reason:{code}")
        self.refresh_details()
        self._timed(t0)

    def save_crop(self, pid: int, c: dict) -> None:
        self.con.execute("INSERT OR REPLACE INTO crops(photo_id, data, risk, manual) VALUES(?,?,?,1)",
                         (pid, json.dumps(c), c["risk"]))
        self.con.commit()

    def toggle_border(self) -> None:
        pid = self.current_id()
        c = self.crop_of(pid)
        if not c:
            return
        t0 = time.perf_counter()
        W, H = c["oriented_size"]
        if c["mode"] == "borderless":
            new = cropmod.bordered(W, H).to_dict()
        elif self.mode == "manual":
            # Model-free: a plain centred 4x6 crop the curator drags into place.
            new = cropmod.propose(W, H, [], [], (0.5, 0.5)).to_dict()
            new["reasons"] = ["borderless chosen by you; drag to adjust"]
        else:
            f = db.features(self.con, pid)
            new = cropmod.propose(W, H, f["faces"], f["pets"], tuple(f["saliency"])).to_dict()
        self.save_crop(pid, new)
        if self.grid.currentItem() is not None and pid in self._rowdata:
            self._rowdata[pid] = self.con.execute(
                "SELECT p.*, s.score, s.recommended, s.rec_order, s.reasons, c.risk, c.manual FROM photos p "
                "LEFT JOIN scores s ON s.photo_id=p.id LEFT JOIN crops c ON c.photo_id=p.id WHERE p.id=?",
                (pid,)).fetchone()
            self._refresh_item(self.grid.currentRow(), db.review_state(self.con, self.ev_name))
        self.preview.update()
        self.refresh_details()
        self._timed(t0)

    def rerank(self) -> None:
        if self.mode == "manual":
            return  # ranking is a model signal
        pipeline.group_and_rank(self.con, self.ev_name)
        self.reload()

    def crops(self) -> None:
        if self.mode == "manual":
            return  # crop proposals use the face detector
        pipeline.propose_crops(self.con, self.ev_name)
        self.reload()

    def closeEvent(self, ev):  # noqa: N802
        t = self.key_timing()
        if t["count"]:  # aggregate timing for the RDP check on the VM (#25)
            db.record_timing(self.con, self.ev_name, "ui_key_p95", t["p95_ms"] / 1000)
            db.record_timing(self.con, self.ev_name, "ui_key_max", t["max_ms"] / 1000)
        super().closeEvent(ev)


def run_ui(con: sqlite3.Connection, paths: Paths, event: str, screenshot: str | None = None,
           mode: str = "assisted") -> int:
    app = QApplication.instance() or QApplication([])
    w = Main(con, paths, event, mode=mode)
    w.show()
    if screenshot:
        app.processEvents()
        w.grab().save(screenshot)
        return 0
    return app.exec()
