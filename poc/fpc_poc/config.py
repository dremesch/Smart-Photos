"""Paths and knobs. Everything here is a POC guess, not a calibrated value."""

from __future__ import annotations

import os
import sys
from pathlib import Path

SUPPORTED = {".jpg", ".jpeg", ".heic", ".heif"}
EXCLUDED = {".mov", ".mp4", ".m4v", ".png", ".dng", ".gif", ".aae", ".webp", ".tif", ".tiff"}

DERIV_EDGE = 1024  # analysis derivative long edge
THUMB_EDGE = 256
MAX_PIXELS = 120_000_000  # decompression-bomb cap (48 MP iPhone = 48M)

# grouping (uncalibrated)
TIME_WINDOW_S = 60
BURST_WINDOW_S = 10
PHASH_STRONG = 6
PHASH_LOOSE = 14
EMB_COS_MIN = 0.92

# crop / export
SAFE_MARGIN = 0.04
EXPORT_LONG, EXPORT_SHORT = 1800, 1200
MIN_SHORT_SIDE_PX = 1200


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "FamilyPhotoCurator-POC"
    return Path.home() / ".local" / "share" / "fpc-poc"


class Paths:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / "poc.db"
        self.deriv = root / "cache" / "deriv"
        self.thumb = root / "cache" / "thumb"
        self.meta = root / "cache" / "meta"
        for p in (self.deriv, self.thumb, self.meta):
            p.mkdir(parents=True, exist_ok=True)
