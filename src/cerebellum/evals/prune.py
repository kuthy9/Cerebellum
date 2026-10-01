"""Every eval run gets a fresh sandbox directory; prune the ones nobody needs any more."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cerebellum.config import Settings
from cerebellum.evals.runner import eval_home
from cerebellum.runtime.store import Store


@dataclass(frozen=True)
class PrunedHome:
    eval_run_id: str
    suite: str
    path: Path


def prune_eval_homes(store: Store, settings: Settings, *, keep: int) -> list[PrunedHome]:
    """Remove the sandbox directories of all but the newest `keep` eval runs of each suite, and
    return what was removed. The eval rows, case results and case runs stay: they are the small
    history the dashboard and the next baseline comparison read. A running eval keeps its
    directory unless it is stale (its process died)."""
    root = settings.home / "evals"
    pruned: list[PrunedHome] = []
    for record in store.eval_runs_beyond(keep):
        if record.status == "running" and not store.is_eval_stale(
            record, timeout=settings.lease_seconds
        ):
            continue
        path = eval_home(settings, record.id)
        if path.parent != root or path.is_symlink() or not path.is_dir():
            continue  # already pruned, or not a directory this eval created
        shutil.rmtree(path)
        pruned.append(PrunedHome(record.id, record.suite, path))
    return pruned
