"""
Metrics ingest router.

Endpoints:
  POST   /agents/{agentId}/metrics
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from ..models import MetricIngest
from .deps import StoreDep

router = APIRouter()


@router.post(
    "/agents/{agentId}/metrics",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["metrics"],
)
def ingest_metrics(
    agentId: str,
    body: MetricIngest,
    store: StoreDep,
) -> Response:
    """Ingest external outcome metrics for an agent version.

    Entry point for application-level feedback loops.  Managed agents push
    domain outcomes which the platform folds into evaluation and rollback
    decisions.

    TODO (Wave 2 — meta-agent):
      - After ingesting, trigger a policy re-evaluation if a canary deployment
        is active for the version referenced in body.versionId.
      - Feed outcome metrics into the meta-agent's decision context.
    """
    agent = store.get_agent(agentId)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )
    store.store_metrics(agentId, body)
    return Response(status_code=status.HTTP_202_ACCEPTED)
