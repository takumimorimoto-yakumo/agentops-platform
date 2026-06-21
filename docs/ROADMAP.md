# AgentOps Platform — Build Roadmap

This document is the implementation SSOT: what to build, in what order, and the
acceptance criteria for each step. It is written so a fresh session can pick up
any milestone and implement it with confidence. Keep it technical and public-safe.

## Goal (one sentence)

Put an AI agent's *behavior* under CI/CD: evaluate every version, roll it out by
canary, and let an **autonomous meta-agent** decide promote / hold / rollback and
file an improvement PR when behavior degrades.

## The meta-agent (core)

The differentiator. A Google ADK agent that operates the deployment, rather than a
fixed control loop.

- **Inputs**: evaluation runs (drift / trajectory / cost / latency), Cloud Trace
  spans, current deployment state and rollback policy.
- **Decisions**: `advance` (next canary step) / `hold` (wait another window) /
  `rollback` / `open_pr` (improvement proposal with a diagnosis).
- **Two-layer safety**: deterministic thresholds are the **hard floor** — a
  catastrophic breach always rolls back without asking the agent. The meta-agent
  exercises **judgment in the gray zone** (ambiguous deltas, slow drift, trade-offs
  between axes). The agent never has authority to *suppress* a safety-floor rollback.
- **Outputs**: a decision record (inputs seen, rationale, action) for the audit log;
  on degradation, a diagnosis plus a PR against the agent's prompt/config repo.

## Killer demo (must stay reproducible)

Deploy a deliberately broken prompt version → canary starts → meta-agent reads the
evaluation regression (drift/trajectory) → **autonomous rollback** + a diagnosis PR.
Runnable on demand (no waiting for organic traffic). This is the primary thing the
whole platform must be able to show end to end.

## Scope (MVP)

| In | Out |
|---|---|
| Autonomous meta-agent (advance/hold/rollback/open_pr) | Agent having authority over the safety floor |
| Evaluation: drift (Gemini judge), trajectory (ADK Eval), cost/latency (Trace) | General MLOps (model training, DWH) |
| Regression check (baseline vs candidate) to gate CI | Support for non-ADK runtimes (contract stays framework-agnostic, but only ADK is implemented) |
| Canary on Cloud Run + deterministic rollback policy | Multi-cloud |
| CLI + read-only web dashboard (3 views) | Full operational GUI |
| Audit log of decisions, deployments, rollbacks | Replacing an existing APM |

## Architecture / components

```
control plane (ADK on Cloud Run)
  ├ api              (REST, api/openapi.yaml)
  ├ evaluator        (ADK Eval + Gemini judge; cost/latency from Trace)
  ├ regression       (baseline vs candidate, per-axis deltas; CI gate)
  ├ deployment ctrl  (Cloud Run traffic split; deterministic rollback policy = safety floor)
  ├ meta-agent       (reads eval+trace → advance/hold/rollback/open_pr)
  └ trace collector  (Cloud Trace / Logging ingestion)
CLI + read-only dashboard (score timeline / canary status / rollback history)
Cloud Build pipelines (regression gate + deploy)
```

## Milestones (ordered; each has an acceptance test)

- **M1 — API skeleton + data model.** Serve `api/openapi.yaml` on Cloud Run;
  persist agents / versions / eval-suites / evaluations. *Accept:* register an
  agent + version, list them, fetch by id.
- **M2 — Evaluator.** Run an ADK Eval suite for a version; compute drift
  (Gemini-as-judge delta vs baseline), trajectory pass, cost/latency from Trace.
  *Accept:* `POST /evaluations` → async run → `GET` returns per-axis scores.
- **M3 — Deployment controller + safety floor.** Canary via Cloud Run traffic
  split; deterministic `RollbackPolicy` evaluated each window; emit events.
  *Accept:* canary advances steps; a forced threshold breach triggers a rollback
  and a `deployment.rolled_back` event.
- **M4 — meta-agent (the core).** ADK agent reads eval + trace + state and decides
  advance/hold/rollback; on degradation opens a diagnosis PR. *Accept:* the killer
  demo runs end to end — broken version → autonomous rollback + PR.
- **M5 — Regression gate in CI.** Wire `regressions` into a Cloud Build step that
  blocks promotion on a failing verdict. *Accept:* a regressing candidate fails the
  build; a clean one passes.
- **M6 — CLI + read-only dashboard.** Three views (score timeline / canary status /
  rollback history). *Accept:* each view renders live data from the API.
- **M7 — Demo hardening + docs.** Scripted, repeatable killer demo; README/ADR
  updated.

## Tech stack

ADK + Gemini API / Cloud Run / Cloud Build / BigQuery / Cloud Trace / Cloud Logging.
No dependency on external LLMs.

## Contract boundaries (other services)

- **CI (Cloud Build)** calls `regressions` and `deployments` to gate merges/rollouts.
- **Managed agents** push outcome metrics via `POST /agents/{id}/metrics` (the bundled
  demo application — `marketing-shorts-agent` — is one such managed agent).
- API contract is `api/openapi.yaml` (OpenAPI 3.1); long-running ops are `202` + poll.

## Open contract items (update `api/openapi.yaml` when stabilized)

- **meta-agent decision resource**: the decision record (inputs, rationale, action,
  link to the opened PR) is not yet in the contract. Add it during M4 once the
  schema settles. Until then, auto-rollback is exposed through the existing
  `:rollback` endpoint and `deployment.rolled_back` event (shared audit trail).

## Config SSOT (no hard-coding)

- Eval thresholds, canary ratios, rollback-policy defaults → `config/defaults.*`
- Env URLs / project ids → `.env.example` + config layer
- Gemini model ids → config layer (never inline in code)
