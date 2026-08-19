from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path("src/eeveetuber")
CORE_PACKAGES = {"avatar", "dialogue", "domain", "memory", "runtime"}
FRAMEWORK_IMPORTS = {
    "alembic",
    "fastapi",
    "langchain",
    "langgraph",
    "sqlalchemy",
    "structlog",
    "uvicorn",
}
REFERENCE_IMPORTS = {"open_llm_vtuber", "letta", "agents"}


def _python_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


@pytest.mark.parametrize("path", _python_files(), ids=str)
def test_core_packages_do_not_depend_on_frameworks(path: Path) -> None:
    relative = path.relative_to(SOURCE_ROOT)
    if relative.parts[0] not in CORE_PACKAGES:
        return
    roots = {name.split(".", 1)[0] for name in _imports(path)}

    assert not roots & FRAMEWORK_IMPORTS


@pytest.mark.parametrize("path", _python_files(), ids=str)
def test_reference_projects_are_not_runtime_dependencies(path: Path) -> None:
    roots = {name.split(".", 1)[0] for name in _imports(path)}

    assert not roots & REFERENCE_IMPORTS


@pytest.mark.parametrize("path", _python_files(), ids=str)
def test_source_file_size_rachet(path: Path) -> None:
    line_count = len(path.read_text(encoding="utf-8").splitlines())

    assert line_count <= 700, f"{path} has {line_count} lines; split the responsibility"

