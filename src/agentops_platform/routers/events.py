"""
Events (audit log) router.

Endpoints:
  GET    /agents/{agentId}/events
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import Event
from .deps import StoreDep

router = APIRouter()


@router.get(
    "/agents/{agentId}/events",
    response_model=list[Event],
    tags=["events"],
)
def list_events(agentId: str, store: StoreDep) -> list[Event]:
    """Audit log of evaluations, deployments, promotions and rollbacks."""
    agent = store.get_agent(agentId)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )
    return store.list_events(agentId)
