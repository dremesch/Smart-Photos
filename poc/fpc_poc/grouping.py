"""Exact / near-duplicate / burst grouping with complete linkage (spec v1.1 §8.1-8.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import imagehash
import numpy as np

from . import config


@dataclass
class P:
    id: int
    sha: str
    ts: datetime | None
    device: str | None
    phash: imagehash.ImageHash
    emb: np.ndarray
    portrait: bool


@dataclass
class Group:
    kind: str  # exact | near | burst
    members: list[int] = field(default_factory=list)


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def similar(a: P, b: P) -> bool:
    if a.portrait != b.portrait:
        return False
    d = a.phash - b.phash
    if d <= config.PHASH_STRONG:
        return True
    cos = float(a.emb @ b.emb)
    return d <= config.PHASH_LOOSE and cos >= config.EMB_COS_MIN


def candidate(a: P, b: P) -> bool:
    if a.ts and b.ts and abs((a.ts - b.ts).total_seconds()) <= config.TIME_WINDOW_S:
        return True
    return (a.phash - b.phash) <= config.PHASH_STRONG  # copies with lost/wrong timestamps


def build(photos: list[P]) -> list[Group]:
    groups: list[Group] = []
    # 1. exact duplicates collapse to one representative
    by_sha: dict[str, list[P]] = {}
    for p in photos:
        by_sha.setdefault(p.sha, []).append(p)
    reps = []
    for ps in by_sha.values():
        reps.append(ps[0])
        if len(ps) > 1:
            groups.append(Group("exact", [p.id for p in ps]))

    # 2. complete-linkage clustering of representatives, in time order
    reps.sort(key=lambda p: (p.ts is None, p.ts or datetime.min, p.id))
    clusters: list[list[P]] = []
    for p in reps:
        best = None
        for c in clusters:
            if all(candidate(p, q) and similar(p, q) for q in c):  # complete linkage
                best = c
                break
        if best is None:
            clusters.append([p])
        else:
            best.append(p)

    exact_of = {g.members[0]: g.members for g in groups}
    for c in clusters:
        if len(c) < 2:
            continue
        ids = []
        for p in c:
            ids += exact_of.get(p.id, [p.id])
        tss = [p.ts for p in c if p.ts]
        devices = {p.device for p in c}
        is_burst = (
            len(tss) == len(c) and len(devices) == 1
            and (max(tss) - min(tss)).total_seconds() <= config.BURST_WINDOW_S
        )
        groups.append(Group("burst" if is_burst else "near", ids))
    # exact groups that were absorbed into a near/burst group are dropped
    covered = {i for g in groups if g.kind != "exact" for i in g.members}
    return [g for g in groups if g.kind != "exact" or not set(g.members) <= covered]
