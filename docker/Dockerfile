FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY README.md ./
RUN pip install --no-cache-dir uv && uv sync --no-dev

COPY apps ./apps
COPY packages ./packages
COPY configs ./configs
COPY scripts ./scripts

ENV PYTHONPATH="apps/api/src:packages/flybot_core/src:packages/flybot_body_flygym/src:packages/flybot_flycore/src:packages/flybot_safety/src:packages/flybot_perception/src:packages/flybot_brain/src:packages/flybot_executive/src:packages/flybot_telemetry/src"
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "flybot_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
