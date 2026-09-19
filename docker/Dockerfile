FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libgl1 \
      libosmesa6 \
      libglfw3 \
      libglew2.2 \
      ffmpeg \
      libxrender1 \
      libxext6 \
      libsm6 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY uv.lock ./
COPY README.md ./
RUN pip install --no-cache-dir uv && uv sync --all-extras --dev

COPY apps ./apps
COPY packages ./packages
COPY configs ./configs
COPY scenarios ./scenarios
COPY scripts ./scripts
COPY tests ./tests
COPY src ./src

ENV PYTHONPATH="apps/api/src:packages/flybot_core/src:packages/flybot_body_flygym/src:packages/flybot_flycore/src:packages/flybot_safety/src:packages/flybot_perception/src:packages/flybot_brain/src:packages/flybot_executive/src:packages/flybot_telemetry/src"
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "flybot_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
