"""Pydantic models for workflow definitions."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from cerebellum.config import (
    DEFAULT_AI_MAX_TOKENS,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_STEP_TIMEOUT_SECONDS,
)
from cerebellum.spec.durations import parse_duration
from cerebellum.spec.schemas import strictify

Duration = Annotated[float, BeforeValidator(parse_duration)]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryPolicy(_Model):
    max: int = Field(0, ge=0, le=20)
    backoff: Literal["fixed", "exponential"] = "exponential"
    base: Duration = 1.0
    max_delay: Duration = 30.0


class OnFailure(_Model):
    fallback: str


class _StepBase(_Model):
    id: Identifier
    description: str = ""
    needs: list[str] = Field(default_factory=list)
    when: str | None = None
    timeout: Duration = DEFAULT_STEP_TIMEOUT_SECONDS
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    on_failure: OnFailure | None = None


class QueryStep(_StepBase):
    type: Literal["query"]
    connector: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    expect: Literal["one", "many", "none", "any"] = "any"


class HttpStep(_StepBase):
    type: Literal["http"]
    connector: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str
    body: Any = None
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)


class MockRule(_Model):
    when: str | None = None
    output: Any


class AiStep(_StepBase):
    type: Literal["ai"]
    prompt: str
    system: str | None = None
    output_schema: dict[str, Any]
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_tokens: int = Field(DEFAULT_AI_MAX_TOKENS, ge=1, le=128000)
    max_repairs: int = Field(2, ge=0, le=5)
    mock: list[MockRule] = Field(default_factory=list)

    @field_validator("output_schema")
    @classmethod
    def _strict_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return strictify(value)


class ValidationRule(_Model):
    expr: str
    message: str


class ValidateStep(_StepBase):
    type: Literal["validate"]
    rules: list[ValidationRule] = Field(min_length=1)


class ApprovalStep(_StepBase):
    type: Literal["approval"]
    title: str
    show: list[str] = Field(default_factory=list)
    timeout: Duration | None = None  # how long to wait for a human; None waits forever
    on_timeout: Literal["approve", "reject"] = "reject"


class TaskStep(_StepBase):
    type: Literal["task"]
    title: str
    assignee: str = "unassigned"
    payload: dict[str, Any] = Field(default_factory=dict)


Step = Annotated[
    QueryStep | HttpStep | AiStep | ValidateStep | ApprovalStep | TaskStep,
    Field(discriminator="type"),
]


class InputField(_Model):
    type: Literal["string", "number", "integer", "boolean", "object", "array"] = "string"
    required: bool = False
    enum: list[Any] | None = None
    description: str = ""


class PostgresConnectorSpec(_Model):
    type: Literal["postgres"]
    dsn: str
    seed: str | None = None


class RestConnectorSpec(_Model):
    type: Literal["rest"]
    base_url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: Duration = 10.0
    health_path: str = "/health"


ConnectorSpec = Annotated[PostgresConnectorSpec | RestConnectorSpec, Field(discriminator="type")]


class Limits(_Model):
    budget_usd: float | None = Field(None, gt=0)
    max_parallel: int = Field(DEFAULT_MAX_PARALLEL, ge=1, le=64)


class Workflow(_Model):
    name: Identifier
    version: int = Field(1, ge=1)
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, InputField] = Field(default_factory=dict)
    connectors: dict[str, ConnectorSpec] = Field(default_factory=dict)
    limits: Limits = Field(default_factory=Limits)
    steps: list[Step] = Field(min_length=1)
    fallbacks: list[Step] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)

    # Filled in by the loader; not part of the YAML surface.
    source_yaml: str = Field("", exclude=True)
    base_dir: str = Field(".", exclude=True)
    digest: str = Field("", exclude=True)

    def step(self, step_id: str) -> Step:
        for step in (*self.steps, *self.fallbacks):
            if step.id == step_id:
                return step
        raise KeyError(step_id)

    @property
    def step_ids(self) -> list[str]:
        return [step.id for step in self.steps]

    @property
    def fallback_ids(self) -> list[str]:
        return [step.id for step in self.fallbacks]

    def is_fallback(self, step_id: str) -> bool:
        return step_id in self.fallback_ids
