# ==============================================================================
# KOBITS PRODUCTION CONTROL PLANE (FastAPI + Multi-Agent Engine)
# ==============================================================================
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Install system dependencies (git for worktrees, curl, gcc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir asyncpg psycopg2-binary redis arq docker

# Copy application source code
COPY . .

# Expose HTTP / WebSocket port
EXPOSE 8000

# Default entrypoint starts the FastAPI production server using Render's dynamic $PORT
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
