"""Workflows the dashboard can show and start: YAML files under a directory, plus every workflow
snapshot that runs in the store were started from."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cerebellum.errors import SpecError
from cerebellum.runtime.store import Store
from cerebellum.spec import load_workflow, parse_workflow
from cerebellum.spec.models import Workflow

DEFAULT_SCAN_DEPTH = 3
SKIP_DIRS = frozenset({"node_modules", "__pycache__", "site-packages", "dist", "build"})


@dataclass(frozen=True)
class WorkflowEntry:
    id: str
    workflow: Workflow
    source: str  # "file" | "history"
    path: str | None
    samples: dict[str, Any] = field(default_factory=dict)
    runs: int = 0
    last_run_at: float | None = None


class Catalog:
    def __init__(self, store: Store, root: Path, *, depth: int = DEFAULT_SCAN_DEPTH) -> None:
        self.store = store
        self.root = Path(root).resolve()
        self.depth = depth

    def entries(self) -> list[WorkflowEntry]:
        snapshots = {snap.digest: snap for snap in self.store.list_workflows()}
        entries: list[WorkflowEntry] = []
        on_disk: set[str] = set()
        for path in self._yaml_files():
            try:
                workflow = load_workflow(path)
            except (SpecError, OSError, UnicodeDecodeError):
                continue  # not a workflow (compose files, eval suites, ...) or unreadable
            snap = snapshots.get(workflow.digest)
            on_disk.add(workflow.digest)
            entries.append(
                WorkflowEntry(
                    id=path.relative_to(self.root).as_posix(),
                    workflow=workflow,
                    source="file",
                    path=str(path),
                    samples=_samples(path.parent),
                    runs=snap.runs if snap else 0,
                    last_run_at=snap.last_run_at if snap else None,
                )
            )
        for digest, snap in snapshots.items():
            if digest in on_disk:
                continue
            try:
                workflow = parse_workflow(snap.source_yaml, base_dir=snap.base_dir)
            except SpecError:
                continue  # e.g. a ${VAR} it needs is not set in this environment
            entries.append(
                WorkflowEntry(
                    id=digest,
                    workflow=workflow,
                    source="history",
                    path=None,
                    samples=_samples(Path(snap.base_dir)),
                    runs=snap.runs,
                    last_run_at=snap.last_run_at,
                )
            )
        return entries

    def get(self, entry_id: str) -> WorkflowEntry:
        for entry in self.entries():
            if entry.id == entry_id:
                return entry
        raise KeyError(entry_id)

    def _yaml_files(self) -> Iterator[Path]:
        if not self.root.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(self.root):
            depth = len(Path(dirpath).relative_to(self.root).parts)
            if depth >= self.depth:
                dirnames[:] = []
            else:
                dirnames[:] = sorted(
                    d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
                )
            for name in sorted(filenames):
                if name.endswith((".yaml", ".yml")):
                    yield Path(dirpath) / name


def _samples(directory: Path) -> dict[str, Any]:
    samples: dict[str, Any] = {}
    for path in sorted((directory / "inputs").glob("*.json")):
        try:
            samples[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return samples
