# ==============================================================================
# KOBITS PRODUCTION CONTROL PLANE (FastAPI + Multi-Agent Engine)
# ==============================================================================
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Install system dependencies (git for worktrees, curl, gcc, ripgrep, compilers, java)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    gcc \
    g++ \
    make \
    ripgrep \
    ca-certificates \
    libpq-dev \
    openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 LTS + npm + pnpm (for React, Next.js, TypeScript repos)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pnpm \
    && rm -rf /var/lib/apt/lists/*

# Install Go 1.22+ (for Golang microservices and `go test`)
RUN curl -fsSL https://go.dev/dl/go1.22.8.linux-amd64.tar.gz | tar -C /usr/local -xz

# Install Rust stable minimal toolchain (for Rust crates, `rustc`, and `cargo test`)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal \
    && rm -rf /root/.rustup/toolchains/stable-*/share/doc

ENV PATH="/usr/local/go/bin:/root/.cargo/bin:${PATH}"


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
