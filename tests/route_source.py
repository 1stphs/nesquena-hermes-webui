"""Helpers for source-level route contract tests."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTE_SOURCE_PATHS = (
    REPO_ROOT / "api" / "routes.py",
    REPO_ROOT / "api" / "routes_dispatcher.py",
    *sorted((REPO_ROOT / "api" / "routes_handlers").glob("*.py")),
    *sorted((REPO_ROOT / "api" / "routes_helpers").glob("*.py")),
)


def read_route_sources() -> str:
    chunks: list[str] = []
    for path in ROUTE_SOURCE_PATHS:
        if path.name == "__init__.py" or not path.exists():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        chunks.append(f"\n# BEGIN {rel}\n")
        chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def function_source(name: str) -> str:
    marker = f"def {name}("
    source = read_route_sources()
    idx = source.find(marker)
    assert idx != -1, f"{name} not found in route sources"
    next_def = source.find("\ndef ", idx + len(marker))
    next_file = source.find("\n# BEGIN ", idx + len(marker))
    candidates = [pos for pos in (next_def, next_file) if pos != -1]
    end = min(candidates) if candidates else len(source)
    return source[idx:end]
