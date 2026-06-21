"""
Tests for the Google ID token authentication dependency.

Covers:
- auth_mode=none (default): all mutating endpoints pass without Authorization header.
- auth_mode=google-id-token: missing header returns 401.
- auth_mode=google-id-token: malformed / invalid token returns 401.
- auth_mode=google-id-token: valid token (mocked) is accepted.

The token verification function (_verify_google_id_token) is patched so that
tests never make real network calls to Google's public-key endpoint.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentops_platform.main import app
from agentops_platform.repository import MemoryStore
from agentops_platform.judge import StubJudge
from agentops_platform.routers.deps import get_store, get_judge_dep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENT_PAYLOAD = {"name": "auth-test-agent", "runtime": "adk-cloud-run"}


def _client_with_auth_mode(store: MemoryStore, judge: StubJudge, auth_mode: str) -> TestClient:
    """Return a TestClient with the given AGENTOPS_AUTH_MODE set via env var."""
    # Override store/judge dependencies to use the fixture instances.
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_judge_dep] = lambda: judge
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests: auth_mode=none (default) — existing behaviour must not break
# ---------------------------------------------------------------------------


class TestAuthModeNone:
    """All mutating endpoints pass without any Authorization header when auth_mode=none."""

    def test_post_agent_no_auth_header_accepted(
        self, client: TestClient
    ) -> None:
        resp = client.post("/v1/agents", json=_AGENT_PAYLOAD)
        assert resp.status_code == 201

    def test_post_metrics_no_auth_header_accepted(self, client: TestClient) -> None:
        agent = client.post("/v1/agents", json=_AGENT_PAYLOAD).json()
        version = client.post(
            f"/v1/agents/{agent['agentId']}/versions",
            json={
                "image": "gcr.io/project/agent@sha256:abc",
                "model": "gemini-2.0-flash",
                "promptDigest": "sha256:111",
            },
        ).json()
        resp = client.post(
            f"/v1/agents/{agent['agentId']}/metrics",
            json={
                "versionId": version["versionId"],
                "source": "test",
                "samples": [
                    {"name": "x", "value": 1.0, "observedAt": "2026-06-21T00:00:00Z"}
                ],
            },
        )
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# Tests: auth_mode=google-id-token
# ---------------------------------------------------------------------------


class TestAuthModeGoogleIdToken:
    """When auth_mode=google-id-token, mutating endpoints require a valid Bearer token."""

    @pytest.fixture
    def auth_client(self, store: MemoryStore, stub_judge: StubJudge) -> TestClient:  # type: ignore[override]
        """TestClient with google-id-token auth mode enabled."""
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_judge_dep] = lambda: stub_judge
        with patch.dict(os.environ, {"AGENTOPS_AUTH_MODE": "google-id-token"}):
            # Clear the lru_cache so settings are re-read with the new env.
            from config.defaults import get_settings
            get_settings.cache_clear()
            yield TestClient(app)
        # Restore default settings cache.
        from config.defaults import get_settings
        get_settings.cache_clear()
        app.dependency_overrides.clear()

    def test_missing_authorization_header_returns_401(
        self, auth_client: TestClient
    ) -> None:
        resp = auth_client.post("/v1/agents", json=_AGENT_PAYLOAD)
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["code"] == "unauthorized"

    def test_non_bearer_scheme_returns_401(self, auth_client: TestClient) -> None:
        resp = auth_client.post(
            "/v1/agents",
            json=_AGENT_PAYLOAD,
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, auth_client: TestClient) -> None:
        """A token that fails google-auth verification should yield 401."""
        with patch(
            "agentops_platform.routers.auth._verify_google_id_token",
            side_effect=ValueError("Token has expired"),
        ):
            resp = auth_client.post(
                "/v1/agents",
                json=_AGENT_PAYLOAD,
                headers={"Authorization": "Bearer this.is.invalid"},
            )
        assert resp.status_code == 401
        body = resp.json()
        assert "expired" in body["detail"]["message"].lower() or "invalid" in body["detail"]["message"].lower()

    def test_valid_token_is_accepted(self, auth_client: TestClient, store: MemoryStore) -> None:
        """A token that passes _verify_google_id_token should allow the request through."""
        mock_payload = {
            "sub": "12345",
            "email": "sa@project.iam.gserviceaccount.com",
            "aud": "http://localhost:8080",
        }
        with patch(
            "agentops_platform.routers.auth._verify_google_id_token",
            return_value=mock_payload,
        ):
            resp = auth_client.post(
                "/v1/agents",
                json=_AGENT_PAYLOAD,
                headers={"Authorization": "Bearer mock.valid.token"},
            )
        assert resp.status_code == 201

    def test_get_endpoints_do_not_require_auth(self, auth_client: TestClient) -> None:
        """Read-only GET endpoints are not wrapped with AuthDep — no token needed."""
        resp = auth_client.get("/v1/agents")
        assert resp.status_code == 200

    def test_healthz_does_not_require_auth(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/healthz")
        assert resp.status_code == 200
