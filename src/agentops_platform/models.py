"""
Pydantic models matching the OpenAPI 3.1 contract (api/openapi.yaml).

All field names and enum values are kept identical to the contract so that
JSON serialisation is wire-compatible without aliasing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Shared ────────────────────────────────────────────────────────────────────


class Error(BaseModel):
    code: str
    message: str


# ── Agents ────────────────────────────────────────────────────────────────────


class AgentCreate(BaseModel):
    name: str = Field(description="Unique agent name")
    description: str | None = None
    runtime: Literal["adk-cloud-run"]


class Agent(AgentCreate):
    agentId: str
    createdAt: datetime


# ── Versions ──────────────────────────────────────────────────────────────────


class AgentVersionCreate(BaseModel):
    image: str = Field(description="Container image digest")
    model: str = Field(description="LLM model id (e.g. a Gemini model)")
    promptDigest: str = Field(description="Content digest of prompts/rules that define behavior")
    configDigest: str | None = None
    gitCommit: str | None = None


class AgentVersion(AgentVersionCreate):
    versionId: str
    createdAt: datetime


# ── Eval Suites ───────────────────────────────────────────────────────────────


class EvalSuiteCreate(BaseModel):
    name: str
    adkEvalSetRef: str = Field(description="GCS or repo path to the ADK Eval set definition")
    judgeModel: str | None = Field(
        default=None, description="Model id used as judge for drift scoring"
    )


class EvalSuite(EvalSuiteCreate):
    suiteId: str
    createdAt: datetime


# ── Evaluations ───────────────────────────────────────────────────────────────


class EvaluationCreate(BaseModel):
    versionId: str
    suiteId: str


class AxisScore(BaseModel):
    axis: Literal["drift", "trajectory", "cost", "latency"]
    score: float = Field(
        description=(
            "Normalized 0..1 (higher is better) for drift/trajectory; "
            "raw value for cost (USD/task) and latency (ms)"
        )
    )
    threshold: float | None = None
    pass_: bool | None = Field(default=None, alias="pass")

    model_config = {"populate_by_name": True}

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        """Serialize 'pass_' as 'pass' to match the OpenAPI contract."""
        data = super().model_dump(**kwargs)
        if "pass_" in data:
            data["pass"] = data.pop("pass_")
        return data


class EvaluationRun(BaseModel):
    evaluationId: str
    versionId: str
    suiteId: str
    state: Literal["queued", "running", "succeeded", "failed"]
    scores: list[AxisScore] = Field(default_factory=list)
    traceUrl: str | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None


# ── Regressions ───────────────────────────────────────────────────────────────


class RegressionCreate(BaseModel):
    baselineVersionId: str
    candidateVersionId: str
    suiteId: str


class RegressionDelta(BaseModel):
    axis: Literal["drift", "trajectory", "cost", "latency"]
    baseline: float
    candidate: float
    delta: float


class RegressionResult(BaseModel):
    regressionId: str
    state: Literal["queued", "running", "succeeded", "failed"]
    verdict: Literal["pass", "fail"] | None = None
    deltas: list[RegressionDelta] = Field(default_factory=list)
    baselineEvaluationId: str | None = None
    candidateEvaluationId: str | None = None


# ── Deployments ───────────────────────────────────────────────────────────────


class RollbackPolicy(BaseModel):
    """Auto-rollback triggers evaluated during the canary window."""

    windowMinutes: int | None = None
    maxDriftDrop: float | None = None
    maxTrajectoryDrop: float | None = None
    maxCostIncreaseRatio: float | None = None
    maxLatencyP95Ms: float | None = None


class CanaryStrategy(BaseModel):
    type: Literal["canary", "all-at-once"]
    steps: list[int] | None = Field(
        default=None,
        description="Traffic percentages for successive canary steps",
    )


class DeploymentCreate(BaseModel):
    versionId: str
    strategy: CanaryStrategy
    rollbackPolicy: RollbackPolicy | None = None


class Deployment(DeploymentCreate):
    deploymentId: str
    state: Literal["pending", "canary", "promoted", "rolled_back", "failed"]
    currentTrafficPercent: int | None = None
    previousStableVersionId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


# ── Metrics ───────────────────────────────────────────────────────────────────


class MetricSample(BaseModel):
    name: str = Field(description="Metric name (e.g. retention_rate)")
    value: float
    observedAt: datetime
    dimensions: dict[str, str] | None = None


class MetricIngest(BaseModel):
    versionId: str
    source: str = Field(description="Origin of the outcome metric")
    samples: list[MetricSample]


# ── Events ────────────────────────────────────────────────────────────────────


class Event(BaseModel):
    eventId: str
    type: Literal[
        "evaluation.finished",
        "regression.finished",
        "deployment.created",
        "deployment.step_advanced",
        "deployment.promoted",
        "deployment.rolled_back",
    ]
    occurredAt: datetime
    deploymentId: str | None = None
    evaluationId: str | None = None
    detail: str | None = None


# ── Rollback request body (optional) ─────────────────────────────────────────


class RollbackRequest(BaseModel):
    reason: str | None = None
