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


def _seconds(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        return default
    return v if v > 0 else default


# Worker deadlines (D4, S2-D8, issue #11). The parent kills a worker still busy after
# deadline + grace, respawns it and records a timeout. Each can be overridden by env var.
DECODE_DEADLINE_S = _seconds("FPC_POC_DECODE_DEADLINE_S", 30.0)  # ingest: hash, decode, derivatives
FEATURE_DEADLINE_S = _seconds("FPC_POC_FEATURE_DEADLINE_S", 60.0)  # analyze: features per derivative
EXPORT_DEADLINE_S = _seconds("FPC_POC_EXPORT_DEADLINE_S", 60.0)  # full decode, crop, encode, verify
KILL_GRACE_S = _seconds("FPC_POC_KILL_GRACE_S", 5.0)
WORKER_START_S = 120.0  # a worker that is not ready by then means a broken install, not a bad file
STRIKES_TO_QUARANTINE = 2  # timeouts or crashes per item and decoder version

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
