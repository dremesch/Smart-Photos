"""Aggregate-only interpreter output (issue #13, Security review of PR #39).

Python's default warning and traceback output prints absolute module paths, and on the VM
those contain the Windows user name (D2). Library warnings (Pillow DecompressionBombWarning,
corrupt EXIF/TIFF/MPO data) and idle pool workers hit by Ctrl+C would print them to stderr.

- ``install_main()``: warnings print ``fpc_poc: warning class=<Category> code=W_LIB`` only.
- ``worker_init()``: the pool initializer. Network guard, sanitised warnings, SIGINT ignored
  (the parent alone handles cancel), uncaught errors as class and code, and any other stderr
  text replaced by a single code line.
"""

from __future__ import annotations

import signal
import sys
import warnings

from . import guard

PREFIX = "fpc_poc: "


def _line(text: str) -> None:
    stream = sys.stderr  # a _SanitizedStream in workers passes PREFIX lines through
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except (OSError, ValueError, AttributeError):
        pass


def _showwarning(message, category, filename, lineno, file=None, line=None) -> None:  # noqa: ARG001
    _line(f"{PREFIX}warning class={category.__name__} code=W_LIB")


def _excepthook(exc_type, exc, tb) -> None:  # noqa: ARG001
    _line(f"{PREFIX}error class={exc_type.__name__} code=E_WORKER_UNCAUGHT")


class _SanitizedStream:
    """Stands in for sys.stderr in workers. Our own code lines pass; anything else (for
    example multiprocessing's bootstrap traceback) becomes one code line per process."""

    def __init__(self, real):
        self._real = real
        self._suppressed = False

    def write(self, s: str) -> int:
        if s.startswith(PREFIX):
            self._real.write(s)
        elif s.strip() and not self._suppressed:
            self._suppressed = True
            self._real.write(f"{PREFIX}worker stderr suppressed code=W_STDERR\n")
        return len(s)

    def flush(self) -> None:
        try:
            self._real.flush()
        except (OSError, ValueError):
            pass

    def isatty(self) -> bool:
        return False


def install_main() -> None:
    warnings.showwarning = _showwarning


def worker_init() -> None:
    guard.block()
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    warnings.showwarning = _showwarning
    sys.excepthook = _excepthook
    if sys.stderr is not None and not isinstance(sys.stderr, _SanitizedStream):
        sys.stderr = _SanitizedStream(sys.stderr)
