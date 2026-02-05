# Build stage - compile tree-sitter and other native dependencies
FROM python:3.11-slim AS builder

# Configure proxy
ENV HTTP_PROXY=http://proxy.internal.adhie.ae:8080
ENV HTTPS_PROXY=http://proxy.internal.adhie.ae:8080
ENV http_proxy=http://proxy.internal.adhie.ae:8080
ENV https_proxy=http://proxy.internal.adhie.ae:8080
ENV NO_PROXY=localhost,127.0.0.1,host.docker.internal
ENV no_proxy=localhost,127.0.0.1,host.docker.internal

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster package installation
RUN pip install --no-cache-dir uv

# Copy project files needed for build
COPY pyproject.toml README.md ./
COPY dotnet_test_generator ./dotnet_test_generator

# Create virtual environment and install dependencies
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install --no-cache .

# Runtime stage
FROM python:3.11-slim AS runtime

# Configure proxy
ENV HTTP_PROXY=http://proxy.internal.adhie.ae:8080
ENV HTTPS_PROXY=http://proxy.internal.adhie.ae:8080
ENV http_proxy=http://proxy.internal.adhie.ae:8080
ENV https_proxy=http://proxy.internal.adhie.ae:8080
ENV NO_PROXY=localhost,127.0.0.1,host.docker.internal
ENV no_proxy=localhost,127.0.0.1,host.docker.internal

WORKDIR /app

# Install runtime dependencies (git is needed for GitPython)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory '*'

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy application code (overwrite the built package with source for potential debugging)
COPY dotnet_test_generator ./dotnet_test_generator
COPY main.py ./

# Create workdir for repository cloning
RUN mkdir -p /app/workdir
VOLUME ["/app/workdir"]

# Default environment variables (host.docker.internal for accessing host's Ollama)
ENV OLLAMA_BASE_URL=http://host.docker.internal:11434
ENV OLLAMA_MODEL=qwen2.5-coder:32b

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from dotnet_test_generator.agents.ollama_client import OllamaClient; exit(0 if OllamaClient().is_available() else 1)" || exit 0

# Entry point
ENTRYPOINT ["testgen"]
CMD ["--help"]
