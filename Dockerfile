FROM python:3.12-slim

# Install uv binary directly from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install system build dependencies (C extensions for gRPC, PyYAML, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        build-essential \
        ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition files first for Docker layer caching
# COPY pyproject.toml uv.lock ./

# Fast, cached dependency installation directly into system Python
# RUN uv sync --frozen --no-dev --no-install-project

COPY pyproject.toml ./
RUN uv pip install --no-cache torch --index-url https://download.pytorch.org/whl/cpu

RUN uv pip install --no-cache .

# Copy application source code
COPY . .

ENTRYPOINT ["python", "run.py"]