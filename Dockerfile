# ==============================================================================
# KOBITS PRODUCTION CONTROL PLANE (FastAPI + Multi-Agent Engine)
# ==============================================================================
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Install core system dependencies (heavy polyglot compilers run in E2B Firecracker MicroVMs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    gcc \
    g++ \
    make \
    ripgrep \
    ca-certificates \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir asyncpg psycopg2-binary redis arq docker e2b

# Copy application source code
COPY . .

# Expose HTTP / WebSocket port
EXPOSE 8000

# Single async worker + 75s keep-alive prevents Google Frontend (GFE) 503 keep-alive race and SQLite worker collisions
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-keep-alive 75"]
