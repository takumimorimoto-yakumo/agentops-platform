"""
Shared test fixtures for AgentOps Platform tests.
"""

from __future__ import annotations

import sys
import os

# Ensure the src and config packages are importable in tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

from agentops_platform.repository import MemoryStore
from agentops_platform.judge import StubJudge
from agentops_platform.routers.deps import get_store, get_judge_dep
from agentops_platform.main import app


@pytest.fixture
def store() -> MemoryStore:
    """Fresh MemoryStore for each test."""
    return MemoryStore()


@pytest.fixture
def stub_judge() -> StubJudge:
    """Default stub judge."""
    return StubJudge()


@pytest.fixture
def client(store: MemoryStore, stub_judge: StubJudge) -> TestClient:
    """TestClient with overridden store and judge dependencies."""
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_judge_dep] = lambda: stub_judge
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def degraded_judge() -> StubJudge:
    """Stub judge that returns deliberately bad scores to trigger rollback."""
    return StubJudge(
        drift_score=0.40,        # well below 0.70 threshold
        trajectory_score=0.35,   # well below 0.75 threshold
        cost_usd=0.05,           # above 0.01 threshold
        latency_ms=5000.0,       # above 2000 ms threshold
    )


@pytest.fixture
def degraded_client(store: MemoryStore, degraded_judge: StubJudge) -> TestClient:
    """TestClient that uses the degraded judge (triggers rollback policy)."""
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_judge_dep] = lambda: degraded_judge
    yield TestClient(app)
    app.dependency_overrides.clear()
