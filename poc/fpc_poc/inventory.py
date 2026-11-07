"""Two-pass inventory, pass 1: directory attributes only. Never opens a file (ADR-0004)."""

from __future__ import annotations

import os
import stat as stat_mod
from dataclasses import dataclass
from pathlib import Path

from . import config

# Windows file attribute flags (winnt.h)
RECALL_ON_DATA_ACCESS = 0x00400000
RECALL_ON_OPEN = 0x00040000
OFFLINE = 0x00001000
PINNED = 0x00080000
UNPINNED = 0x00100000


def cloud_state(attrs: int | None) -> str:
    """Classify from st_file_attributes. None (non-Windows) => local."""
    if attrs is None:
        return "local"
    if attrs & (RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE):
        return "online_only"
    if attrs & UNPINNED:
        return "local_unpinned"
    return "local"


def is_openable(state: str) -> bool:
    return state in ("local", "local_unpinned")


def current_state(path: str) -> str:
    """Re-check right before reading. os.stat reads metadata only; it does not hydrate."""
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    return cloud_state(getattr(st, "st_file_attributes", None))


@dataclass
class Entry:
    path: str
    rel: str
    ext: str
    size: int
    mtime: float
    cloud_state: str
    kind: str  # photo | excluded | unknown
    companion: str | None = None


def scan(roots: list[Path]) -> list[Entry]:
    out: list[Entry] = []
    for root in roots:
        root = root.resolve()
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for de in it:
                    if de.is_dir(follow_symlinks=False):
                        stack.append(Path(de.path))
                        continue
                    if not de.is_file(follow_symlinks=False):
                        continue
                    st = de.stat(follow_symlinks=False)  # from the directory entry on Windows
                    if not stat_mod.S_ISREG(st.st_mode):
                        continue
                    ext = Path(de.name).suffix.lower()
                    kind = (
                        "photo" if ext in config.SUPPORTED
                        else "excluded" if ext in config.EXCLUDED
                        else "unknown"
                    )
                    out.append(Entry(
                        path=de.path, rel=str(Path(de.path).relative_to(root)), ext=ext,
                        size=st.st_size, mtime=st.st_mtime,
                        cloud_state=cloud_state(getattr(st, "st_file_attributes", None)),
                        kind=kind,
                    ))
    # Live Photo pairing by stem in the same folder (POC; real version uses ContentIdentifier)
    movs = {(str(Path(e.path).with_suffix("")).lower()): e.path for e in out if e.ext == ".mov"}
    for e in out:
        if e.kind == "photo":
            e.companion = movs.get(str(Path(e.path).with_suffix("")).lower())
    return out
