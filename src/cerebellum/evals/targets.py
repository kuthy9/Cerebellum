"""Where an eval's connectors point, so the person running it knows what it will touch."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from cerebellum.connectors.postgres import SANDBOX_DSN
from cerebellum.spec.models import Workflow


@dataclass(frozen=True)
class Target:
    connector: str
    kind: str  # postgres | rest
    where: str  # display text, credentials removed
    isolated: bool  # True when eval cases cannot reach anything outside their sandbox
    warning: str | None


def redact(url: str) -> str:
    """A URL or DSN for display: user and password removed; key=value DSNs are not shown."""
    if "://" not in url:
        return "PostgreSQL (key=value DSN)"
    parts = urlsplit(url)
    if not (parts.username or parts.password):
        return url
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _same_server(url: str, other: str) -> bool:
    def norm(value: str) -> str:
        return value.rstrip("/").replace("://localhost", "://127.0.0.1")

    return norm(url) == norm(other)


def connector_targets(workflow: Workflow, sandbox_url: str | None) -> list[Target]:
    """Each connector's target. Only a `dsn: sandbox` database (fresh per eval run) and the
    sandbox payments API at `sandbox_url` are isolated; everything else is called for real."""
    targets: list[Target] = []
    for name, spec in workflow.connectors.items():
        if spec.type == "postgres":
            if spec.dsn == SANDBOX_DSN:
                targets.append(Target(name, "postgres", "fresh sandbox database", True, None))
            else:
                warning = f"{name} is a real PostgreSQL database: eval cases read and write it"
                targets.append(Target(name, "postgres", redact(spec.dsn), False, warning))
            continue
        url = redact(spec.base_url)
        if sandbox_url is not None and _same_server(spec.base_url, sandbox_url):
            targets.append(Target(name, "rest", f"{url} (sandbox)", True, None))
            continue
        warning = f"{name} → {url} is not the sandbox payments API: eval cases call it for real"
        if sandbox_url is not None:
            warning += "; the cases' sandbox fault modes do not apply to it"
        targets.append(Target(name, "rest", url, False, warning))
    return targets
