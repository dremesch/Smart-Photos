"""Decoding contract + crash-isolated subprocess pool (ADR-0003, ADR-0006).

Everything that touches source bytes runs in worker processes via `run_isolated`.
Workers write derivatives to the cache themselves, so pixels never cross IPC.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageCms, ImageOps

from . import config, guard
from .inventory import current_state, is_openable

_HEIF_REGISTERED = False

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_ORIENTATION = 274
TAG_MAKE, TAG_MODEL = 271, 272
TAG_DTO, TAG_OFFSET_DTO = 36867, 36881
TAG_DT = 306


def _register_heif() -> None:
    global _HEIF_REGISTERED
    if not _HEIF_REGISTERED:
        import pillow_heif

        pillow_heif.register_heif_opener()
        _HEIF_REGISTERED = True


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_oriented(path: str) -> tuple[Image.Image, dict]:
    """THE decoding contract. Orientation applied exactly once, per format.

    JPEG: apply EXIF Orientation. HEIC: libheif already applied irot/imir -> do NOT
    apply EXIF Orientation again (double-rotation bug).
    """
    Image.MAX_IMAGE_PIXELS = config.MAX_PIXELS
    ext = Path(path).suffix.lower()
    if ext in (".heic", ".heif"):
        _register_heif()
    img = Image.open(path)
    img.load()
    exif = img.getexif()
    raw_orientation = exif.get(TAG_ORIENTATION, 1)
    if ext in (".heic", ".heif"):
        rule = "heif-container-v1"
        oriented = img
    else:
        rule = "jpeg-exif-v1"
        oriented = ImageOps.exif_transpose(img)
    info = {
        "fmt": img.format,
        "orientation_rule": rule,
        "raw_exif_orientation": raw_orientation,
        "icc": img.info.get("icc_profile"),
        "nclx": bool(img.info.get("nclx_profile")),
        "exif": exif,
        "mode": img.mode,
    }
    return oriented, info


def to_srgb(img: Image.Image, icc: bytes | None) -> Image.Image:
    """Color-manage to sRGB. POC: ICC only; HEIF nclx-only files are assumed sRGB (TODO)."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.mode == "RGBA":
        img = img.convert("RGB")
    if not icc:
        return img
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(
            img, src, dst, renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, outputMode="RGB"
        )
    except Exception:  # noqa: BLE001 - POC: fall back to raw pixels
        return img


def _exif_meta(exif) -> dict:
    sub = exif.get_ifd(EXIF_IFD) if exif else {}
    dto = sub.get(TAG_DTO) or None
    src = "exif_original" if dto else None
    if not dto and exif.get(TAG_DT):
        dto, src = exif.get(TAG_DT), "exif_datetime"
    make = (exif.get(TAG_MAKE) or "").strip("\x00 ")
    model = (exif.get(TAG_MODEL) or "").strip("\x00 ")
    return {
        "capture_ts": str(dto).strip("\x00") if dto else None,
        "utc_offset": (sub.get(TAG_OFFSET_DTO) or None),
        "ts_source": src,
        "device": f"{make} {model}".strip() or None,
        "has_gps": bool(exif.get_ifd(GPS_IFD)) if exif else False,
    }


# ---------------------------------------------------------------- worker tasks


def task_ingest(path: str, cache_root: str) -> dict:
    """Pass-2 work for one file: re-check cloud state, hash, decode once, write derivatives."""
    if os.environ.get("FPC_POC_CRASH_MARKER") and os.environ["FPC_POC_CRASH_MARKER"] in path:
        os.abort()  # simulate a native decoder segfault
    state = current_state(path)
    if not is_openable(state):
        return {"status": "unavailable", "cloud_state": state}
    sha = sha256_file(path)
    cache = Path(cache_root)
    meta_p = cache / "meta" / f"{sha}.json"
    if meta_p.exists():  # content-addressed cache hit: no decode
        meta = json.loads(meta_p.read_text())
        meta["cache_hit"] = True
        return meta
    img, info = load_oriented(path)
    meta = {
        "status": "ok",
        "sha256": sha,
        "width": img.width,
        "height": img.height,
        "fmt": info["fmt"],
        "orientation_rule": info["orientation_rule"],
        "raw_exif_orientation": info["raw_exif_orientation"],
        "has_icc": bool(info["icc"]),
        "nclx": info["nclx"],
        **_exif_meta(info["exif"]),
    }
    rgb = to_srgb(img, info["icc"])
    d = rgb.copy()
    d.thumbnail((config.DERIV_EDGE, config.DERIV_EDGE), Image.Resampling.LANCZOS)
    d.save(cache / "deriv" / f"{sha}.jpg", quality=90)
    d.thumbnail((config.THUMB_EDGE, config.THUMB_EDGE), Image.Resampling.LANCZOS)
    d.save(cache / "thumb" / f"{sha}.jpg", quality=85)
    meta_p.write_text(json.dumps(meta))
    meta["cache_hit"] = False
    return meta


# ---------------------------------------------------------------- isolation pool


def worker_error(e: BaseException) -> dict:
    """A per-item failure: the exception class and an error code, never str(e).

    Third-party messages (Pillow, pillow-heif, OSError) routinely embed the source path.
    """
    return {"status": "error", "error_class": type(e).__name__, "error_code": "E_WORKER"}


def run_isolated(
    fn: Callable[..., dict],
    jobs: list[tuple[Any, tuple]],
    workers: int | None = None,
    on_result: Callable[[Any, dict], None] | None = None,
) -> dict[Any, dict]:
    """Run fn(*args) for each (key, args) in subprocesses.

    A hard crash (segfault/abort) breaks the pool; we then re-run every unfinished job
    alone in a fresh single-worker pool to pinpoint the crasher. Crashers get
    {"status": "crashed"}; all other jobs still complete.
    """
    workers = workers or max(1, min(4, (os.cpu_count() or 2) - 1))
    ctx = get_context("spawn")
    results: dict[Any, dict] = {}

    def record(k, r):
        results[k] = r
        if on_result:
            on_result(k, r)

    pending = list(jobs)
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=guard.block) as ex:
            futs = {ex.submit(fn, *args): key for key, args in pending}
            for f in as_completed(futs):
                k = futs[f]
                try:
                    record(k, f.result())
                except BrokenProcessPool:
                    raise
                except Exception as e:  # noqa: BLE001 - per-item failure
                    record(k, worker_error(e))
    except BrokenProcessPool:
        for key, args in pending:
            if key in results:
                continue
            try:
                with ProcessPoolExecutor(max_workers=1, mp_context=ctx, initializer=guard.block) as ex1:
                    record(key, ex1.submit(fn, *args).result())
            except BrokenProcessPool:
                record(key, {"status": "crashed", "error_class": "BrokenProcessPool",
                             "error_code": "E_CRASH"})
            except Exception as e:  # noqa: BLE001
                record(key, worker_error(e))
    return results
