from __future__ import annotations

from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "family_photo_curator"


@pytest.fixture(scope="session")
def package_root() -> Path:
    return PACKAGE_ROOT
