# Deployment Guide — agentops-platform

This guide covers deploying agentops-platform to Cloud Run using Cloud Build.
All steps are performed **by a human operator**; no script here executes the deployment automatically.

---

## Prerequisites

### 1. Google Cloud SDK

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
```

### 2. APIs to enable

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudtrace.googleapis.com \
  logging.googleapis.com
```

### 3. Artifact Registry repository

```bash
gcloud artifacts repositories create agentops \
  --repository-format=docker \
  --location=<REGION> \
  --description="agentops-platform container images"
```

### 4. Service account

Create a dedicated service account for the Cloud Run service:

```bash
gcloud iam service-accounts create agentops-platform-sa \
  --display-name="agentops-platform Cloud Run SA"

# Grant required roles
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:agentops-platform-sa@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/cloudtrace.agent"

gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:agentops-platform-sa@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/logging.logWriter"

# Only if you use Secret Manager:
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:agentops-platform-sa@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## One-command deploy (via Cloud Build)

```bash
gcloud builds submit . \
  --config=cloudbuild.yaml \
  --substitutions=\
_PROJECT_ID=<PROJECT_ID>,\
_REGION=<REGION>,\
_REPO=agentops,\
_SERVICE_NAME=agentops-platform,\
_IMAGE_TAG=$(git rev-parse --short HEAD)
```

This builds the image, pushes it to Artifact Registry, and deploys to Cloud Run in one step.

---

## Manual deploy (shell script)

If the image is already in Artifact Registry, deploy only:

```bash
PROJECT_ID=<PROJECT_ID> \
REGION=<REGION> \
SERVICE_NAME=agentops-platform \
IMAGE_TAG=<TAG> \
  ./deploy/deploy.sh
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | yes | — | GCP project id |
| `GOOGLE_CLOUD_REGION` | yes | — | Cloud Run region |
| `AGENTOPS_REPOSITORY_BACKEND` | no | `memory` | `memory` or `sqlite` |
| `AGENTOPS_JUDGE_BACKEND` | no | `stub` | `stub` or `gemini` |
| `AGENTOPS_JUDGE_MODEL` | no | `gemini-2.0-flash` | Gemini model id for judge |
| `AGENTOPS_AUTH_MODE` | no | `none` | `none` (dev) or `google_id_token` (prod) |
| `AGENTOPS_CANARY_DEFAULT_STEPS` | no | `10,25,50,100` | Comma-separated traffic percentages |
| `AGENTOPS_ROLLBACK_WINDOW_MINUTES` | no | `15` | Evaluation window in minutes |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` / `INFO` / `WARNING` |

### Secrets (Secret Manager)

Secret values are **never** embedded in environment variable configs.
Store them in Secret Manager and reference them in `deploy/service.yaml`:

| Secret name (suggested) | Env var mapped to | Description |
|---|---|---|
| `agentops-judge-api-key` | `AGENTOPS_JUDGE_API_KEY` | API key for Gemini judge (if not using ADC) |

---

## Verify the deployment

```bash
# Get the service URL
gcloud run services describe agentops-platform \
  --region=<REGION> \
  --format="value(status.url)"

# Health check (requires a bearer token for authenticated service)
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer ${TOKEN}" https://<SERVICE_URL>/healthz
```

---

## Service configuration reference

See `deploy/service.yaml` for the full Cloud Run service spec (memory, concurrency, env vars, secret mounts).
