"""
Deployments router.

Endpoints:
  POST   /agents/{agentId}/deployments
  GET    /agents/{agentId}/deployments
  GET    /deployments/{deploymentId}
  POST   /deployments/{deploymentId}:promote
  POST   /deployments/{deploymentId}:rollback
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status

from config.defaults import (
    EVENT_DEPLOYMENT_CREATED,
)
from ..models import (
    Deployment,
    DeploymentCreate,
    Event,
    RollbackRequest,
)
from ..rollback import (
    TERMINAL_STATES,
    advance_canary_step,
    promote_deployment,
    rollback_deployment,
)
from .auth import AuthDep
from .deps import StoreDep

router = APIRouter()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _make_event(
    event_type: str,
    deployment_id: str,
    detail: str | None = None,
) -> Event:
    return Event(
        eventId=_new_id(),
        type=event_type,  # type: ignore[arg-type]
        occurredAt=_now(),
        deploymentId=deployment_id,
        detail=detail,
    )


@router.post(
    "/agents/{agentId}/deployments",
    response_model=Deployment,
    status_code=status.HTTP_201_CREATED,
    tags=["deployments"],
)
def create_deployment(
    agentId: str,
    body: DeploymentCreate,
    store: StoreDep,
    _auth: AuthDep,
) -> Deployment:
    """Deploy a version with a canary strategy and rollback policy."""
    agent = store.get_agent(agentId)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )

    now = _now()

    # Determine initial state and traffic based on strategy
    if body.strategy.type == "all-at-once":
        initial_state = "promoted"
        initial_traffic = 100
    else:
        # canary: start pending, first step will be applied on first advance
        initial_state = "pending"
        # Apply first canary step immediately if steps are provided
        steps = body.strategy.steps or []
        initial_traffic = steps[0] if steps else 0

        if steps:
            initial_state = "canary"

    deployment = Deployment(
        deploymentId=_new_id(),
        versionId=body.versionId,
        strategy=body.strategy,
        rollbackPolicy=body.rollbackPolicy,
        state=initial_state,  # type: ignore[arg-type]
        currentTrafficPercent=initial_traffic,
        createdAt=now,
        updatedAt=now,
    )

    stored = store.create_deployment(agentId, deployment)

    # Emit deployment.created event
    event = _make_event(
        EVENT_DEPLOYMENT_CREATED,
        stored.deploymentId,
        detail=f"version={body.versionId} strategy={body.strategy.type}",
    )
    store.append_event(agentId, event)

    # If canary, also emit first step_advanced event
    if initial_state == "canary" and initial_traffic > 0:
        step_event = _make_event(
            "deployment.step_advanced",
            stored.deploymentId,
            detail=f"traffic advanced to {initial_traffic}%",
        )
        store.append_event(agentId, step_event)

    return stored


@router.get(
    "/agents/{agentId}/deployments",
    response_model=list[Deployment],
    tags=["deployments"],
)
def list_deployments(agentId: str, store: StoreDep) -> list[Deployment]:
    """List deployments."""
    agent = store.get_agent(agentId)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Agent not found"},
        )
    return store.list_deployments(agentId)


@router.get(
    "/deployments/{deploymentId}",
    response_model=Deployment,
    tags=["deployments"],
)
def get_deployment(deploymentId: str, store: StoreDep) -> Deployment:
    """Get deployment status (canary ratio, health, state)."""
    deployment = store.get_deployment(deploymentId)
    if deployment is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Deployment not found"},
        )
    return deployment


@router.post(
    "/deployments/{deploymentId}:promote",
    response_model=Deployment,
    tags=["deployments"],
)
def promote_deployment_endpoint(deploymentId: str, store: StoreDep, _auth: AuthDep) -> Deployment:
    """Promote the canary to 100% traffic."""
    deployment = store.get_deployment(deploymentId)
    if deployment is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Deployment not found"},
        )
    if deployment.state in TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conflict",
                "message": f"Deployment is already in terminal state: {deployment.state}",
            },
        )

    # Find the agent_id for this deployment (needed for event emission)
    # We scan agent deployments to find the owner.  In a SQL store this
    # would be a join; here we track it in the router.
    agent_id = _find_agent_id_for_deployment(store, deploymentId)

    updated, event = promote_deployment(deployment)
    store.update_deployment(updated)
    if agent_id:
        store.append_event(agent_id, event)
    return updated


@router.post(
    "/deployments/{deploymentId}:rollback",
    response_model=Deployment,
    tags=["deployments"],
)
def rollback_deployment_endpoint(
    deploymentId: str,
    store: StoreDep,
    _auth: AuthDep,
    body: Optional[RollbackRequest] = None,
) -> Deployment:
    """Roll back to the previous stable version."""
    deployment = store.get_deployment(deploymentId)
    if deployment is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Deployment not found"},
        )
    if deployment.state in TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conflict",
                "message": f"Deployment is already in terminal state: {deployment.state}",
            },
        )

    reason = body.reason if body else None
    agent_id = _find_agent_id_for_deployment(store, deploymentId)

    updated, event = rollback_deployment(deployment, reason=reason)
    store.update_deployment(updated)
    if agent_id:
        store.append_event(agent_id, event)
    return updated


def _find_agent_id_for_deployment(store: StoreDep, deployment_id: str) -> str | None:
    """Scan agents to find which one owns the deployment.

    This is O(n agents) — acceptable for the in-memory store at demo scale.
    A persistent store would store agentId on the deployment record.
    """
    for agent in store.list_agents():
        deployments = store.list_deployments(agent.agentId)
        for d in deployments:
            if d.deploymentId == deployment_id:
                return agent.agentId
    return None
