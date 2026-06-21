"""
Evaluator service — orchestrates evaluation runs and regression checks.

This module coordinates between the repository, the judge and the state
machine.  It is called by the FastAPI routers; it does not own HTTP concerns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from config.defaults import (
    EVAL_STATE_FAILED,
    EVAL_STATE_RUNNING,
    EVAL_STATE_SUCCEEDED,
    EVENT_EVALUATION_FINISHED,
    EVENT_REGRESSION_FINISHED,
)
from .judge import JudgeProtocol
from .models import (
    AxisScore,
    EvaluationRun,
    Event,
    RegressionDelta,
    RegressionResult,
)
from .repository import MemoryStore


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def run_evaluation_sync(
    store: MemoryStore,
    agent_id: str,
    evaluation: EvaluationRun,
    judge: JudgeProtocol,
) -> EvaluationRun:
    """Execute an evaluation run synchronously using the given judge.

    In production this would be dispatched to a Cloud Run Job / Task Queue.
    For the foundation wave, evaluation is synchronous so the state machine
    and tests can observe results immediately.

    Args:
        store: The repository to read eval suites and persist results.
        agent_id: Parent agent id (for event association).
        evaluation: The EvaluationRun record (state == "queued" on entry).
        judge: The judge implementation to use for scoring.

    Returns:
        The updated EvaluationRun with state == "succeeded" (or "failed").
    """
    # Transition to running
    running = evaluation.model_copy(
        update={"state": EVAL_STATE_RUNNING, "startedAt": _now()}
    )
    store.update_evaluation(running)

    suite = store.get_eval_suite(evaluation.suiteId)
    if suite is None:
        failed = running.model_copy(
            update={"state": EVAL_STATE_FAILED, "finishedAt": _now()}
        )
        store.update_evaluation(failed)
        return failed

    try:
        scores: list[AxisScore] = judge.evaluate(running, suite)
        succeeded = running.model_copy(
            update={
                "state": EVAL_STATE_SUCCEEDED,
                "scores": scores,
                "finishedAt": _now(),
            }
        )
        store.update_evaluation(succeeded)
        _emit_event(
            store,
            agent_id,
            EVENT_EVALUATION_FINISHED,
            evaluation_id=evaluation.evaluationId,
            detail=f"evaluation succeeded for version {evaluation.versionId}",
        )
        return succeeded
    except Exception as exc:  # noqa: BLE001
        failed = running.model_copy(
            update={
                "state": EVAL_STATE_FAILED,
                "finishedAt": _now(),
            }
        )
        store.update_evaluation(failed)
        _emit_event(
            store,
            agent_id,
            EVENT_EVALUATION_FINISHED,
            evaluation_id=evaluation.evaluationId,
            detail=f"evaluation failed: {exc}",
        )
        return failed


def run_regression_sync(
    store: MemoryStore,
    agent_id: str,
    regression: RegressionResult,
    baseline_eval: EvaluationRun,
    candidate_eval: EvaluationRun,
) -> RegressionResult:
    """Compare baseline and candidate evaluations and record per-axis deltas.

    Args:
        store: The repository.
        agent_id: Parent agent id (for event association).
        regression: The RegressionResult record to update.
        baseline_eval: Completed evaluation of the baseline version.
        candidate_eval: Completed evaluation of the candidate version.

    Returns:
        The updated RegressionResult with verdict and deltas.
    """
    running = regression.model_copy(update={"state": "running"})
    store.update_regression(running)

    baseline_scores = {s.axis: s.score for s in baseline_eval.scores}
    candidate_scores = {s.axis: s.score for s in candidate_eval.scores}

    deltas: list[RegressionDelta] = []
    for axis in set(baseline_scores) | set(candidate_scores):
        b = baseline_scores.get(axis, 0.0)
        c = candidate_scores.get(axis, 0.0)
        deltas.append(
            RegressionDelta(axis=axis, baseline=b, candidate=c, delta=c - b)  # type: ignore[arg-type]
        )

    # Verdict: fail if any normalized axis (drift/trajectory) regressed below 0
    verdict: str = "pass"
    for d in deltas:
        if d.axis in ("drift", "trajectory") and d.delta < 0:
            verdict = "fail"
            break

    done = running.model_copy(
        update={
            "state": "succeeded",
            "verdict": verdict,
            "deltas": deltas,
            "baselineEvaluationId": baseline_eval.evaluationId,
            "candidateEvaluationId": candidate_eval.evaluationId,
        }
    )
    store.update_regression(done)
    _emit_event(
        store,
        agent_id,
        EVENT_REGRESSION_FINISHED,
        detail=f"regression verdict={verdict}",
    )
    return done


def _emit_event(
    store: MemoryStore,
    agent_id: str,
    event_type: str,
    evaluation_id: str | None = None,
    deployment_id: str | None = None,
    detail: str | None = None,
) -> None:
    event = Event(
        eventId=_new_id(),
        type=event_type,  # type: ignore[arg-type]
        occurredAt=_now(),
        evaluationId=evaluation_id,
        deploymentId=deployment_id,
        detail=detail,
    )
    store.append_event(agent_id, event)
