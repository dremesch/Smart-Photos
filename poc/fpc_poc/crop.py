"""4x6 crop: exact geometry, invariant, 1-D search, risk classes (spec v1.1 §12)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import config


@dataclass
class Crop:
    oriented_size: tuple[int, int]
    target: str  # "3:2" | "2:3"
    mode: str  # borderless | bordered
    px: tuple[int, int, int, int]  # x, y, w, h in oriented source pixels
    norm: tuple[float, float, float, float]
    risk: str  # likely_safe | needs_review | crop_conflict
    reasons: list[str]
    protected: list[list[float]]  # normalized boxes considered

    def to_dict(self) -> dict:
        return asdict(self)


def target_ratio(w: int, h: int) -> tuple[str, float]:
    return ("3:2", 1.5) if w >= h else ("2:3", 2 / 3)


def max_rect(w: int, h: int) -> tuple[int, int, str]:
    """Largest integer rectangle of the target ratio; returns (cw, ch, free_axis)."""
    _, r = target_ratio(w, h)
    if w / h >= r:  # too wide: full height, slide on x
        return round(h * r), h, "x"
    return w, round(w / r), "y"


def validate(w: int, h: int, x: int, y: int, cw: int, ch: int) -> None:
    """The pixel-aspect invariant. Raises on violation."""
    _, r = target_ratio(w, h)
    if not (0 <= x and 0 <= y and x + cw <= w and y + ch <= h and cw > 0 and ch > 0):
        raise ValueError(f"crop out of bounds: {(x, y, cw, ch)} in {(w, h)}")
    # within one source pixel of the exact ratio
    if abs(cw - ch * r) > 1.0 and abs(ch - cw / r) > 1.0:
        raise ValueError(f"crop ratio {cw / ch:.4f} != {r:.4f}")


def _head_box(b: list[float]) -> list[float]:
    """Expand a face box to a head-ish region (hair, chin)."""
    x, y, w, h = b
    return [x - 0.25 * w, y - 0.45 * h, w * 1.5, h * 1.75]


def propose(w: int, h: int, faces: list[dict], pets: list[dict], saliency: tuple[float, float],
            margin: float = config.SAFE_MARGIN) -> Crop:
    target, _ = target_ratio(w, h)
    cw, ch, axis = max_rect(w, h)
    slack = (w - cw) if axis == "x" else (h - ch)
    # faces outrank pets when not everything fits (spec: no clipped face if avoidable)
    prot = [_head_box(f["box"]) for f in faces] + [p["box"] for p in pets]
    weights = [1000] * len(faces) + [300] * len(pets)
    boxes = [(b[0] * w, b[1] * h, b[2] * w, b[3] * h) for b in prot]
    m = margin * (cw if axis == "x" else ch)

    def evaluate(off: int):
        x0, y0 = (off, 0) if axis == "x" else (0, off)
        clipped = viol = penalty = 0
        for (bx, by, bw, bh), wt in zip(boxes, weights):
            lo, hi = (bx, bx + bw) if axis == "x" else (by, by + bh)
            a, b = (x0, x0 + cw) if axis == "x" else (y0, y0 + ch)
            lo_c, hi_c = max(lo, 0), min(hi, w if axis == "x" else h)  # box clipped by image itself
            if lo_c < a - 0.5 or hi_c > b + 0.5:
                clipped += 1
                penalty += wt
            elif lo_c < a + m or hi_c > b - m:
                viol += 1
        if boxes:
            cx = sum(bx + bw / 2 for bx, _, bw, _ in boxes) / len(boxes)
            cy = sum(by + bh / 2 for _, by, _, bh in boxes) / len(boxes)
        else:
            cx, cy = saliency[0] * w, saliency[1] * h
        centre = (x0 + cw / 2 - cx) if axis == "x" else (y0 + ch / 2 - cy)
        return (-penalty - 50 * viol - abs(centre) / max(w, h)), clipped, viol

    step = max(1, slack // 400)
    offs = sorted(set(list(range(0, slack + 1, step)) + [slack]))
    best = max(offs, key=lambda o: evaluate(o)[0])
    _, clipped, viol = evaluate(best)
    x, y = (best, 0) if axis == "x" else (0, best)
    validate(w, h, x, y, cw, ch)

    reasons = []
    if clipped:
        risk = "crop_conflict"
        reasons.append(f"{clipped} protected region(s) cannot fit; consider bordered")
    elif viol:
        risk = "needs_review"
        reasons.append(f"{viol} protected region(s) inside the safe margin")
    else:
        risk = "likely_safe"
        reasons.append("protected regions fit with margin" if boxes else "no protected subjects detected")
    if any(f.get("conf", 1) < 0.8 for f in faces):
        risk = "needs_review" if risk == "likely_safe" else risk
        reasons.append("low-confidence face")
    if boxes and any(b[0] < 0 or b[1] < 0 or b[0] + b[2] > 1 or b[1] + b[3] > 1 for b in prot):
        reasons.append("subject touches the original frame edge")
        if risk == "likely_safe":
            risk = "needs_review"
    return Crop((w, h), target, "borderless", (x, y, cw, ch),
                (x / w, y / h, cw / w, ch / h), risk, reasons, prot)


def bordered(w: int, h: int) -> Crop:
    target, _ = target_ratio(w, h)
    return Crop((w, h), target, "bordered", (0, 0, w, h), (0.0, 0.0, 1.0, 1.0),
                "likely_safe", ["full image fitted with borders"], [])
