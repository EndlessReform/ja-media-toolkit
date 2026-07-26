"""Architectural tests for the framework-neutral product layer."""

from __future__ import annotations

import ast
from pathlib import Path


PRODUCTS = Path(__file__).parents[1] / "src" / "ja_media_data" / "products"
FORBIDDEN_PREFIXES = (
    "dagster",
    "fastapi",
    "ja_media_data.operator",
    "ja_media_data.orchestration",
)


def test_product_modules_do_not_import_operator_or_orchestration_frameworks() -> None:
    """Keep compilers and commits callable from either legacy or Dagster adapters."""

    violations: list[str] = []
    for path in sorted(PRODUCTS.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = _imported_names(node)
            for name in names:
                if name.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"{path.relative_to(PRODUCTS)} imports {name}")
    assert violations == []


def _imported_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return (node.module,)
    return ()
