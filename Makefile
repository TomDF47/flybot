PYTHONPATH := apps/api/src:packages/flybot_core/src:packages/flybot_body_flygym/src:packages/flybot_flycore/src:packages/flybot_safety/src:packages/flybot_perception/src:packages/flybot_brain/src:packages/flybot_executive/src:packages/flybot_telemetry/src

.PHONY: sync sim api ui test lint typecheck check demo

sync:
	uv sync --all-extras --dev

sim:
	TEST_GROUND_TRUTH=true PYTHONPATH=$(PYTHONPATH) uv run python scripts/run_demo.py --scripted-d1

api:
	PYTHONPATH=$(PYTHONPATH) uv run uvicorn flybot_api.main:app --reload --host 0.0.0.0 --port 8000

ui:
	cd apps/operator-ui && npm install && npm run dev -- --host 0.0.0.0 --port 5173

test:
	PYTHONPATH=$(PYTHONPATH) uv run pytest

lint:
	PYTHONPATH=$(PYTHONPATH) uv run ruff check .

typecheck:
	PYTHONPATH=$(PYTHONPATH) uv run mypy apps packages scripts tests && PYTHONPATH=$(PYTHONPATH) uv run pyright

check: lint typecheck test

demo:
	PYTHONPATH=$(PYTHONPATH) uv run python scripts/run_demo.py --instruction "Walk to the red cube."
