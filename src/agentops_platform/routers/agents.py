"""
Agents and Versions router.

Endpoints:
  POST   /agents
  GET    /agents
  GET    /agents/{agentId}
  POST   /agents/{agentId}/versions
  GET    /agents/{agentId}/versions
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..models import Agent, AgentCreate, AgentVersion, AgentVersionCreate
from .auth import AuthDep
from .deps import StoreDep

router = APIRouter()


@router.post("/agents", response_model=Agent, status_code=status.HTTP_201_CREATED, tags=["agents"])
def register_agent(body: AgentCreate, store: StoreDep, _auth: AuthDep) -> Agent:
    """Register an agent under management."""
    return store.create_agent(body)


@router.get("/agents", response_model=list[Agent], tags=["agents"])
def list_agents(store: StoreDep) -> list[Agent]:
    """List managed agents."""
    return store.list_agents()


@router.get("/agents/{agentId}", response_model=Agent, tags=["agents"])
def get_agent(agentId: str, store: StoreDep) -> Agent:
    """Get an agent."""
    agent = store.get_agent(agentId)
    if agent is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Agent not found"})
    return agent


@router.post(
    "/agents/{agentId}/versions",
    response_model=AgentVersion,
    status_code=status.HTTP_201_CREATED,
    tags=["versions"],
)
def create_version(agentId: str, body: AgentVersionCreate, store: StoreDep, _auth: AuthDep) -> AgentVersion:
    """Register a new agent version (deployable artifact)."""
    version = store.create_version(agentId, body)
    if version is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Agent not found"})
    return version


@router.get(
    "/agents/{agentId}/versions",
    response_model=list[AgentVersion],
    tags=["versions"],
)
def list_versions(agentId: str, store: StoreDep) -> list[AgentVersion]:
    """List versions of an agent."""
    versions = store.list_versions(agentId)
    if versions is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Agent not found"})
    return versions
