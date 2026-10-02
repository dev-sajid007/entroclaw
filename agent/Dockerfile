# Agent API image. Mount the repository to work on at /workspace.
#   docker build -t entroclaw-agent ./agent
#   docker run --rm -p 127.0.0.1:8765:8765 -e OPENAI_API_KEY -v "$PWD:/workspace" entroclaw-agent
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# Run as an unprivileged user; the agent only needs the mounted workspace and its state dir.
RUN useradd --create-home --uid 1000 agent && mkdir -p /workspace /state && chown agent:agent /workspace /state
USER agent
RUN git config --global --add safe.directory /workspace

# The container is the sandbox; bubblewrap would need extra privileges inside it.
ENV SANDBOX=off \
    WORKSPACE=/workspace \
    CHECKPOINT_PATH=/state/checkpoints.sqlite \
    HOST=0.0.0.0 \
    PORT=8765
VOLUME ["/state"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')" || exit 1
ENTRYPOINT ["entroclaw-agent"]
CMD ["serve"]
