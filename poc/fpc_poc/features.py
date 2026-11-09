"""Lightweight per-photo features from the cached derivative (ADR-0005).

Runs on EVERY eligible photo. All boxes are normalized to the oriented image, so
they scale to full resolution for cropping.

POC model choices (to be replaced after M0 benchmarks + license review):
- faces / eyes / cat faces: OpenCV Haar cascades (bundled with opencv-python 4.x).
  If a YuNet ONNX file is present at models/face_detection_yunet*.onnx it is used
  for faces instead (eyes still via Haar).
- "embedding": tiny colour+layout vector. Stand-in for SigLIP/DINOv2.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
VERSION = "poc-features-1"  # bump when compute() changes: quarantined items get a fresh chance


@lru_cache(maxsize=1)
def _cascades():
    d = cv2.data.haarcascades
    return {
        "face": cv2.CascadeClassifier(d + "haarcascade_frontalface_default.xml"),
        "eye": cv2.CascadeClassifier(d + "haarcascade_eye_tree_eyeglasses.xml"),
        "cat": cv2.CascadeClassifier(d + "haarcascade_frontalcatface_extended.xml"),
    }


@lru_cache(maxsize=1)
def _yunet_path() -> str | None:
    hits = sorted((REPO / "models").glob("face_detection_yunet*.onnx"))
    return str(hits[0]) if hits else None


def _lapvar(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _detect_faces(bgr: np.ndarray, gray: np.ndarray) -> list[tuple[int, int, int, int, float]]:
    h, w = gray.shape
    yn = _yunet_path()
    if yn:
        det = cv2.FaceDetectorYN.create(yn, "", (w, h), 0.7)
        _, faces = det.detect(bgr)
        if faces is None:
            return []
        return [(int(f[0]), int(f[1]), int(f[2]), int(f[3]), float(f[14])) for f in faces]
    m = max(16, int(min(h, w) * 0.04))
    found = _cascades()["face"].detectMultiScale(gray, 1.1, 6, minSize=(m, m))
    return [(int(x), int(y), int(fw), int(fh), 1.0) for x, y, fw, fh in found]


def _iou(a: list[float], b: list[float]) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter + 1e-9)


def _embedding(rgb: np.ndarray) -> list[float]:
    small = cv2.resize(rgb, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float32).ravel() / 255
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [8, 6], [0, 180, 0, 256]).ravel()
    hist = hist / (hist.sum() + 1e-6)
    v = np.concatenate([small - small.mean(), hist * 4])
    v = v / (np.linalg.norm(v) + 1e-9)
    return [round(float(x), 5) for x in v]


def _saliency_center(gray: np.ndarray) -> tuple[float, float]:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    mag = cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), 15)
    tot = float(mag.sum()) + 1e-6
    ys, xs = np.indices(mag.shape)
    return float((mag * xs).sum() / tot / gray.shape[1]), float((mag * ys).sum() / tot / gray.shape[0])


def compute(deriv_path: str) -> dict:
    cv2.setNumThreads(1)  # parallelism comes from the process pool; avoid oversubscription
    pil = Image.open(deriv_path).convert("RGB")
    rgb = np.asarray(pil)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    lum = gray.astype(np.float32) / 255

    faces = []
    for x, y, fw, fh, conf in _detect_faces(bgr, gray):
        roi = gray[y : y + fh, x : x + fw]
        upper = roi[: max(1, int(fh * 0.6)), :]
        em = max(6, fw // 8)
        eyes = _cascades()["eye"].detectMultiScale(upper, 1.1, 4, minSize=(em, em))
        faces.append({
            "box": [x / w, y / h, fw / w, fh / h],
            "conf": conf,
            "eyes": int(len(eyes)),
            "eyes_open": bool(len(eyes) >= 2),  # crude proxy: Haar finds open eyes far better
            "sharp": _lapvar(roi),
        })
    m = max(24, int(min(h, w) * 0.06))
    pets = [
        {"box": [x / w, y / h, pw / w, ph / h], "kind": "cat"}
        for x, y, pw, ph in _cascades()["cat"].detectMultiScale(gray, 1.05, 3, minSize=(m, m))
    ]
    # a "human face" that overlaps a cat face is the cat
    faces = [f for f in faces if not any(_iou(f["box"], p["box"]) > 0.3 for p in pets)]
    # primary faces: background detections are usually much smaller (and often false positives)
    if faces:
        big = max(f["box"][2] * f["box"][3] for f in faces)
        for f in faces:
            f["primary"] = f["box"][2] * f["box"][3] >= 0.4 * big

    tiny = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
    return {
        "phash": str(imagehash.phash(pil)),
        "dhash": str(imagehash.dhash(pil)),
        "sharp": _lapvar(gray),
        "mean_lum": float(lum.mean()),
        "clip_hi": float((lum > 0.98).mean()),
        "clip_lo": float((lum < 0.02).mean()),
        "contrast": float(np.percentile(lum, 95) - np.percentile(lum, 5)),
        "noise": float(np.std(tiny.astype(np.float32) - cv2.GaussianBlur(tiny, (3, 3), 0))),
        "faces": faces,
        "pets": pets,
        "saliency": _saliency_center(gray),
        "emb": _embedding(rgb),
        "detector": "yunet" if _yunet_path() else "haar",
    }
