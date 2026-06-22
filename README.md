# agentops-platform

> Individual hackathon entry by Takumi Morimoto — 個人事業（屋号：八雲）として開発・公開

**DevOps for AI Agents** — an AgentOps platform that runs evaluation, regression testing, canary release, and automatic rollback for AI agents (Google ADK) as a CI/CD loop on Google Cloud.

Built for the DevOps × AI Agent Hackathon 2026.

## Why

AI agents change behavior when you touch a prompt, a rule, or a model version — but most teams ship those changes with no regression gate, no canary, and no rollback. This platform treats the agent itself as the deployment artifact and puts its *behavior* under CI/CD.

The core differentiator is an **autonomous meta-agent** (built with Google ADK + Gemini) that reads evaluation results and decides — without human input — whether to advance the canary, hold and wait, or roll back and file an improvement PR.

## Relationship to managed agents

This platform **operates** agents; it does not *use* them. A *managed agent* does its own real-world task (e.g. producing content); this platform keeps that agent's behavior correct by putting each version under evaluation, canary, and automatic rollback. The platform never consumes the agent's output — it governs the agent.

```
   agentops-platform   (control plane + autonomous meta-agent)
        │  ▲
   (1)  │  │  (2)
 operate │  │ report
  / eval ▼  │
   a managed agent     (does the real task; e.g. marketing-shorts-agent)
        │
        ▼  the agent's real output goes to its own end users — the platform never sees it
```

- **(1) platform → agent**: evaluate each version, roll out by canary, auto-rollback on regression.
- **(2) agent → platform**: register its versions and push outcome metrics (e.g. audience retention) back.

[marketing-shorts-agent](https://github.com/takumimorimoto-yakumo/marketing-shorts-agent) is the reference managed agent used to demonstrate this loop end to end.

## What it does (implemented)

| Capability | Status | How |
|---|---|---|
| Evaluation dataset management | Implemented | ADK Eval based; `POST /agents/{id}/eval-suites` |
| Behavior evaluation (drift) | Implemented | Gemini-as-judge score delta vs baseline |
| Behavior evaluation (trajectory) | Implemented | ADK Eval trajectory / tool-call sequence checks |
| Cost / latency tracking | Implemented | Per-axis scores ingested as `cost` / `latency` axes |
| Regression check | Implemented | Baseline vs candidate per-axis delta; CI gate |
| Canary release state machine | Implemented | `pending → canary → promoted / rolled_back` |
| Deterministic rollback policy (safety floor) | Implemented | Threshold breach triggers immediate rollback |
| Autonomous meta-agent (gray-zone judgment) | Implemented | Gemini or deterministic fallback; advance/hold/rollback |
| Improvement PR draft on rollback | Implemented | dryrun (local) or `gh pr create`; diagnosis + suggestions |
| Outcome metrics ingest | Implemented | `POST /agents/{id}/metrics`; fed into meta-agent signal |
| Audit event log | Implemented | Every state change emits a typed Event |
| Read-only web dashboard | Implemented | `/dashboard` — score timeline / canary status / rollback history |
| CLI + autonomy demo script | Implemented | `python -m agentops_platform.examples.autonomy_demo` |

**Private-pluggable** (not in this repo): real Cloud Run traffic split integration, BigQuery / Cloud Trace adapters. The HTTP contract (`api/openapi.yaml`) is fully defined so custom adapters can be added.

## Architecture

```
[ agentops-platform ]
  ├ control plane (FastAPI on Cloud Run)
  │  ├ evaluator        (judge interface: StubJudge / GeminiJudge)
  │  ├ regression       (baseline vs candidate delta; verdict=pass|fail)
  │  ├ deployment ctrl  (canary state machine + deterministic safety floor)
  │  ├ meta-agent       (Layer 2 gray-zone: Gemini or deterministic fallback)
  │  └ audit log        (Event store)
  ├ read-only dashboard (/dashboard — HTML + /dashboard/data JSON)
  └ autonomy demo       (src/agentops_platform/examples/autonomy_demo.py)
```

Stack: FastAPI / Pydantic / Google ADK + Gemini API / Cloud Run / Cloud Build.

## Quickstart

### 1. Install

```bash
git clone https://github.com/takumimorimoto-yakumo/agentops-platform.git
cd agentops-platform
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Run tests

```bash
pytest
# Expected: 100 passed
```

### 3. Run the autonomy demo (no external services required)

The demo shows the full loop: register agent → canary deploy → evaluation → meta-agent detects degradation → **autonomous rollback** + PR draft — with zero human action.

```bash
# Run from the repository root (package is installed via pip install -e)
python -m agentops_platform.examples.autonomy_demo
```

Expected output ends with:

```
  Canary deployment state : rolled_back
  Meta-agent action       : ROLLBACK
  Rollback happened       : True
  PR draft filed          : yes (dryrun)
```

### 4. Start the API server + live dashboard

```bash
uvicorn agentops_platform.main:app --reload --port 8080
# Open: http://localhost:8080/dashboard
# API docs: http://localhost:8080/docs
```

To run the demo with the server live (dashboard updates in real time):

```bash
uvicorn agentops_platform.main:app --port 8080 &
python -m agentops_platform.examples.autonomy_demo --with-server
# Open: http://localhost:8080/dashboard
```

### 5. Configuration

Copy `.env.example` to `.env` and edit as needed:

```bash
cp .env.example .env
# Set AGENTOPS_JUDGE_BACKEND=gemini and GOOGLE_CLOUD_PROJECT to use real Gemini judge
```

Key environment variables:

| Variable | Default | Description |
|---|---|---|
| `AGENTOPS_JUDGE_BACKEND` | `stub` | `stub` (no LLM) or `gemini` (real judge) |
| `AGENTOPS_JUDGE_MODEL` | `gemini-2.5-flash` | Gemini model for drift scoring |
| `AGENTOPS_PR_MODE` | `dryrun` | `dryrun` (local PR body) or `gh` (real GitHub PR) |
| `AGENTOPS_AUTH_MODE` | `none` | `none` (dev) or `google-id-token` (production) |
| `AGENTOPS_META_AGENT_DRIFT_WARN` | `0.05` | Soft drift-drop threshold triggering gray-zone judgment |

## API

The control-plane contract is published as [`api/openapi.yaml`](./api/openapi.yaml) (OpenAPI 3.1).

Key endpoints:

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/agents` | Register an agent |
| `POST` | `/v1/agents/{id}/versions` | Register a deployable version |
| `POST` | `/v1/agents/{id}/evaluations` | Start evaluation run (202 async) |
| `POST` | `/v1/agents/{id}/regressions` | CI regression gate (baseline vs candidate) |
| `POST` | `/v1/agents/{id}/deployments` | Deploy with canary strategy |
| `POST` | `/v1/deployments/{id}:rollback` | Manual or auto-triggered rollback |
| `POST` | `/v1/deployments/{id}:promote` | Promote canary to 100% |
| `GET`  | `/v1/agents/{id}/events` | Audit log |
| `POST` | `/v1/agents/{id}/metrics` | Ingest outcome metrics |
| `GET`  | `/dashboard` | Read-only monitoring dashboard |

Long-running operations return `202` with a resource id for polling. Design records live in [`docs/adr/`](./docs/adr/).

## Two-layer safety design

```
Layer 1 — Safety floor (deterministic)
  Catastrophic threshold breach → immediate rollback, no LLM consulted.
  Thresholds: maxDriftDrop, maxTrajectoryDrop, maxCostIncreaseRatio, maxLatencyP95Ms

Layer 2 — Gray-zone judgment (meta-agent)
  Soft warning thresholds exceeded but hard floor not breached →
  Gemini (or deterministic fallback) reasons about advance / hold / rollback.
  On rollback: files a PR draft with diagnosis and improvement suggestions.
```

The meta-agent never has authority to suppress a safety-floor rollback.

## Deploy to Cloud Run

See [`deploy/DEPLOY.md`](./deploy/DEPLOY.md) for the full guide.

Quick path via Cloud Build:

```bash
gcloud builds submit . \
  --config=cloudbuild.yaml \
  --substitutions=_PROJECT_ID=<ID>,_REGION=<REGION>,_REPO=agentops,_SERVICE_NAME=agentops-platform,_IMAGE_TAG=$(git rev-parse --short HEAD)
```

## Status

Work in progress (hackathon period: June–July 2026). The autonomy demo (M4 killer demo) is runnable on demand with no external dependencies. Cloud Run integration (M3 traffic split) and full Cloud Trace wiring are in progress.

## License

Apache-2.0 — Copyright (c) 2026 Takumi Morimoto. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
