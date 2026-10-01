"""Workflows the dashboard can show and start: YAML files under a directory, plus every workflow
snapshot that runs in the store were started from."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cerebellum.errors import SpecError
from cerebellum.runtime.store import Store
from cerebellum.spec import load_workflow, parse_workflow
from cerebellum.spec.inputs import nests_deeper_than
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
    """Every scan walks the tree again, but a file is only parsed again once its mtime, size,
    inode or ctime changes, and a snapshot (immutable per digest) only once. Scans may run in
    worker threads."""

    def __init__(self, store: Store, root: Path, *, depth: int = DEFAULT_SCAN_DEPTH) -> None:
        self.store = store
        self.root = Path(root).resolve()
        self.depth = depth
        self._lock = threading.Lock()
        # path → ((mtime_ns, size, inode, ctime_ns), workflow or None when it is not a workflow)
        self._files: dict[Path, tuple[tuple[int, int, int, int], Workflow | None]] = {}
        self._snapshots: dict[str, Workflow | None] = {}

    def entries(self) -> list[WorkflowEntry]:
        with self._lock:
            return self._scan()

    def _scan(self) -> list[WorkflowEntry]:
        snapshots = {snap.digest: snap for snap in self.store.list_workflows()}
        entries: list[WorkflowEntry] = []
        on_disk: set[str] = set()
        files: dict[Path, tuple[tuple[int, int, int, int], Workflow | None]] = {}
        for path in self._yaml_files():
            try:
                stat = path.stat()
            except OSError:
                continue  # removed while the tree was being walked
            # mtime and size alone miss a same-size copy that keeps the old mtime (cp -p,
            # rsync -a, tar): a replacement has a new inode, a rewrite in place a new ctime.
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns)
            cached = self._files.get(path)
            if cached is not None and cached[0] == stamp:
                workflow = cached[1]
            else:
                try:
                    workflow = load_workflow(path)
                except (SpecError, OSError, UnicodeDecodeError):
                    workflow = None  # not a workflow (compose files, eval suites...) or unreadable
            files[path] = (stamp, workflow)
            if workflow is None:
                continue
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
        self._files = files  # files that are gone drop out of the cache
        for digest, snap in snapshots.items():
            if digest in on_disk:
                continue
            if digest not in self._snapshots:
                try:
                    parsed = parse_workflow(snap.source_yaml, base_dir=snap.base_dir)
                except SpecError:
                    parsed = None  # e.g. a ${VAR} it needs is not set in this environment
                self._snapshots[digest] = parsed
            workflow = self._snapshots[digest]
            if workflow is None:
                continue
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
            sample = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if not nests_deeper_than(sample):  # a run could not take it as its input
            samples[path.stem] = sample
    return samples
