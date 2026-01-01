FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy project files (README needed for hatchling build)
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Create data directory
RUN mkdir -p /data

ENV DOCS_DIR=/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Run the API server
CMD ["uv", "run", "python", "-m", "src.main", "serve", "--host", "0.0.0.0", "--port", "8000"]

