"""Step executors keyed by step type. `approval` steps are handled by the engine itself."""

from cerebellum.steps.ai import run_ai
from cerebellum.steps.base import StepExecutor, StepRuntime
from cerebellum.steps.http import run_http
from cerebellum.steps.query import run_query
from cerebellum.steps.task import run_task
from cerebellum.steps.validate import run_validate

EXECUTORS: dict[str, StepExecutor] = {
    "query": run_query,
    "http": run_http,
    "ai": run_ai,
    "validate": run_validate,
    "task": run_task,
}

__all__ = ["EXECUTORS", "StepExecutor", "StepRuntime"]
