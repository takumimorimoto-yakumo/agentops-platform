# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN pip install --no-cache-dir --upgrade pip

# Copy dependency manifests first for Docker layer caching
COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/

# Install the package WITH the Gemini extra so google-genai is present for the
# vertex/aistudio judge backends. (A previous broken extras spec installed only
# the base deps, which silently disabled the live LLM and degraded to fallback.)
RUN pip install --no-cache-dir ".[gemini]"

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages and application from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY --from=builder /app/src ./src
COPY --from=builder /app/config ./config

USER appuser

# Cloud Run sets PORT; default to 8080
ENV PORT=8080
EXPOSE ${PORT}

# Start the application
CMD ["sh", "-c", "uvicorn agentops_platform.main:app --host 0.0.0.0 --port ${PORT}"]
