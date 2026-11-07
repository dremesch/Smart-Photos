from __future__ import annotations

import importlib
import pkgutil

import family_photo_curator
from family_photo_curator.__main__ import main


def test_version_is_set() -> None:
    assert family_photo_curator.__version__


def test_cli_runs() -> None:
    assert main([]) == 0


def test_all_headless_packages_import() -> None:
    for info in pkgutil.walk_packages(family_photo_curator.__path__, "family_photo_curator."):
        if info.name.startswith("family_photo_curator.ui"):
            continue
        importlib.import_module(info.name)
