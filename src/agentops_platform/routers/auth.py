"""
Google ID token verification dependency for mutating endpoints.

This module provides a FastAPI dependency that validates Google-signed OIDC
tokens when ``AGENTOPS_AUTH_MODE=google-id-token``.  When the mode is
``none`` (the default), the dependency is a no-op so that existing tests and
local development continue to work without credentials.

Usage
-----
Add ``AuthDep`` to any mutating endpoint (POST / PATCH / DELETE):

    @router.post("/agents/{agentId}/deployments", ...)
    def create_deployment(agentId: str, body: ..., store: StoreDep, _: AuthDep) -> ...:
        ...

Token requirements
------------------
- Signed by Google (RS256 / ES256 via Google public-key endpoint).
- ``aud`` claim must equal the service's expected audience
  (``AGENTOPS_AUTH_AUDIENCE`` env var, defaults to the control-plane URL).
- Token must not be expired.

The ``AGENTOPS_AUTH_AUDIENCE`` variable should be set to the Cloud Run
service URL when running in production.
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from config.defaults import get_settings


def _verify_google_id_token(token: str, audience: str) -> dict:
    """Verify a Google-signed OIDC token and return its payload.

    Uses ``google.oauth2.id_token.verify_oauth2_token`` which validates:
    - RSA/EC signature against Google's public keys.
    - ``aud`` claim equality.
    - ``exp`` / ``iat`` clock drift.

    Raises ``ValueError`` if the token is invalid.
    """
    # Import lazily so the dependency is only required when auth is enabled.
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    request_adapter = google_requests.Request()
    return google_id_token.verify_oauth2_token(token, request_adapter, audience)


def require_google_id_token(request: Request) -> None:
    """FastAPI dependency: validate a Google ID token for mutating endpoints.

    Behaviour depends on ``AGENTOPS_AUTH_MODE``:

    - ``none`` (default): no-op; request passes through without checking any
      Authorization header.  Existing tests run in this mode.
    - ``google-id-token``: the ``Authorization: Bearer <token>`` header is
      required.  The token is verified against Google's public-key endpoint.
      Invalid or missing tokens result in HTTP 401.
    """
    settings = get_settings()

    if settings.auth_mode != "google-id-token":
        # Auth disabled — no-op for local dev and existing tests.
        return

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "unauthorized",
                "message": "Authorization: Bearer <id-token> header is required",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[len("Bearer "):]
    # Audience defaults to the control-plane URL, but can be overridden via env.
    audience = os.environ.get("AGENTOPS_AUTH_AUDIENCE", settings.control_plane_url)

    try:
        _verify_google_id_token(token, audience)
    except Exception as exc:  # google-auth raises ValueError / google.auth.exceptions.*
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "unauthorized",
                "message": f"Invalid or expired ID token: {exc}",
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# Annotated type alias for clean router signatures.
AuthDep = Annotated[None, Depends(require_google_id_token)]
