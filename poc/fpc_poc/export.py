"""Print export worker: decode via the contract, crop, sRGB, strip metadata, atomic write, verify.

Results carry codes, never message text (issue #13): warnings LOW_RES; problems RATIO,
SIZE, NO_ICC, PROFILE_NOT_SRGB, METADATA_PRESENT; error code E_SOURCE_CHANGED.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image, ImageCms

from . import config
from .crop import target_ratio
from .decode import load_oriented, to_srgb
from .inventory import current_state, is_openable

SRGB_ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()

# JPEG markers a print-ready file may contain: SOI, APP0 (JFIF), APP2 (ICC), DQT, SOFn, DHT,
# DRI, SOS, RSTn, EOI. Anything else (COM, APP1 EXIF/XMP, APP13 IPTC, other APPn) is metadata.
ALLOWED_MARKERS = frozenset(
    {0xD8, 0xE0, 0xE2, 0xDB, 0xC4, 0xDD, 0xDA, 0xD9}
    | set(range(0xD0, 0xD8))
    | (set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC})
)
_APP_IDS = {0xE0: b"JFIF\x00", 0xE2: b"ICC_PROFILE\x00"}


def jpeg_markers(data: bytes) -> list[int]:
    """Walk JPEG segments from SOI to EOI. Data after EOI is reported as marker -1.
    An APP0/APP2 segment with an unexpected identifier is reported as -2."""
    if data[:2] != b"\xff\xd8":
        return [-3]
    out, i, n = [0xD8], 2, len(data)
    while i < n:
        if data[i] != 0xFF:
            return [*out, -3]
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        m = data[i]
        i += 1
        if m == 0xD9:
            out.append(m)
            if data[i:].strip(b"\x00"):
                out.append(-1)
            return out
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            out.append(m)
            continue
        seg_len = int.from_bytes(data[i:i + 2], "big")
        body = data[i + 2:i + seg_len]
        out.append(-2 if m in _APP_IDS and not body.startswith(_APP_IDS[m]) else m)
        i += seg_len
        if m == 0xDA:  # entropy-coded data runs to the next real marker
            while i + 1 < n and not (data[i] == 0xFF and data[i + 1] not in (0x00, *range(0xD0, 0xD8))):
                i += 1
    return [*out, -3]  # no EOI


def task_export(path: str, crop: dict, out_path: str, expected_sha: str) -> dict:
    out = Path(out_path)
    if out.exists():  # never overwrite; validate what is there
        v = verify(out, crop)
        return {"status": "skipped_existing", **v}
    if not is_openable(current_state(path)):
        return {"status": "unavailable"}
    img, info = load_oriented(path)
    w, h = img.size
    if [w, h] != list(crop["oriented_size"]):
        return {"status": "error", "error_class": "SourceChanged", "error_code": "E_SOURCE_CHANGED"}
    rgb = to_srgb(img, info["icc"])
    long_, short = config.EXPORT_LONG, config.EXPORT_SHORT
    tw, th = (long_, short) if w >= h else (short, long_)
    warnings = []
    if crop["mode"] == "bordered":
        canvas = Image.new("RGB", (tw, th), (255, 255, 255))
        fit = rgb.copy()
        fit.thumbnail((tw, th), Image.Resampling.LANCZOS)
        canvas.paste(fit, ((tw - fit.width) // 2, (th - fit.height) // 2))
        final = canvas
        short_src = min(w, h)
    else:
        x, y, cw, ch = crop["px"]
        short_src = min(cw, ch)
        final = rgb.crop((x, y, x + cw, y + ch)).resize((tw, th), Image.Resampling.LANCZOS)
    if short_src < config.MIN_SHORT_SIDE_PX:
        warnings.append("LOW_RES")
    # crop()/resize() copy the source's info dict (COM comment, XMP, EXIF bytes) when the
    # source had no ICC profile, and Pillow's JPEG writer falls back to it. Drop it all.
    final.info = {}
    tmp = out.with_suffix(".tmp")
    try:
        # Write, flush and fsync through one writable handle: on Windows os.fsync calls
        # _commit, which fails on a handle opened read-only.
        with open(tmp, "wb") as f:
            final.save(f, "JPEG", quality=95, subsampling=0, icc_profile=SRGB_ICC,
                       exif=b"", comment=b"")  # explicit: no EXIF, no comment
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
    finally:
        if tmp.exists():  # a failed export removes its own partial file
            tmp.unlink()
    return {"status": "done", "warnings": warnings, **verify(out, crop)}


def verify(out: Path, crop: dict) -> dict:
    segments_ok = set(jpeg_markers(out.read_bytes())) <= ALLOWED_MARKERS
    with Image.open(out) as im:
        im.load()
        w, h = im.size
        exif = im.getexif()
        icc = im.info.get("icc_profile")
    _, r = target_ratio(*crop["oriented_size"])
    problems = []
    if abs(w / h - r) > 0.002:
        problems.append("RATIO")
    if sorted((w, h)) != [config.EXPORT_SHORT, config.EXPORT_LONG]:
        problems.append("SIZE")
    if not icc:
        problems.append("NO_ICC")
    else:
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
        if "srgb" not in desc.lower():
            problems.append("PROFILE_NOT_SRGB")
    if exif.get_ifd(0x8825) or len(exif) or not segments_ok:
        problems.append("METADATA_PRESENT")
    return {"verified": not problems, "problems": problems, "size": [w, h]}
