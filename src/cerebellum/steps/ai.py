"""`ai` step: structured output validated against the step's JSON Schema, with repair turns."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from cerebellum.ai.base import AIError, AIRequest
from cerebellum.errors import StepError
from cerebellum.spec.expressions import render
from cerebellum.spec.schemas import validation_errors
from cerebellum.steps.base import StepRuntime


async def run_ai(rt: StepRuntime) -> Any:
    step = rt.step
    prompt = render(step.prompt, rt.ctx)
    system = render(step.system, rt.ctx) if step.system else None
    request = AIRequest(
        model=step.model or rt.settings.model,
        prompt=prompt,
        schema=step.output_schema,
        system=system,
        effort=step.effort,
        max_tokens=step.max_tokens,
        mock_rules=tuple(step.mock),
        context=rt.ctx,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    errors: list[str] = []
    for repair in range(step.max_repairs + 1):
        try:
            result = await rt.ai.generate(request, messages)
        except AIError as exc:
            rt.record_call(
                "llm",
                {
                    "provider": rt.ai.name,
                    "model": request.model,
                    "mock": rt.ai.mock,
                    "system": system,
                    "prompt": prompt,
                    "repair": repair,
                    "ok": False,
                    "error": str(exc),
                    "kind": exc.kind,
                },
            )
            raise
        output, errors = _parse(result.text, step.output_schema)
        rt.record_call(
            "llm",
            {
                "provider": rt.ai.name,
                "model": result.model,
                "mock": result.mock,
                "system": system,
                "prompt": prompt,
                "repair": repair,
                "ok": not errors,
                "response": result.text,
                "errors": errors,
                "usage": asdict(result.usage),
                "stop_reason": result.stop_reason,
                "duration_ms": round(result.latency_ms, 2),
            },
            result.cost_usd,
        )
        if not errors:
            return output
        messages = [
            *messages,
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": _repair_prompt(errors)},
        ]
    raise StepError(
        f"AI output failed schema validation after {step.max_repairs + 1} attempt(s): {errors[0]}",
        retryable=True,
        kind="schema",
        details={"errors": errors},
    )


def _parse(text: str, schema: dict[str, Any]) -> tuple[Any, list[str]]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"<root>: response is not valid JSON ({exc.msg})"]
    return value, validation_errors(schema, value)


def _repair_prompt(errors: list[str]) -> str:
    listed = "\n".join(f"- {error}" for error in errors[:10])
    return (
        "Your previous response failed JSON Schema validation:\n"
        f"{listed}\nReturn only the corrected JSON object."
    )
