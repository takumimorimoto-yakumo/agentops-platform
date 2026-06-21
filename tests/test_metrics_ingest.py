"""
Tests for metrics ingest and the downstream feedback loop.

Verifies:
- Multiple samples are ingested and stored
- Ingest with dimensions (labels)
- Ingest for a non-existent agent returns 404
- The stored metrics can be retrieved from the store
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentops_platform.repository import MemoryStore


class TestMetricsIngest:
    def _setup_agent_version(self, client: TestClient) -> tuple[str, str]:
        agent = client.post(
            "/v1/agents",
            json={"name": "metrics-test-agent", "runtime": "adk-cloud-run"},
        ).json()
        version = client.post(
            f"/v1/agents/{agent['agentId']}/versions",
            json={
                "image": "gcr.io/project/agent@sha256:abc",
                "model": "gemini-2.0-flash",
                "promptDigest": "sha256:111",
            },
        ).json()
        return agent["agentId"], version["versionId"]

    def test_ingest_multiple_samples(self, client: TestClient, store: MemoryStore) -> None:
        agent_id, version_id = self._setup_agent_version(client)

        resp = client.post(
            f"/v1/agents/{agent_id}/metrics",
            json={
                "versionId": version_id,
                "source": "analytics",
                "samples": [
                    {"name": "retention_rate", "value": 0.72, "observedAt": "2026-06-21T00:00:00Z"},
                    {"name": "click_through_rate", "value": 0.05, "observedAt": "2026-06-21T00:01:00Z"},
                ],
            },
        )
        assert resp.status_code == 202

        ingests = store.list_metrics(agent_id)
        assert len(ingests) == 1
        assert len(ingests[0].samples) == 2
        sample_names = [s.name for s in ingests[0].samples]
        assert "retention_rate" in sample_names
        assert "click_through_rate" in sample_names

    def test_ingest_with_dimensions(self, client: TestClient, store: MemoryStore) -> None:
        agent_id, version_id = self._setup_agent_version(client)

        resp = client.post(
            f"/v1/agents/{agent_id}/metrics",
            json={
                "versionId": version_id,
                "source": "youtube",
                "samples": [
                    {
                        "name": "view_duration_seconds",
                        "value": 45.3,
                        "observedAt": "2026-06-21T00:00:00Z",
                        "dimensions": {"region": "JP", "device": "mobile"},
                    }
                ],
            },
        )
        assert resp.status_code == 202

        ingests = store.list_metrics(agent_id)
        sample = ingests[0].samples[0]
        assert sample.dimensions == {"region": "JP", "device": "mobile"}

    def test_ingest_multiple_batches(self, client: TestClient, store: MemoryStore) -> None:
        agent_id, version_id = self._setup_agent_version(client)

        for i in range(3):
            client.post(
                f"/v1/agents/{agent_id}/metrics",
                json={
                    "versionId": version_id,
                    "source": f"source-{i}",
                    "samples": [
                        {"name": "metric", "value": float(i), "observedAt": "2026-06-21T00:00:00Z"}
                    ],
                },
            )

        ingests = store.list_metrics(agent_id)
        assert len(ingests) == 3

    def test_ingest_agent_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/agents/nonexistent/metrics",
            json={
                "versionId": "v1",
                "source": "test",
                "samples": [
                    {"name": "x", "value": 1.0, "observedAt": "2026-06-21T00:00:00Z"}
                ],
            },
        )
        assert resp.status_code == 404
