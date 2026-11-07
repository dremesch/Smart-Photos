"""Per-photo scoring, within-group ranking and album recommendation (spec v1.1 §8.4, §10).

Weights are uncalibrated POC guesses. The album step is plain greedy with a
redundancy penalty; no approximation guarantee is claimed.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

W = {"eyes": 0.30, "subject_sharp": 0.20, "sharp": 0.15, "exposure": 0.15, "res": 0.10, "prov": 0.10}


def _norm_sharp(v: float) -> float:
    return max(0.0, min(1.0, math.log1p(v) / math.log1p(1500)))


def score(f: dict, width: int, height: int, has_ts: bool, has_device: bool) -> tuple[float, list[str]]:
    reasons = []
    faces = [fc for fc in f.get("faces", []) if fc.get("primary", True)]
    if faces:
        eyes = sum(fc["eyes_open"] for fc in faces) / len(faces)
        subj = sum(_norm_sharp(fc["sharp"]) for fc in faces) / len(faces)
        reasons.append(f"{len(faces)} face(s), eyes open {eyes:.0%}")
    else:
        eyes, subj = 0.6, _norm_sharp(f["sharp"])  # neutral when no faces
    sharp = _norm_sharp(f["sharp"])
    exposure = max(0.0, 1 - abs(f["mean_lum"] - 0.47) * 2.2 - (f["clip_hi"] + f["clip_lo"]) * 3)
    res = min(1.0, (width * height) / 12e6)
    prov = 0.5 * has_ts + 0.5 * has_device
    comps = {"eyes": eyes, "subject_sharp": subj, "sharp": sharp, "exposure": exposure, "res": res, "prov": prov}
    s = sum(W[k] * v for k, v in comps.items())
    if sharp < 0.35:
        reasons.append("soft focus")
    if exposure < 0.5:
        reasons.append("exposure problems")
    if res < 0.3:
        reasons.append("low resolution (copy?)")
    if prov < 1:
        reasons.append("incomplete metadata")
    if f.get("pets"):
        reasons.append("pet detected")
    return round(s, 4), reasons


def recommend(
    photos: list[dict],  # id, score, emb(np), day, group, people(bool)
    target: int,
    approved: set[int],
    rejected: set[int],
    redundancy: float = 0.35,
    per_group: int = 1,
) -> list[int]:
    """Greedy marginal utility: score + people bonus + day coverage - redundancy."""
    sel: list[int] = [p["id"] for p in photos if p["id"] in approved]
    by_id = {p["id"]: p for p in photos}
    per_day = defaultdict(int)
    per_grp = defaultdict(int)
    for i in sel:
        per_day[by_id[i]["day"]] += 1
        if by_id[i]["group"] is not None:
            per_grp[by_id[i]["group"]] += 1
    days = {p["day"] for p in photos}
    pool = [p for p in photos if p["id"] not in approved and p["id"] not in rejected]
    while len(sel) < target and pool:
        best, best_u = None, -1e9
        for p in pool:
            if p["group"] is not None and per_grp[p["group"]] >= per_group:
                continue
            red = max((float(p["emb"] @ by_id[i]["emb"]) for i in sel), default=0.0)
            cov = 0.15 / (1 + per_day[p["day"]]) if len(days) > 1 else 0.0
            u = p["score"] + (0.08 if p["people"] else 0.0) + cov - redundancy * max(0.0, red - 0.5)
            if u > best_u:
                best, best_u = p, u
        if best is None:
            break
        sel.append(best["id"])
        per_day[best["day"]] += 1
        if best["group"] is not None:
            per_grp[best["group"]] += 1
        pool.remove(best)
    return sel


def as_array(v: list[float]) -> np.ndarray:
    return np.asarray(v, dtype=np.float32)
