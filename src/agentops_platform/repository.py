"""
Repository layer — in-memory implementation with a typed interface.

The MemoryStore class is the default backend (no external dependency).
Replace with a SQL/BigQuery-backed store by implementing the same protocol
and wiring it through the dependency-injection factory in main.py.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol, runtime_checkable

from .models import (
    Agent,
    AgentCreate,
    AgentVersion,
    AgentVersionCreate,
    Deployment,
    DeploymentCreate,
    EvalSuite,
    EvalSuiteCreate,
    EvaluationRun,
    Event,
    MetricIngest,
    RegressionResult,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Store protocol ─────────────────────────────────────────────────────────────
# All methods are synchronous; FastAPI wraps them with run_in_threadpool if needed.


@runtime_checkable
class AgentStore(Protocol):
    def create_agent(self, body: AgentCreate) -> Agent: ...
    def list_agents(self) -> list[Agent]: ...
    def get_agent(self, agent_id: str) -> Agent | None: ...

    def create_version(self, agent_id: str, body: AgentVersionCreate) -> AgentVersion | None: ...
    def list_versions(self, agent_id: str) -> list[AgentVersion] | None: ...
    def get_version(self, version_id: str) -> AgentVersion | None: ...

    def create_eval_suite(self, agent_id: str, body: EvalSuiteCreate) -> EvalSuite | None: ...
    def list_eval_suites(self, agent_id: str) -> list[EvalSuite] | None: ...
    def get_eval_suite(self, suite_id: str) -> EvalSuite | None: ...

    def create_evaluation(self, evaluation: EvaluationRun) -> EvaluationRun: ...
    def list_evaluations(
        self, agent_id: str, version_id: str | None = None
    ) -> list[EvaluationRun]: ...
    def get_evaluation(self, evaluation_id: str) -> EvaluationRun | None: ...
    def update_evaluation(self, evaluation: EvaluationRun) -> EvaluationRun: ...

    def create_regression(self, result: RegressionResult) -> RegressionResult: ...
    def get_regression(self, regression_id: str) -> RegressionResult | None: ...
    def update_regression(self, result: RegressionResult) -> RegressionResult: ...

    def create_deployment(self, agent_id: str, deployment: Deployment) -> Deployment: ...
    def list_deployments(self, agent_id: str) -> list[Deployment]: ...
    def get_deployment(self, deployment_id: str) -> Deployment | None: ...
    def update_deployment(self, deployment: Deployment) -> Deployment: ...

    def store_metrics(self, agent_id: str, ingest: MetricIngest) -> None: ...
    def list_metrics(self, agent_id: str) -> list[MetricIngest]: ...

    def append_event(self, agent_id: str, event: Event) -> None: ...
    def list_events(self, agent_id: str) -> list[Event]: ...


# ── In-memory implementation ──────────────────────────────────────────────────


class MemoryStore:
    """Thread-safe in-memory store (single process only).

    Suitable for local development, testing and demos.
    For production, replace with a persistent backend.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._agents: dict[str, Agent] = {}
        # agent_id → list[AgentVersion]
        self._versions: dict[str, list[AgentVersion]] = defaultdict(list)
        # version_id → AgentVersion (lookup index)
        self._versions_by_id: dict[str, AgentVersion] = {}
        # agent_id → list[EvalSuite]
        self._eval_suites: dict[str, list[EvalSuite]] = defaultdict(list)
        # suite_id → EvalSuite
        self._eval_suites_by_id: dict[str, EvalSuite] = {}
        # evaluation_id → EvaluationRun
        self._evaluations: dict[str, EvaluationRun] = {}
        # agent_id → list[evaluation_id]
        self._agent_evaluations: dict[str, list[str]] = defaultdict(list)
        # regression_id → RegressionResult
        self._regressions: dict[str, RegressionResult] = {}
        # deployment_id → Deployment
        self._deployments: dict[str, Deployment] = {}
        # agent_id → list[deployment_id]
        self._agent_deployments: dict[str, list[str]] = defaultdict(list)
        # agent_id → list[MetricIngest]
        self._metrics: dict[str, list[MetricIngest]] = defaultdict(list)
        # agent_id → list[Event]
        self._events: dict[str, list[Event]] = defaultdict(list)

    # ── Agents ────────────────────────────────────────────────────────────

    def create_agent(self, body: AgentCreate) -> Agent:
        with self._lock:
            agent = Agent(
                agentId=_new_id(),
                createdAt=_now(),
                **body.model_dump(),
            )
            self._agents[agent.agentId] = agent
            return agent

    def list_agents(self) -> list[Agent]:
        with self._lock:
            return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Agent | None:
        with self._lock:
            return self._agents.get(agent_id)

    # ── Versions ──────────────────────────────────────────────────────────

    def create_version(self, agent_id: str, body: AgentVersionCreate) -> AgentVersion | None:
        with self._lock:
            if agent_id not in self._agents:
                return None
            version = AgentVersion(
                versionId=_new_id(),
                createdAt=_now(),
                **body.model_dump(),
            )
            self._versions[agent_id].append(version)
            self._versions_by_id[version.versionId] = version
            return version

    def list_versions(self, agent_id: str) -> list[AgentVersion] | None:
        with self._lock:
            if agent_id not in self._agents:
                return None
            return list(self._versions[agent_id])

    def get_version(self, version_id: str) -> AgentVersion | None:
        with self._lock:
            return self._versions_by_id.get(version_id)

    # ── Eval suites ───────────────────────────────────────────────────────

    def create_eval_suite(self, agent_id: str, body: EvalSuiteCreate) -> EvalSuite | None:
        with self._lock:
            if agent_id not in self._agents:
                return None
            suite = EvalSuite(
                suiteId=_new_id(),
                createdAt=_now(),
                **body.model_dump(),
            )
            self._eval_suites[agent_id].append(suite)
            self._eval_suites_by_id[suite.suiteId] = suite
            return suite

    def list_eval_suites(self, agent_id: str) -> list[EvalSuite] | None:
        with self._lock:
            if agent_id not in self._agents:
                return None
            return list(self._eval_suites[agent_id])

    def get_eval_suite(self, suite_id: str) -> EvalSuite | None:
        with self._lock:
            return self._eval_suites_by_id.get(suite_id)

    # ── Evaluations ───────────────────────────────────────────────────────

    def create_evaluation(self, evaluation: EvaluationRun) -> EvaluationRun:
        with self._lock:
            self._evaluations[evaluation.evaluationId] = evaluation
            return evaluation

    def list_evaluations(
        self, agent_id: str, version_id: str | None = None
    ) -> list[EvaluationRun]:
        with self._lock:
            ids = self._agent_evaluations.get(agent_id, [])
            runs = [self._evaluations[eid] for eid in ids if eid in self._evaluations]
            if version_id is not None:
                runs = [r for r in runs if r.versionId == version_id]
            return runs

    def get_evaluation(self, evaluation_id: str) -> EvaluationRun | None:
        with self._lock:
            return self._evaluations.get(evaluation_id)

    def update_evaluation(self, evaluation: EvaluationRun) -> EvaluationRun:
        with self._lock:
            self._evaluations[evaluation.evaluationId] = evaluation
            return evaluation

    def _register_evaluation_for_agent(self, agent_id: str, evaluation_id: str) -> None:
        """Associate an evaluation with an agent (called from the router)."""
        with self._lock:
            if evaluation_id not in self._agent_evaluations[agent_id]:
                self._agent_evaluations[agent_id].append(evaluation_id)

    # ── Regressions ───────────────────────────────────────────────────────

    def create_regression(self, result: RegressionResult) -> RegressionResult:
        with self._lock:
            self._regressions[result.regressionId] = result
            return result

    def get_regression(self, regression_id: str) -> RegressionResult | None:
        with self._lock:
            return self._regressions.get(regression_id)

    def update_regression(self, result: RegressionResult) -> RegressionResult:
        with self._lock:
            self._regressions[result.regressionId] = result
            return result

    # ── Deployments ───────────────────────────────────────────────────────

    def create_deployment(self, agent_id: str, deployment: Deployment) -> Deployment:
        with self._lock:
            self._deployments[deployment.deploymentId] = deployment
            self._agent_deployments[agent_id].append(deployment.deploymentId)
            return deployment

    def list_deployments(self, agent_id: str) -> list[Deployment]:
        with self._lock:
            ids = self._agent_deployments.get(agent_id, [])
            return [self._deployments[did] for did in ids if did in self._deployments]

    def get_deployment(self, deployment_id: str) -> Deployment | None:
        with self._lock:
            return self._deployments.get(deployment_id)

    def update_deployment(self, deployment: Deployment) -> Deployment:
        with self._lock:
            self._deployments[deployment.deploymentId] = deployment
            return deployment

    # ── Metrics ───────────────────────────────────────────────────────────

    def store_metrics(self, agent_id: str, ingest: MetricIngest) -> None:
        with self._lock:
            self._metrics[agent_id].append(ingest)

    def list_metrics(self, agent_id: str) -> list[MetricIngest]:
        with self._lock:
            return list(self._metrics.get(agent_id, []))

    # ── Events ────────────────────────────────────────────────────────────

    def append_event(self, agent_id: str, event: Event) -> None:
        with self._lock:
            self._events[agent_id].append(event)

    def list_events(self, agent_id: str) -> list[Event]:
        with self._lock:
            return list(self._events.get(agent_id, []))
