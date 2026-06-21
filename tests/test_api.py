"""
Integration tests for all API endpoints.

Covers:
- Happy path (201/200/202) for each endpoint
- 404 for missing resources
- Canary deployment state transitions
- Metrics ingest
- Events audit log
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _register_agent(client: TestClient, name: str = "test-agent") -> dict:
    resp = client.post(
        "/v1/agents",
        json={"name": name, "runtime": "adk-cloud-run"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_version(client: TestClient, agent_id: str) -> dict:
    resp = client.post(
        f"/v1/agents/{agent_id}/versions",
        json={
            "image": "gcr.io/project/agent@sha256:abc123",
            "model": "gemini-2.0-flash",
            "promptDigest": "sha256:deadbeef",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_suite(client: TestClient, agent_id: str) -> dict:
    resp = client.post(
        f"/v1/agents/{agent_id}/eval-suites",
        json={
            "name": "smoke-suite",
            "adkEvalSetRef": "gs://bucket/eval-set.json",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Agent tests ───────────────────────────────────────────────────────────────


class TestAgents:
    def test_register_and_get_agent(self, client: TestClient) -> None:
        agent = _register_agent(client, "my-agent")
        assert agent["name"] == "my-agent"
        assert agent["runtime"] == "adk-cloud-run"
        assert "agentId" in agent
        assert "createdAt" in agent

        resp = client.get(f"/v1/agents/{agent['agentId']}")
        assert resp.status_code == 200
        assert resp.json()["agentId"] == agent["agentId"]

    def test_list_agents(self, client: TestClient) -> None:
        _register_agent(client, "agent-a")
        _register_agent(client, "agent-b")
        resp = client.get("/v1/agents")
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_get_agent_not_found(self, client: TestClient) -> None:
        resp = client.get("/v1/agents/nonexistent-id")
        assert resp.status_code == 404

    def test_agent_with_description(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/agents",
            json={
                "name": "documented-agent",
                "description": "An agent with docs",
                "runtime": "adk-cloud-run",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["description"] == "An agent with docs"


# ── Version tests ─────────────────────────────────────────────────────────────


class TestVersions:
    def test_create_and_list_versions(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])
        assert "versionId" in version
        assert version["model"] == "gemini-2.0-flash"

        resp = client.get(f"/v1/agents/{agent['agentId']}/versions")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["versionId"] == version["versionId"]

    def test_create_version_agent_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/agents/bad-id/versions",
            json={
                "image": "gcr.io/x/y@sha256:abc",
                "model": "gemini-2.0-flash",
                "promptDigest": "sha256:aaa",
            },
        )
        assert resp.status_code == 404


# ── Eval suite tests ──────────────────────────────────────────────────────────


class TestEvalSuites:
    def test_create_and_list_eval_suites(self, client: TestClient) -> None:
        agent = _register_agent(client)
        suite = _create_suite(client, agent["agentId"])
        assert "suiteId" in suite
        assert suite["name"] == "smoke-suite"

        resp = client.get(f"/v1/agents/{agent['agentId']}/eval-suites")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_eval_suite_agent_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/agents/bad-id/eval-suites",
            json={"name": "x", "adkEvalSetRef": "gs://bucket/x"},
        )
        assert resp.status_code == 404


# ── Evaluation tests ──────────────────────────────────────────────────────────


class TestEvaluations:
    def test_start_evaluation_returns_202(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])
        suite = _create_suite(client, agent["agentId"])

        resp = client.post(
            f"/v1/agents/{agent['agentId']}/evaluations",
            json={
                "versionId": version["versionId"],
                "suiteId": suite["suiteId"],
            },
        )
        assert resp.status_code == 202, resp.text
        run = resp.json()
        assert run["state"] == "succeeded"
        assert len(run["scores"]) == 4

    def test_get_evaluation(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])
        suite = _create_suite(client, agent["agentId"])

        run = client.post(
            f"/v1/agents/{agent['agentId']}/evaluations",
            json={"versionId": version["versionId"], "suiteId": suite["suiteId"]},
        ).json()

        resp = client.get(f"/v1/evaluations/{run['evaluationId']}")
        assert resp.status_code == 200
        assert resp.json()["evaluationId"] == run["evaluationId"]

    def test_get_evaluation_not_found(self, client: TestClient) -> None:
        resp = client.get("/v1/evaluations/nonexistent")
        assert resp.status_code == 404

    def test_list_evaluations(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])
        suite = _create_suite(client, agent["agentId"])

        client.post(
            f"/v1/agents/{agent['agentId']}/evaluations",
            json={"versionId": version["versionId"], "suiteId": suite["suiteId"]},
        )
        client.post(
            f"/v1/agents/{agent['agentId']}/evaluations",
            json={"versionId": version["versionId"], "suiteId": suite["suiteId"]},
        )

        resp = client.get(f"/v1/agents/{agent['agentId']}/evaluations")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_evaluations_filter_by_version(self, client: TestClient) -> None:
        agent = _register_agent(client)
        v1 = _create_version(client, agent["agentId"])
        v2 = _create_version(client, agent["agentId"])
        suite = _create_suite(client, agent["agentId"])

        client.post(
            f"/v1/agents/{agent['agentId']}/evaluations",
            json={"versionId": v1["versionId"], "suiteId": suite["suiteId"]},
        )
        client.post(
            f"/v1/agents/{agent['agentId']}/evaluations",
            json={"versionId": v2["versionId"], "suiteId": suite["suiteId"]},
        )

        resp = client.get(
            f"/v1/agents/{agent['agentId']}/evaluations",
            params={"versionId": v1["versionId"]},
        )
        assert resp.status_code == 200
        runs = resp.json()
        assert all(r["versionId"] == v1["versionId"] for r in runs)
        assert len(runs) == 1


# ── Regression tests ──────────────────────────────────────────────────────────


class TestRegressions:
    def test_start_regression_check(self, client: TestClient) -> None:
        agent = _register_agent(client)
        v_baseline = _create_version(client, agent["agentId"])
        v_candidate = _create_version(client, agent["agentId"])
        suite = _create_suite(client, agent["agentId"])

        resp = client.post(
            f"/v1/agents/{agent['agentId']}/regressions",
            json={
                "baselineVersionId": v_baseline["versionId"],
                "candidateVersionId": v_candidate["versionId"],
                "suiteId": suite["suiteId"],
            },
        )
        assert resp.status_code == 202, resp.text
        result = resp.json()
        assert result["state"] == "succeeded"
        assert result["verdict"] in ("pass", "fail")
        assert len(result["deltas"]) > 0

    def test_get_regression_not_found(self, client: TestClient) -> None:
        resp = client.get("/v1/regressions/nonexistent")
        assert resp.status_code == 404


# ── Deployment tests ──────────────────────────────────────────────────────────


class TestDeployments:
    def test_create_canary_deployment(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        resp = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "canary", "steps": [10, 25, 50, 100]},
            },
        )
        assert resp.status_code == 201, resp.text
        dep = resp.json()
        assert dep["state"] == "canary"
        assert dep["currentTrafficPercent"] == 10

    def test_create_all_at_once_deployment(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        resp = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "all-at-once"},
            },
        )
        assert resp.status_code == 201, resp.text
        dep = resp.json()
        assert dep["state"] == "promoted"
        assert dep["currentTrafficPercent"] == 100

    def test_get_deployment(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        dep = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "canary", "steps": [10, 50, 100]},
            },
        ).json()

        resp = client.get(f"/v1/deployments/{dep['deploymentId']}")
        assert resp.status_code == 200
        assert resp.json()["deploymentId"] == dep["deploymentId"]

    def test_get_deployment_not_found(self, client: TestClient) -> None:
        resp = client.get("/v1/deployments/nonexistent")
        assert resp.status_code == 404

    def test_list_deployments(self, client: TestClient) -> None:
        agent = _register_agent(client)
        v1 = _create_version(client, agent["agentId"])
        v2 = _create_version(client, agent["agentId"])

        client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={"versionId": v1["versionId"], "strategy": {"type": "all-at-once"}},
        )
        client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={"versionId": v2["versionId"], "strategy": {"type": "all-at-once"}},
        )

        resp = client.get(f"/v1/agents/{agent['agentId']}/deployments")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_promote_canary(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        dep = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "canary", "steps": [10, 100]},
            },
        ).json()

        resp = client.post(f"/v1/deployments/{dep['deploymentId']}:promote")
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "promoted"
        assert resp.json()["currentTrafficPercent"] == 100

    def test_rollback_canary(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        dep = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "canary", "steps": [10, 50]},
            },
        ).json()

        resp = client.post(
            f"/v1/deployments/{dep['deploymentId']}:rollback",
            json={"reason": "manual rollback test"},
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["state"] == "rolled_back"
        assert result["currentTrafficPercent"] == 0

    def test_cannot_promote_already_promoted(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        dep = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={"versionId": version["versionId"], "strategy": {"type": "all-at-once"}},
        ).json()

        resp = client.post(f"/v1/deployments/{dep['deploymentId']}:promote")
        assert resp.status_code == 409

    def test_cannot_rollback_already_rolled_back(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        dep = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "canary", "steps": [10]},
            },
        ).json()

        client.post(f"/v1/deployments/{dep['deploymentId']}:rollback")
        resp = client.post(f"/v1/deployments/{dep['deploymentId']}:rollback")
        assert resp.status_code == 409


# ── Metrics ingest tests ──────────────────────────────────────────────────────


class TestMetrics:
    def test_ingest_metrics_returns_202(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        resp = client.post(
            f"/v1/agents/{agent['agentId']}/metrics",
            json={
                "versionId": version["versionId"],
                "source": "test-source",
                "samples": [
                    {
                        "name": "retention_rate",
                        "value": 0.72,
                        "observedAt": "2026-06-21T00:00:00Z",
                    }
                ],
            },
        )
        assert resp.status_code == 202

    def test_ingest_metrics_agent_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/agents/bad-id/metrics",
            json={
                "versionId": "v1",
                "source": "test",
                "samples": [
                    {"name": "x", "value": 1.0, "observedAt": "2026-06-21T00:00:00Z"}
                ],
            },
        )
        assert resp.status_code == 404


# ── Events audit log tests ────────────────────────────────────────────────────


class TestEvents:
    def test_deployment_events_recorded(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        dep = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "canary", "steps": [10, 50]},
            },
        ).json()

        client.post(f"/v1/deployments/{dep['deploymentId']}:promote")

        resp = client.get(f"/v1/agents/{agent['agentId']}/events")
        assert resp.status_code == 200
        events = resp.json()
        event_types = [e["type"] for e in events]
        assert "deployment.created" in event_types
        assert "deployment.promoted" in event_types

    def test_rollback_event_recorded(self, client: TestClient) -> None:
        agent = _register_agent(client)
        version = _create_version(client, agent["agentId"])

        dep = client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "canary", "steps": [10]},
            },
        ).json()

        client.post(f"/v1/deployments/{dep['deploymentId']}:rollback")

        resp = client.get(f"/v1/agents/{agent['agentId']}/events")
        assert resp.status_code == 200
        event_types = [e["type"] for e in resp.json()]
        assert "deployment.rolled_back" in event_types

    def test_events_agent_not_found(self, client: TestClient) -> None:
        resp = client.get("/v1/agents/nonexistent/events")
        assert resp.status_code == 404
