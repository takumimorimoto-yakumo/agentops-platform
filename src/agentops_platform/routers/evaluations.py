"""
Evaluation suites and evaluation runs router.

Endpoints:
  POST   /agents/{agentId}/eval-suites
  GET    /agents/{agentId}/eval-suites
  POST   /agents/{agentId}/evaluations
  GET    /agents/{agentId}/evaluations
  GET    /evaluations/{evaluationId}
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status

from config.defaults import EVAL_STATE_QUEUED
from ..evaluator import run_evaluation_sync
from ..models import (
    EvalSuite,
    EvalSuiteCreate,
    EvaluationCreate,
    EvaluationRun,
)
from .auth import AuthDep
from .deps import JudgeDep, StoreDep

router = APIRouter()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@router.post(
    "/agents/{agentId}/eval-suites",
    response_model=EvalSuite,
    status_code=status.HTTP_201_CREATED,
    tags=["evaluations"],
)
def create_eval_suite(agentId: str, body: EvalSuiteCreate, store: StoreDep, _auth: AuthDep) -> EvalSuite:
    """Register an evaluation suite (ADK Eval dataset reference)."""
    suite = store.create_eval_suite(agentId, body)
    if suite is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )
    return suite


@router.get(
    "/agents/{agentId}/eval-suites",
    response_model=list[EvalSuite],
    tags=["evaluations"],
)
def list_eval_suites(agentId: str, store: StoreDep) -> list[EvalSuite]:
    """List evaluation suites."""
    suites = store.list_eval_suites(agentId)
    if suites is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )
    return suites


@router.post(
    "/agents/{agentId}/evaluations",
    response_model=EvaluationRun,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["evaluations"],
)
def start_evaluation(
    agentId: str,
    body: EvaluationCreate,
    store: StoreDep,
    judge: JudgeDep,
    _auth: AuthDep,
) -> EvaluationRun:
    """Start an evaluation run for a version.

    Returns 202 with the run in 'queued' state (per the OpenAPI contract for
    long-running operations).  The foundation wave runs evaluation synchronously
    for simplicity; in production this would dispatch to a Cloud Run Job.
    """
    agent = store.get_agent(agentId)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )

    # Create queued run record
    run = EvaluationRun(
        evaluationId=str(uuid.uuid4()),
        versionId=body.versionId,
        suiteId=body.suiteId,
        state=EVAL_STATE_QUEUED,
        startedAt=_now(),
    )
    store.create_evaluation(run)
    store.register_evaluation_for_agent(agentId, run.evaluationId)

    # Run synchronously (async job dispatch is Wave 2 scope)
    completed = run_evaluation_sync(store, agentId, run, judge)
    return completed


@router.get(
    "/agents/{agentId}/evaluations",
    response_model=list[EvaluationRun],
    tags=["evaluations"],
)
def list_evaluations(
    agentId: str,
    store: StoreDep,
    versionId: str | None = Query(default=None),
) -> list[EvaluationRun]:
    """List evaluation runs."""
    agent = store.get_agent(agentId)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )
    return store.list_evaluations(agentId, version_id=versionId)


@router.get(
    "/evaluations/{evaluationId}",
    response_model=EvaluationRun,
    tags=["evaluations"],
)
def get_evaluation(evaluationId: str, store: StoreDep) -> EvaluationRun:
    """Get an evaluation run with per-axis scores."""
    run = store.get_evaluation(evaluationId)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Evaluation not found"},
        )
    return run
