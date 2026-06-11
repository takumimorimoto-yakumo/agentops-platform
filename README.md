# agentops-platform

> Individual hackathon entry by Takumi Morimoto — 個人事業（屋号：八雲）として開発・公開

**DevOps for AI Agents** — an AgentOps platform that runs evaluation, regression testing, canary release, and automatic rollback for AI agents (Google ADK) as a CI/CD loop on Google Cloud.

Built for the DevOps × AI Agent Hackathon 2026.

## Why

AI agents change behavior when you touch a prompt, a rule, or a model version — but most teams ship those changes with no regression gate, no canary, and no rollback. This platform treats the agent itself as the deployment artifact and puts its *behavior* under CI/CD.

## What it does

| Capability | How |
|---|---|
| Evaluation dataset management | ADK Eval based |
| Regression testing | Behavior diff on prompt / rule changes (Gemini-as-judge score delta) |
| Behavior evaluation | ADK Eval trajectory evaluation (tool-call sequence checks) |
| Cost / latency tracking | Cloud Trace / Cloud Logging |
| Canary release | Traffic-ratio control on Cloud Run |
| Auto rollback | Triggered on evaluation score degradation |
| Interfaces | CLI + read-only Web dashboard (score timeline / canary status / rollback history) |

Target agents: built with **Google ADK** (the evaluation/deployment API is a framework-agnostic HTTP contract, so other frameworks can be added later).

## Architecture

```
[ agentops-platform ]
  ├ control plane (ADK on Cloud Run)
  │  ├ evaluator        (ADK Eval + Gemini judge)
  │  ├ deployment ctrl  (canary + auto-rollback)
  │  ├ trace collector  (Cloud Trace)
  │  └ regression test  (prompt/rule diff)
  ├ CLI + Web Dashboard
  └ Cloud Build pipelines
```

Stack: ADK / Gemini API / Cloud Run / Cloud Build / BigQuery / Cloud Trace / Cloud Logging.

## API

The control-plane contract is published as [`api/openapi.yaml`](./api/openapi.yaml) (OpenAPI 3.1). Long-running operations are asynchronous (`202` + polling). Design records live in [`docs/adr/`](./docs/adr/).

## Status

Work in progress (hackathon period: June–July 2026). Architecture and design records (ADR) are published as they are written.

## License

Apache-2.0 — Copyright (c) 2026 Takumi Morimoto. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
