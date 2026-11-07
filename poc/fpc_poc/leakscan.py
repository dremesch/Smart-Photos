"""Leak scanner for agent-visible and shareable output (issue #13, S2-D11).

Counts, never shows, three kinds of hit:

- ``canary``: a planted canary token (ASCII, UTF-16LE or UTF-16BE, any case)
- ``abs_path``: an absolute path (Windows drive letter, UNC or POSIX), or a literal the
  caller knows is sensitive (for example the temporary root of a test run)
- ``timestamp``: a capture-time-like pattern (``2024:04:02``, ``2024-04-02``, ``10:00:00``)

Results are counts only, so a failing gate tells an agent *that* something leaked and
in which kind of source, never *what* leaked. The pre-USB gate (#23) reuses this.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

KINDS = ("canary", "abs_path", "timestamp")

_WIN_DRIVE = r"(?<![A-Za-z0-9])[A-Za-z]:(?:\\|/)+[A-Za-z0-9_.~$ \-]"
_UNC = r"\\\\+[A-Za-z0-9_.$\-]+\\+[A-Za-z0-9_.$\-]+"
_POSIX = r"(?<![\w.:/\\])/(?:[A-Za-z0-9_.~\-]+/)+[A-Za-z0-9_.~\-]*"
ABS_PATH = re.compile("|".join((_WIN_DRIVE, _UNC, _POSIX)))
TIMESTAMP = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[:\-/._]\d{2}[:\-/._]\d{2}(?!\d)"  # dates as EXIF / ISO / file names write them
    r"|(?<!\d)\d{1,2}:\d{2}:\d{2}(?!\d)"  # wall-clock or capture times
)
JPEG_SUFFIXES = {".jpg", ".jpeg"}


def _variants(token: str) -> list[bytes]:
    t = token.lower()
    return [t.encode("ascii"), t.encode("utf-16-le"), t.encode("utf-16-be")]


def _lower_ascii(data: bytes) -> bytes:
    return data.lower()  # bytes.lower only touches A-Z, so UTF-16 forms survive too


def scan_bytes(data: bytes, tokens: Iterable[str], literals: Iterable[str] = (),
               text_patterns: bool = True) -> Counter:
    """Scan raw bytes. Path and timestamp patterns run on a latin-1 view when asked."""
    c: Counter = Counter()
    low = _lower_ascii(data)
    for tok in tokens:
        c["canary"] += sum(low.count(v) for v in _variants(tok))
    for lit in literals:
        if lit:
            c["abs_path"] += sum(low.count(v) for v in _variants(lit))
    if text_patterns:
        text = data.decode("latin-1")
        c["abs_path"] += len(ABS_PATH.findall(text))
        c["timestamp"] += len(TIMESTAMP.findall(text))
    return c


def scan_text(text: str, tokens: Iterable[str], literals: Iterable[str] = ()) -> Counter:
    return scan_bytes(text.encode("utf-8", "replace"), tokens, literals)


def _jpeg_header(data: bytes) -> bytes:
    """Bytes before the first start-of-scan marker: every metadata segment, no entropy data."""
    i = data.find(b"\xff\xda")
    return data if i < 0 else data[:i]


def scan_file(path: Path, tokens: Iterable[str], literals: Iterable[str] = ()) -> Counter:
    tokens, literals = list(tokens), list(literals)
    data = path.read_bytes()
    if path.suffix.lower() in JPEG_SUFFIXES:
        # Canary bytes anywhere; path/time patterns only in metadata segments, because
        # compressed pixel data can match short regexes by chance.
        c = scan_bytes(data, tokens, literals, text_patterns=False)
        c.update(scan_bytes(_jpeg_header(data), [], [], text_patterns=True))
        return c
    return scan_bytes(data, tokens, literals)


def scan_tree(root: Path, tokens: Iterable[str], literals: Iterable[str] = ()) -> Counter:
    """Scan every file under root, and every relative file and folder name."""
    tokens, literals = list(tokens), list(literals)
    c: Counter = Counter()
    if not root.exists():
        return c
    for p in sorted(root.rglob("*")):
        c.update(scan_text(p.relative_to(root).as_posix(), tokens, literals))
        if p.is_file():
            c["files"] += 1
            c.update(scan_file(p, tokens, literals))
    return c


def hits(c: Counter) -> int:
    return sum(c[k] for k in KINDS)


def report(results: dict[str, Counter]) -> str:
    """Counts per source and kind only; safe to show an agent."""
    parts = []
    for src, c in results.items():
        parts.append(f"{src}: " + ", ".join(f"{k}={c[k]}" for k in KINDS) + f", files={c['files']}")
    return "; ".join(parts)
