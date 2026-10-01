"""Example workflows shipped with the package."""

from __future__ import annotations

from pathlib import Path


def template_path(name: str) -> Path:
    path = Path(__file__).parent / name
    if not path.is_dir():
        raise FileNotFoundError(f"no packaged template named {name!r}")
    return path
