"""Static import analysis shared by architecture tests (no package code is executed)."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "family_photo_curator"


@dataclass(frozen=True)
class ImportEdge:
    source_file: Path
    source_layer: str
    target: str
    lineno: int


def layer_of(module_file: Path, package_root: Path) -> str:
    rel = module_file.relative_to(package_root)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def iter_imports(package_root: Path) -> Iterator[ImportEdge]:
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        layer = layer_of(path, package_root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield ImportEdge(path, layer, alias.name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import: resolve against the file's package
                    rel_parts = list(path.relative_to(package_root).parent.parts)
                    base = rel_parts[: len(rel_parts) - (node.level - 1)]
                    target = ".".join([PACKAGE, *base, *([node.module] if node.module else [])])
                else:
                    target = node.module or ""
                yield ImportEdge(path, layer, target, node.lineno)


def internal_layer(target: str) -> str | None:
    parts = target.split(".")
    if parts[0] != PACKAGE or len(parts) < 2:
        return None
    return parts[1]
