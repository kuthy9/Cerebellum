"""`cerebellum demo`: the refund workflow end to end against the local sandbox."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cerebellum.runtime.engine import Engine
from cerebellum.runtime.store import RunRecord
from cerebellum.sandbox.server import SandboxHandle
from cerebellum.spec.models import Workflow
from cerebellum.templates import template_path


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    input_file: str
    fail_mode: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("small", "Small refund · auto-approved", "small.json", "never"),
    Scenario("large", "Large refund · waits for a human", "large.json", "never"),
    Scenario("flaky", "Payments API flaky · retried", "flaky.json", "first:2"),
    Scenario("outage", "Payments API down · manual fallback", "outage.json", "always"),
    Scenario("fraud", "Suspicious reason · denied by AI", "fraud.json", "never"),
)


def scenario_input(scenario: Scenario) -> dict[str, Any]:
    path = template_path("refund") / "inputs" / scenario.input_file
    return json.loads(path.read_text(encoding="utf-8"))


async def run_scenarios(
    engine: Engine,
    workflow: Workflow,
    sandbox: SandboxHandle,
    on_result: Callable[[Scenario, RunRecord], None],
) -> list[tuple[Scenario, RunRecord]]:
    results: list[tuple[Scenario, RunRecord]] = []
    try:
        for scenario in SCENARIOS:
            await asyncio.to_thread(sandbox.set_fail_mode, scenario.fail_mode)
            record = await engine.start(workflow, scenario_input(scenario))
            on_result(scenario, record)
            results.append((scenario, record))
    finally:
        await asyncio.to_thread(sandbox.set_fail_mode, "never")
    return results
