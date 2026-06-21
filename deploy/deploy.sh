#!/usr/bin/env bash
# deploy/deploy.sh — Deploy agentops-platform to Cloud Run.
#
# This script is intentionally a thin wrapper around `gcloud run deploy`.
# It does NOT build or push the image; use Cloud Build (cloudbuild.yaml) for that.
#
# Required environment variables:
#   PROJECT_ID      GCP project id
#   REGION          Cloud Run region (e.g. asia-northeast1)
#   SERVICE_NAME    Cloud Run service name (default: agentops-platform)
#   IMAGE_TAG       Container image tag to deploy (default: latest)
#   REPO            Artifact Registry repository name (default: agentops)
#
# Optional / Secret Manager references:
#   SECRET_JUDGE_API_KEY_VERSION  Secret Manager version for the judge API key
#                                 (e.g. projects/<id>/secrets/<name>/versions/latest)
#
# Usage:
#   PROJECT_ID=my-project REGION=asia-northeast1 ./deploy/deploy.sh

set -euo pipefail

# ── Resolve configuration ─────────────────────────────────────────────────────
PROJECT_ID="${PROJECT_ID:?PROJECT_ID is required}"
REGION="${REGION:?REGION is required}"
SERVICE_NAME="${SERVICE_NAME:-agentops-platform}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
REPO="${REPO:-agentops}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE_NAME}:${IMAGE_TAG}"

echo "==> Deploying ${SERVICE_NAME} (${IMAGE_TAG}) to ${REGION} in ${PROJECT_ID}"

# ── Build the gcloud deploy command ──────────────────────────────────────────
DEPLOY_ARGS=(
  run deploy "${SERVICE_NAME}"
  "--project=${PROJECT_ID}"
  "--region=${REGION}"
  "--platform=managed"
  "--image=${IMAGE}"
  "--no-allow-unauthenticated"
  # Resource settings
  "--memory=512Mi"
  "--cpu=1"
  "--concurrency=80"
  "--min-instances=0"
  "--max-instances=10"
  # Non-secret env vars
  "--update-env-vars=AGENTOPS_REPOSITORY_BACKEND=memory"
  "--update-env-vars=AGENTOPS_AUTH_MODE=google_id_token"
  "--update-env-vars=GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
  "--update-env-vars=GOOGLE_CLOUD_REGION=${REGION}"
)

# ── Attach Secret Manager secrets (if specified) ──────────────────────────────
# Secret values are NEVER set here as plain text.  Attach them via Secret Manager.
#
# Example — uncomment and fill in your secret resource path:
#   DEPLOY_ARGS+=(
#     "--update-secrets=AGENTOPS_JUDGE_API_KEY=${SECRET_JUDGE_API_KEY_VERSION}"
#   )
#
# Format: ENV_VAR_NAME=projects/<project>/secrets/<secret-name>/versions/<version>
# The Cloud Run service account must have roles/secretmanager.secretAccessor.
#
# if [[ -n "${SECRET_JUDGE_API_KEY_VERSION:-}" ]]; then
#   DEPLOY_ARGS+=("--update-secrets=AGENTOPS_JUDGE_API_KEY=${SECRET_JUDGE_API_KEY_VERSION}")
# fi

# ── Execute ───────────────────────────────────────────────────────────────────
gcloud "${DEPLOY_ARGS[@]}"

echo "==> Done. Service URL:"
gcloud run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format="value(status.url)"
