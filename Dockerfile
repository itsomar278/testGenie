# Build stage - compile tree-sitter and other native dependencies
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ git && \
    rm -rf /var/lib/apt/lists/*

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

WORKDIR /app

# Install runtime dependencies
# - git: needed for GitPython
# - wget, curl, ca-certificates: needed for downloading .NET
# - libicu, libssl, zlib: .NET runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        wget \
        curl \
        ca-certificates \
        libicu-dev \
        libssl-dev \
        zlib1g \
        && rm -rf /var/lib/apt/lists/* && \
    git config --global --add safe.directory '*'

# Install .NET 9 SDK
# Using the official Microsoft install script for maximum compatibility
RUN curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin --channel 9.0 --install-dir /usr/share/dotnet && \
    ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet

# Install Azure Artifacts Credential Provider for NuGet feed authentication
# This enables VSS_NUGET_EXTERNAL_FEED_ENDPOINTS-based auth with Azure DevOps feeds
RUN curl -sSL https://aka.ms/install-artifacts-credprovider.sh | bash

# Set .NET environment variables
ENV DOTNET_ROOT=/usr/share/dotnet
ENV PATH="${PATH}:/usr/share/dotnet"
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV DOTNET_NOLOGO=1
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=false

# Bypass proxy for internal Azure DevOps servers
ENV NO_PROXY="devops.malaffi.ae,localhost,127.0.0.1"
ENV no_proxy="devops.malaffi.ae,localhost,127.0.0.1"

# Internal NuGet feed for shared packages (added alongside nuget.org)
ENV NUGET_INTERNAL_FEED="https://devops.malaffi.ae/ADHDS/bc771773-61a4-46e7-94fa-b2cce0ef7fc3/_packaging/Nugetpackage-Dev/nuget/v3/index.json"

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
