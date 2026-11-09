"""Decoding contract + crash- and hang-isolated subprocess pool (ADR-0003, ADR-0006, S2-D8).

Everything that touches source bytes runs in worker processes via `run_isolated`.
Workers write derivatives to the cache themselves, so pixels never cross IPC.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
from collections import deque
from itertools import count
from multiprocessing import get_context
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageCms, ImageOps

from . import config, quiet
from .inventory import current_state, is_openable

_HEIF_REGISTERED = False

# Bump when decoding behaviour changes: quarantined items get a fresh chance (issue #11).
DECODER_VERSION = "poc-decode-1"

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
    if os.environ.get("FPC_POC_HANG_MARKER") and os.environ["FPC_POC_HANG_MARKER"] in path:
        time.sleep(3600)  # simulate a decoder stuck in a loop (POC only, D5; never in src/)
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

_STATUS_OF_CODE = {"E_TIMEOUT": "timeout", "E_CRASH": "crashed", "E_QUARANTINED": "quarantined"}


def worker_error(e: BaseException | str, code: str = "E_WORKER") -> dict:
    """THE constructor for a per-item failure: the exception class (or a fixed class name for
    pool events) and an error code, never str(e). The status follows from the code.

    Third-party messages (Pillow, pillow-heif, OSError) routinely embed the source path.
    """
    cls = e if isinstance(e, str) else type(e).__name__
    return {"status": _STATUS_OF_CODE.get(code, "error"), "error_class": cls, "error_code": code}


def serve(rx, tx) -> None:
    """Worker loop (started by ``quiet.worker_main``): announce readiness, then run one task
    at a time until the parent sends None or goes away."""
    tx.send(("ready", None, None))
    while True:
        try:
            msg = rx.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        seq, fn, args = msg
        try:
            out = fn(*args)
        except Exception as e:  # noqa: BLE001 - per-item failure
            out = worker_error(e)
        try:
            tx.send(("done", seq, out))
        except Exception as e:  # noqa: BLE001 - e.g. an unpicklable result
            tx.send(("done", seq, worker_error(e)))


class _Worker:
    """One long-lived process with its own pipe pair and at most one task in flight.

    Two one-way pipes, not one duplex pipe: on POSIX a duplex Pipe is a socketpair, which
    the network guard (installed in the main process) refuses; a one-way Pipe is os.pipe.
    """

    def __init__(self, ctx):
        from . import quiet

        self.conn, child_tx = ctx.Pipe(duplex=False)  # results: worker -> parent
        child_rx, self.tx = ctx.Pipe(duplex=False)  # tasks: parent -> worker
        self.proc = ctx.Process(target=quiet.worker_main, args=(child_rx, child_tx), daemon=True)
        self.proc.start()
        child_rx.close()
        child_tx.close()
        self.spawned = time.monotonic()
        self.ready = False
        self.task: tuple[int, Any, float] | None = None  # (seq, key, started)

    def kill(self) -> None:
        """Process.kill(): SIGKILL on POSIX, TerminateProcess on Windows. Closing our end of
        the pipe means nothing the dead worker wrote can be read afterwards."""
        if self.proc.is_alive():
            self.proc.kill()
        self.proc.join(timeout=10)
        self.conn.close()
        self.tx.close()

    def stop(self) -> None:
        try:
            self.tx.send(None)
        except (OSError, ValueError):
            pass
        self.proc.join(timeout=2)
        self.kill()


class WorkerStartError(RuntimeError):
    """A worker never became ready: the install is broken, no single file is to blame."""


def run_isolated(
    fn: Callable[..., Any],
    jobs: list[tuple[Any, tuple]],
    workers: int | None = None,
    on_result: Callable[[Any, Any], None] | None = None,
    *,
    on_start: Callable[[Any], None] | None = None,
    deadline: float | None = None,
    grace: float | None = None,
) -> dict[Any, Any]:
    """Run fn(*args) for each (key, args) in long-lived subprocess workers (S2-D8).

    Each worker has its own pipe and one task in flight. ``on_start(key)`` runs just before
    a task is sent (the pipeline writes its attempt-started row there). A worker still busy
    ``deadline + grace`` seconds after dispatch is killed and replaced; its item gets
    ``worker_error("DeadlineExceeded", "E_TIMEOUT")``. A worker that dies mid-task gives
    ``worker_error("WorkerDied", "E_CRASH")``. Every other item still completes.

    ``on_result`` runs in this process. If it raises, the exception propagates (after all
    workers are killed); it is never turned into an item error or called again for the item.
    """
    if not jobs:
        return {}
    from . import config

    workers = workers or max(1, min(4, (os.cpu_count() or 2) - 1))
    deadline = config.DECODE_DEADLINE_S if deadline is None else deadline
    grace = config.KILL_GRACE_S if grace is None else grace
    limit = deadline + grace
    ctx = get_context("spawn")
    results: dict[Any, Any] = {}
    pending = deque(jobs)
    seqs = count()
    pool: list[_Worker] = []

    def finish(key, res) -> None:
        results[key] = res
        if on_result:
            on_result(key, res)

    def replace(w: _Worker) -> None:
        w.kill()  # reaped before its replacement starts: the worker count stays bounded
        pool.remove(w)
        if len(pending) > sum(x.task is None for x in pool):
            pool.append(_Worker(ctx))

    try:
        for _ in range(min(workers, len(jobs))):
            pool.append(_Worker(ctx))  # one at a time: a failed start still cleans up the rest
        while pending or any(w.task for w in pool):
            for w in list(pool):  # dispatch to idle, ready workers
                if w.ready and w.task is None and pending:
                    key, args = pending.popleft()
                    if on_start:
                        on_start(key)
                    seq = next(seqs)
                    try:
                        w.tx.send((seq, fn, args))
                    except (OSError, ValueError):  # the worker died between tasks
                        replace(w)
                        finish(key, worker_error("WorkerDied", "E_CRASH"))
                        continue
                    w.task = (seq, key, time.monotonic())

            now = time.monotonic()
            due = [w.task[2] + limit for w in pool if w.task]
            due += [w.spawned + config.WORKER_START_S for w in pool if not w.ready]
            timeout = max(0.0, min(due) - now) if due else None
            ready = set(wait([w.conn for w in pool] + [w.proc.sentinel for w in pool], timeout))

            for w in list(pool):
                if w.conn not in ready and w.proc.sentinel not in ready:
                    continue
                dead = False
                try:
                    while w.conn.poll():
                        kind, seq, res = w.conn.recv()
                        if kind == "ready":
                            w.ready = True
                        elif w.task and seq == w.task[0]:
                            key, w.task = w.task[1], None
                            finish(key, res)
                        # anything else is a stale reply: ignored
                except (EOFError, OSError):
                    dead = True
                if dead or not w.proc.is_alive():
                    task, w.task = w.task, None
                    if not w.ready:
                        raise WorkerStartError
                    replace(w)
                    if task:
                        finish(task[1], worker_error("WorkerDied", "E_CRASH"))

            now = time.monotonic()
            for w in list(pool):
                if w.task and now >= w.task[2] + limit:
                    task, w.task = w.task, None
                    replace(w)
                    finish(task[1], worker_error("DeadlineExceeded", "E_TIMEOUT"))
                elif not w.ready and now >= w.spawned + config.WORKER_START_S:
                    raise WorkerStartError
    finally:
        for w in pool:
            if w.task:
                w.kill()
            else:
                w.stop()
    return results
