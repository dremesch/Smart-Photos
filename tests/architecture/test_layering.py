"""Enforce the layer dependency rules in docs/design/01-architecture-overview.md §5."""

from __future__ import annotations

from pathlib import Path

from ._imports import internal_layer, iter_imports

ALLOWED: dict[str, set[str]] = {
    "ui": {"ui", "services", "core", "diagnostics"},
    "services": {
        "services",
        "core",
        "adapters",
        "persistence",
        "workers",
        "security",
        "diagnostics",
    },
    "workers": {"workers", "core", "persistence", "security", "diagnostics"},
    "persistence": {"persistence", "core", "diagnostics"},
    "adapters": {"adapters", "core", "security", "diagnostics"},
    "core": {"core", "diagnostics"},
    "security": {"security", "diagnostics"},
    "diagnostics": {"diagnostics"},
    "__init__": set(),
    "__main__": {"__init__", "services", "ui"},
}

HEADLESS_LAYERS = set(ALLOWED) - {"ui", "__main__"}
UI_TOOLKITS = ("PySide6", "PyQt5", "PyQt6", "shiboken6")


def test_every_layer_is_classified(package_root: Path) -> None:
    layers = {p.name for p in package_root.iterdir() if p.is_dir() and not p.name.startswith("_")}
    layers |= {p.stem for p in package_root.glob("*.py")}
    assert layers <= set(ALLOWED), f"Unclassified layers: {layers - set(ALLOWED)}"


def test_layer_dependencies(package_root: Path) -> None:
    violations = []
    for edge in iter_imports(package_root):
        target_layer = internal_layer(edge.target)
        if edge.target == "family_photo_curator":
            target_layer = "__init__"
        if target_layer and target_layer not in ALLOWED[edge.source_layer]:
            violations.append(
                f"{edge.source_file.relative_to(package_root)}:{edge.lineno} "
                f"{edge.source_layer} -> {target_layer}"
            )
    assert not violations, "Layering violations:\n" + "\n".join(violations)


def test_engine_is_headless(package_root: Path) -> None:
    """Spec v1.1 §4.1: the processing engine must not import UI toolkits."""
    violations = [
        f"{e.source_file.relative_to(package_root)}:{e.lineno} imports {e.target}"
        for e in iter_imports(package_root)
        if e.source_layer in HEADLESS_LAYERS and e.target.split(".")[0] in UI_TOOLKITS
    ]
    assert not violations, "\n".join(violations)
