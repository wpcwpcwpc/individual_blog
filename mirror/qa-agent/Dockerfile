# ─────────────────────────────────────────────
#  QA Agent System — Dockerfile
#  Multi-stage build: slim final image
# ─────────────────────────────────────────────

# --- Stage 1: dependency builder ---
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for building some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt


# --- Stage 2: runtime image ---
FROM python:3.11-slim AS runtime

LABEL description="QA Agent System — AI-powered test automation"

# Non-root user for security
RUN groupadd -r qaagent && useradd -r -g qaagent qaagent

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY . .

# Create data directories
RUN mkdir -p /app/data /app/skills /app/agents /app/logs \
    && chown -R qaagent:qaagent /app

USER qaagent

# Environment defaults (overridden by docker-compose / k8s)
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    LOG_LEVEL=info

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["python", "main.py"]
