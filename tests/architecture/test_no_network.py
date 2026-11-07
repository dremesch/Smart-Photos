"""Privacy boundary (spec v1.1 §16.2, ADR-0001): no network code in application source.

Only ``security.network_guard`` may touch ``socket``, and only to block it.
"""

from __future__ import annotations

from pathlib import Path

from ._imports import iter_imports

NETWORK_MODULES = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "websockets",
    "websocket",
    "grpc",
    "boto3",
    "azure",
    "google",
    "PySide6.QtNetwork",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
}
ALLOWED_FILES = {Path("security/network_guard.py")}


def _is_network(target: str) -> bool:
    return any(target == m or target.startswith(m + ".") for m in NETWORK_MODULES)


def test_no_network_imports(package_root: Path) -> None:
    violations = [
        f"{e.source_file.relative_to(package_root)}:{e.lineno} imports {e.target}"
        for e in iter_imports(package_root)
        if _is_network(e.target) and e.source_file.relative_to(package_root) not in ALLOWED_FILES
    ]
    assert not violations, "Network imports are not permitted:\n" + "\n".join(violations)
