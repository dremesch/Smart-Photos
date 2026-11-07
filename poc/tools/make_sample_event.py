"""Generate a synthetic 'event' that exercises every risky path in the POC.

Uses scikit-image sample images (public domain / permissive). Never use real
family photos as committed fixtures.

    python poc/tools/make_sample_event.py OUT_DIR [--canary TOKEN]

With --canary, an extra day folder is planted whose folder name, file names,
EXIF (Make, Model, ImageDescription, Artist, Software), MakerNote, XMP packet and
ICC profile description all carry TOKEN. The leak tests (and the pre-USB gate)
assert that TOKEN never reaches any agent-visible or shareable output.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image, ImageCms, ImageDraw, ImageFilter
from skimage import data

W, H = 2016, 1512  # 3 MP 4:3 keeps generation fast
DEV = ("Apple", "iPhone 15 Pro")
DEV2 = ("Apple", "iPhone 13")
GPS = {1: "N", 2: (35.0, 39.0, 29.0), 3: "E", 4: (139.0, 42.0, 5.0)}


def exif_bytes(ts: datetime | None, dev=DEV, orientation: int = 1, gps: bool = True) -> bytes:
    ex = Image.Exif()
    ex[271], ex[272] = dev
    ex[274] = orientation
    if ts:
        ex[306] = ts.strftime("%Y:%m:%d %H:%M:%S")
        sub = ex.get_ifd(0x8769)
        sub[36867] = ts.strftime("%Y:%m:%d %H:%M:%S")
        sub[36881] = "+09:00"
    if gps:
        ex.get_ifd(0x8825).update(GPS)
    return ex.tobytes()


def bg(seed: int, size=(W, H)) -> Image.Image:
    src = [data.coffee(), data.rocket(), data.hubble_deep_field(), data.astronaut()][seed % 4]
    im = Image.fromarray(src).resize(size, Image.Resampling.BICUBIC)
    return im.filter(ImageFilter.GaussianBlur(25))


def face(size: int) -> Image.Image:
    a = Image.fromarray(data.astronaut()).crop((60, 0, 380, 320))  # head + shoulders
    return a.resize((size, size), Image.Resampling.LANCZOS)


def close_eyes(f: Image.Image) -> Image.Image:
    """Paint over the eyes with nearby skin tone so eye detection fails (a 'blink')."""
    f = f.copy()
    s = f.width / 320
    d = ImageDraw.Draw(f)
    skin = f.getpixel((int(205 * s), int(160 * s)))
    for cx in (170, 225):  # approximate eye centres in the crop
        d.ellipse([(cx - 22) * s, (95 - 9) * s, (cx + 22) * s, (95 + 9) * s], fill=skin)
    return f


def portrait_scene(seed: int, fx: int, fy: int, fsize=900, eyes=True, blur=0.0, dark=1.0,
                   size=(W, H)) -> Image.Image:
    im = bg(seed, size)
    f = face(fsize)
    if not eyes:
        f = close_eyes(f)
    im.paste(f, (fx, fy))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    if dark != 1.0:
        im = Image.eval(im, lambda v: int(v * dark))
    return im


def scenery(name: str, size=(W, H)) -> Image.Image:
    src = getattr(data, name)()
    if src.ndim == 2:
        src = np.stack([src] * 3, -1)
    return Image.fromarray(src[..., :3]).resize(size, Image.Resampling.LANCZOS)


def save_jpg(im: Image.Image, p: Path, ts, **kw) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    im.save(p, quality=92, exif=exif_bytes(ts, **kw))


def canary_icc(token: str) -> bytes:
    """An sRGB ICC profile whose description is patched to carry the canary token.

    The colorants are untouched, so colour management still works; only the 13-character
    v4 'mluc' description ("sRGB built-in", UTF-16BE) is replaced.
    """
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    old = "sRGB built-in".encode("utf-16-be")
    new = (token + " icc-desc")[:13].ljust(13).encode("utf-16-be")
    assert icc.count(old) == 1 and len(new) == len(old)
    return icc.replace(old, new)


def canary_exif(token: str, ts: datetime) -> bytes:
    ex = Image.Exif()
    ex[271], ex[272] = token, f"{token} model"
    ex[274] = 1
    ex[270] = f"{token} description"  # ImageDescription
    ex[305] = f"{token} software"  # Software
    ex[315] = f"{token} artist"  # Artist
    ex[306] = ts.strftime("%Y:%m:%d %H:%M:%S")
    sub = ex.get_ifd(0x8769)
    sub[36867] = ts.strftime("%Y:%m:%d %H:%M:%S")
    sub[36881] = "+09:00"
    sub[0x927C] = f"Apple iOS\x00{token} makernote".encode()  # MakerNote (UNDEFINED)
    ex.get_ifd(0x8825).update(GPS)
    return ex.tobytes()


def canary_xmp(token: str) -> bytes:
    return (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">'
        f'<dc:description>{token} xmp</dc:description>'
        f'<photoshop:City>{token} city</photoshop:City>'
        '<photoshop:DateCreated>2024-04-05T11:00:00+09:00</photoshop:DateCreated>'
        '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    ).encode()


def plant_canaries(out: Path, token: str) -> list[Path]:
    """Plant `token` in a folder name, file names, EXIF, MakerNote, XMP and ICC description."""
    day = out / f"Day4_{token}"
    day.mkdir(parents=True, exist_ok=True)
    t4 = datetime(2024, 4, 5, 11, 0, 0)
    icc, xmp = canary_icc(token), canary_xmp(token)
    written = []
    scenes = [portrait_scene(1, 1100, 350, fsize=700), scenery("camera"), scenery("moon")]
    for k, im in enumerate(scenes):
        p = day / f"IMG_{token}_{4001 + k}.JPG"
        im.save(p, quality=92, exif=canary_exif(token, t4 + timedelta(hours=k)),
                icc_profile=icc, xmp=xmp)
        written.append(p)
    p = day / f"IMG_{token}_4010.HEIC"
    pillow_heif.from_pillow(scenery("coins")).save(
        p, quality=90, exif=canary_exif(token, t4 + timedelta(hours=5)), xmp=xmp, icc_profile=icc)
    written.append(p)
    good = written[0].read_bytes()
    p = day / f"IMG_{token}_4099_corrupt.JPG"  # decoder error whose message would carry the path
    p.write_bytes(good[: len(good) // 3])
    written.append(p)
    return written


def main(out: Path, canary: str | None = None) -> None:
    if out.exists():
        shutil.rmtree(out)
    d1, d2, d3 = (out / f"Day{i}" for i in (1, 2, 3))
    t1 = datetime(2024, 4, 2, 10, 0, 0)

    # Burst: 5 frames, 1 s apart. Frame 3 is the sharp, eyes-open winner.
    burst = [
        dict(blur=6), dict(blur=2.5), dict(), dict(eyes=False), dict(dark=0.55),
    ]
    for k, kw in enumerate(burst):
        im = portrait_scene(0, 560 + k * 6, 300, **kw)
        save_jpg(im, d1 / f"IMG_{1001 + k}.JPG", t1 + timedelta(seconds=k))

    # Same scene from a second phone 3 s later: same near-duplicate group, never the same burst
    save_jpg(portrait_scene(0, 600, 300), d1 / "IMG_2001.JPG", t1 + timedelta(seconds=3), dev=DEV2)

    # Portrait photo stored rotated with EXIF orientation 6
    disp = portrait_scene(1, 300, 700, fsize=800, size=(H, W))
    stored = disp.transpose(Image.Transpose.ROTATE_90)
    save_jpg(stored, d1 / "IMG_1010.JPG", t1 + timedelta(minutes=5), orientation=6)

    # Face touching the top edge -> crop must be careful (needs_review / conflict)
    save_jpg(portrait_scene(2, 500, 0, fsize=700), d1 / "IMG_1011.JPG", t1 + timedelta(minutes=9))

    # Cat burst (pet protection)
    for k in range(3):
        im = Image.fromarray(data.chelsea()).resize((W, H), Image.Resampling.LANCZOS)
        im = im.rotate(k * 1.5, resample=Image.Resampling.BICUBIC)
        save_jpg(im, d1 / f"IMG_{1020 + k}.JPG", t1 + timedelta(minutes=20, seconds=k))

    # Day 2: scenery, HEIC, Live Photo, exact dup, messaging copy
    t2 = datetime(2024, 4, 3, 14, 0, 0)
    for k, name in enumerate(["coffee", "rocket", "hubble_deep_field", "immunohistochemistry"]):
        save_jpg(scenery(name), d2 / f"IMG_{2100 + k}.JPG", t2 + timedelta(minutes=10 * k))
    heic = portrait_scene(3, 900, 250, fsize=800)
    pillow_heif.from_pillow(heic).save(
        d2 / "IMG_2200.HEIC", quality=90, exif=exif_bytes(t2 + timedelta(hours=1)))
    (d2 / "IMG_2200.MOV").write_bytes(b"\x00\x00\x00\x18ftypqt  fake live photo video")
    pillow_heif.from_pillow(scenery("astronaut")).save(
        d2 / "IMG_2201.HEIC", quality=90, exif=exif_bytes(t2 + timedelta(hours=2)))
    (d2 / "Copies").mkdir(parents=True)
    shutil.copy2(d2 / "IMG_2100.JPG", d2 / "Copies" / "IMG_2100 (1).JPG")
    scenery("rocket", (800, 600)).save(d2 / "WhatsApp Image 2024-04-03.jpeg", quality=70)

    # Day 3: more scenery + no-timestamp photo
    t3 = datetime(2024, 4, 4, 9, 0, 0)
    for k, name in enumerate(["chelsea", "coffee"]):
        im = scenery(name).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        save_jpg(im, d3 / f"IMG_{3000 + k}.JPG", t3 + timedelta(hours=k))
    scenery("hubble_deep_field").save(d3 / "IMG_3010_no_exif.JPG", quality=90)

    # Hostile and excluded inputs
    good = (d1 / "IMG_1001.JPG").read_bytes()
    (d3 / "IMG_3999_corrupt.JPG").write_bytes(good[: len(good) // 3])
    (d3 / "IMG_3998_CRASHME.JPG").write_bytes(good)  # crashes the decoder when the marker env var is set
    Image.new("RGB", (1170, 2532), (240, 240, 240)).save(d3 / "Screenshot 2024-04-04.png")
    (d3 / "notes.txt").write_text("not a photo")
    if canary:
        plant_canaries(out, canary)
    n = sum(1 for _ in out.rglob("*") if _.is_file())
    print(f"wrote {n} files to {out}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--canary", help="plant this token in names and metadata")
    a = ap.parse_args()
    main(a.out, a.canary)
