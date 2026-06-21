"""
AgentOps Platform — FastAPI application entry point.

Serves the REST API defined in api/openapi.yaml under the /v1 prefix.
All long-running operations return 202 + resource id for polling.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from config.defaults import get_settings
from .routers import agents, deployments, evaluations, events, metrics, regressions

settings = get_settings()

app = FastAPI(
    title="AgentOps Platform API",
    version="0.1.0",
    description=(
        "Control-plane API for operating AI agents with DevOps discipline: "
        "evaluation, regression testing, canary release and automatic rollback."
    ),
    license_info={"name": "Apache-2.0", "identifier": "Apache-2.0"},
)

# ── Routers ───────────────────────────────────────────────────────────────────
# All routes are mounted under /v1 to match the openapi.yaml server URL pattern.

PREFIX = "/v1"

app.include_router(agents.router, prefix=PREFIX)
app.include_router(evaluations.router, prefix=PREFIX)
app.include_router(regressions.router, prefix=PREFIX)
app.include_router(deployments.router, prefix=PREFIX)
app.include_router(metrics.router, prefix=PREFIX)
app.include_router(events.router, prefix=PREFIX)


# ── Health check ──────────────────────────────────────────────────────────────


@app.get("/healthz", include_in_schema=False)
def health_check() -> JSONResponse:
    """Kubernetes / Cloud Run liveness probe endpoint."""
    return JSONResponse({"status": "ok"})


# ── Local dev entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agentops_platform.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
