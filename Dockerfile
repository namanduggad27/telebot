FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on

WORKDIR /app

# Install system build dependencies required for Hydrogram / tgcrypto C extensions and Pillow image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy project dependencies and install
COPY pyproject.toml /app/
RUN pip install uv && uv venv .venv && uv pip install -e .

COPY . /app/

# Create non-root user for security
RUN useradd -m pipeline && chown -R pipeline:pipeline /app
USER pipeline

ENV PATH="/app/.venv/bin:$PATH"
