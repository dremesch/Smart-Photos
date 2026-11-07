"""Print export worker: decode via the contract, crop, sRGB, strip metadata, atomic write, verify."""

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
        return {"status": "error", "error": "source changed since crop (oriented size differs)"}
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
        warnings.append(f"low resolution: {short_src}px short side")
    tmp = out.with_suffix(".tmp")
    final.save(tmp, "JPEG", quality=95, subsampling=0, icc_profile=SRGB_ICC)  # no exif => GPS stripped
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, out)
    return {"status": "done", "warnings": warnings, **verify(out, crop)}


def verify(out: Path, crop: dict) -> dict:
    with Image.open(out) as im:
        im.load()
        w, h = im.size
        exif = im.getexif()
        icc = im.info.get("icc_profile")
    _, r = target_ratio(*crop["oriented_size"])
    problems = []
    if abs(w / h - r) > 0.002:
        problems.append(f"ratio {w}x{h}")
    if sorted((w, h)) != [config.EXPORT_SHORT, config.EXPORT_LONG]:
        problems.append(f"size {w}x{h}")
    if not icc:
        problems.append("no ICC profile")
    else:
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
        if "srgb" not in desc.lower():
            problems.append(f"profile {desc!r}")
    if exif.get_ifd(0x8825) or len(exif):
        problems.append("metadata present")
    return {"verified": not problems, "problems": problems, "size": [w, h]}
