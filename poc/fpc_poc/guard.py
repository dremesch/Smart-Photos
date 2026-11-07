"""Fail-closed network guard (ADR-0001). Applied in the main process and every worker."""

from __future__ import annotations

import socket


class NetworkBlocked(RuntimeError):
    pass


def _deny(*_a, **_k):
    raise NetworkBlocked("network access is disabled in Family Photo Curator")


def block() -> None:
    socket.socket = _deny  # type: ignore[assignment,misc]
    socket.create_connection = _deny  # type: ignore[assignment]
    socket.getaddrinfo = _deny  # type: ignore[assignment]
