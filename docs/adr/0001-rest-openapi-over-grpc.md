# ADR-0001: REST + OpenAPI over gRPC for the control-plane API

- Status: Accepted
- Date: 2026-06-11

## Context

The platform exposes a control-plane API (evaluation, regression, canary
deployment, rollback, metric ingestion) consumed by:

1. CI pipelines (Cloud Build steps gating merges and deployments)
2. Managed agents pushing outcome metrics (feedback loop)
3. The CLI and the read-only Web dashboard

We had to choose between gRPC and REST/JSON with an OpenAPI contract.

## Decision

REST/JSON with a published OpenAPI 3.1 contract ([api/openapi.yaml](../../api/openapi.yaml)).

## Rationale

- **Cloud Run friendliness**: plain HTTP works with Cloud Run
  service-to-service auth (Google-signed ID tokens) and `curl`-level
  debuggability, which matters in CI steps.
- **Framework-agnostic hook**: any agent runtime can push metrics or be
  gated by regression checks with nothing but an HTTP client. This keeps
  the door open for non-ADK runtimes without shipping client stubs.
- **Contract as documentation**: the OpenAPI file doubles as the public
  interface spec of the project; consumers can generate clients in any
  language.
- **Velocity**: no proto toolchain, no codegen step in a short development
  window.

## Trade-offs

- No streaming. Evaluation runs are asynchronous (202 + polling); if push
  notification becomes necessary we will add a webhook, not streaming.
- JSON overhead is irrelevant at control-plane traffic volumes.

## Consequences

- All long-running operations follow the same pattern: `POST` returns
  `202` with a resource id, `GET` polls state.
- Auto-rollback is platform-initiated but uses the same `:rollback`
  endpoint and emits the same `deployment.rolled_back` event, so manual
  and automatic operations share one audit trail.
