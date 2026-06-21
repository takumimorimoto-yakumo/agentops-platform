"""
Regression check router.

Endpoints:
  POST   /agents/{agentId}/regressions
  GET    /regressions/{regressionId}
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from ..evaluator import run_evaluation_sync, run_regression_sync
from ..models import (
    EvaluationCreate,
    EvaluationRun,
    RegressionCreate,
    RegressionResult,
)
from config.defaults import EVAL_STATE_QUEUED
from datetime import datetime, timezone
from .deps import JudgeDep, StoreDep

router = APIRouter()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@router.post(
    "/agents/{agentId}/regressions",
    response_model=RegressionResult,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["regressions"],
)
def start_regression_check(
    agentId: str,
    body: RegressionCreate,
    store: StoreDep,
    judge: JudgeDep,
) -> RegressionResult:
    """Compare candidate version behavior against a baseline version."""
    agent = store.get_agent(agentId)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )

    regression = RegressionResult(
        regressionId=str(uuid.uuid4()),
        state="queued",
    )
    store.create_regression(regression)

    # Evaluate baseline version
    baseline_run = EvaluationRun(
        evaluationId=str(uuid.uuid4()),
        versionId=body.baselineVersionId,
        suiteId=body.suiteId,
        state=EVAL_STATE_QUEUED,
        startedAt=_now(),
    )
    store.create_evaluation(baseline_run)
    store._register_evaluation_for_agent(agentId, baseline_run.evaluationId)
    baseline_eval = run_evaluation_sync(store, agentId, baseline_run, judge)

    # Evaluate candidate version
    candidate_run = EvaluationRun(
        evaluationId=str(uuid.uuid4()),
        versionId=body.candidateVersionId,
        suiteId=body.suiteId,
        state=EVAL_STATE_QUEUED,
        startedAt=_now(),
    )
    store.create_evaluation(candidate_run)
    store._register_evaluation_for_agent(agentId, candidate_run.evaluationId)
    candidate_eval = run_evaluation_sync(store, agentId, candidate_run, judge)

    # Compare
    result = run_regression_sync(
        store, agentId, regression, baseline_eval, candidate_eval
    )
    return result


@router.get(
    "/regressions/{regressionId}",
    response_model=RegressionResult,
    tags=["regressions"],
)
def get_regression(regressionId: str, store: StoreDep) -> RegressionResult:
    """Get a regression check result."""
    result = store.get_regression(regressionId)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Regression check not found"},
        )
    return result
