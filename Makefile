PYTHONPATH := apps/api/src:packages/flybot_core/src:packages/flybot_body_flygym/src:packages/flybot_flycore/src:packages/flybot_safety/src:packages/flybot_perception/src:packages/flybot_brain/src:packages/flybot_executive/src:packages/flybot_telemetry/src

.PHONY: sync sim api ui test lint typecheck check demo demo-d2 demo-d3 demo-d4 demo-d5 test-demos

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

test-demos:
	PYTHONPATH=$(PYTHONPATH) uv run pytest tests/integration/test_runtime_d2_d5.py

lint:
	PYTHONPATH=$(PYTHONPATH) uv run ruff check .

typecheck:
	PYTHONPATH=$(PYTHONPATH) uv run mypy apps packages scripts tests && PYTHONPATH=$(PYTHONPATH) uv run pyright

check: lint typecheck test

demo:
	PYTHONPATH=$(PYTHONPATH) uv run python scripts/run_demo.py --instruction "Walk to the red cube."

demo-d2:
	TEST_GROUND_TRUTH=true PYTHONPATH=$(PYTHONPATH) uv run python scripts/run_demo.py --instruction "Patrol the arena and tell me if anything changes." --timeout-s 25

demo-d3:
	TEST_GROUND_TRUTH=true PYTHONPATH=$(PYTHONPATH) uv run python scripts/run_demo.py --instruction "Find the blue block and touch it once with your right front leg." --timeout-s 25

demo-d4:
	TEST_GROUND_TRUTH=true PYTHONPATH=$(PYTHONPATH) uv run python scripts/run_demo.py --instruction "Follow the moving green target but do not get closer than one body length." --timeout-s 25

demo-d5:
	TEST_GROUND_TRUTH=true PYTHONPATH=$(PYTHONPATH) uv run python scripts/run_demo.py --instruction "Inspect the yellow object, circle it, then return home." --timeout-s 30
