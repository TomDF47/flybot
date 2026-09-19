# flybot

FlyBot is a simulation-first embodied-agent research prototype implementing:

`Operator -> CognitiveBrain -> MissionExecutive -> IntentCommand -> FlyCore -> SafetyKernel -> BodyAdapter`

This is a **private research prototype** and **not safety-certified**.

## Current milestone status

Implemented:

- **M0–M1** bootstrap and runnable repo
- **M2–M4** typed intent/executive/cognitive provider path
- **M5–M8** demo slices for D2–D5 in deterministic mock mode
- **FlyGym 2.x adapter** (`BODY_BACKEND=flygym`) with:
  - custom arena geoms (targets, obstacle, home marker, moving target)
  - body pose, joint readouts, contact extraction, scene/eye camera frame path
  - `reset`, `step`, `high_rate_observation`, `low_rate_frame`, `safe_stop`
- **M9 scaffolding**:
  - benchmark seed sweep harness (`scripts/benchmark.py`)
  - replay summary harness (`scripts/replay.py`)

Still prototype-level:

- Recording/replay/benchmark are lightweight scaffolds, not final research analytics.
- OpenAI planning remains optional and is not required for CI/demo success.

## Repository structure

```text
apps/
  api/
  operator-ui/
packages/
  flybot_core/
  flybot_brain/
  flybot_executive/
  flybot_perception/
  flybot_flycore/
  flybot_body_flygym/
  flybot_safety/
  flybot_telemetry/
configs/
scenarios/
tests/
scripts/
docs/
docker/
```

## Safety and control guarantees currently enforced

- LLM/Cognitive provider does **not** output raw motor/joint commands.
- SafetyKernel is deterministic and has final clamp/veto authority.
- E-stop path directly calls `BodyAdapter.safe_stop()` and does not depend on LLM calls.
- Recording defaults off in configuration.
- No face recognition/covert person ID functionality.

## Setup

```bash
cp .env.example .env
uv sync --all-extras --dev
```

Optional UI install:

```bash
cd apps/operator-ui
npm install
cd ../..
```

## Run commands

```bash
make api    # FastAPI on :8000
make ui     # Vite UI on :5173 (proxying /api to :8000)
make sim    # scripted D1 run
make demo   # one-command D1-D6 suite in mock backend
make demo-d2
make demo-d3
make demo-d4
make demo-d5
make benchmark
make calibrate-contact
make test
make test-demos
make check
```

### One-command full demo flow (D1–D6)

`make demo` runs these instructions in order:

1. `Walk to the red cube.`
2. `Patrol the arena and tell me if anything changes.`
3. `Find the blue block and touch it once with your right front leg.`
4. `Follow the moving green target but do not get closer than one body length.`
5. `Inspect the yellow object, circle it, then return home.`
6. `Stop now.`

## FlyGym 2.x assumptions and fallback

- This code targets FlyGym **2.1+** API shape and avoids legacy `flygym-gymnasium`.
- CI/default remains deterministic mock:
  - `BODY_BACKEND=mock`
- To run FlyGym backend:
  1. install extras: `uv sync --all-extras --dev`
  2. set `BODY_BACKEND=flygym` (or `SIMULATION_BACKEND=flygym`)
  3. run smoke test:
     ```bash
     PYTHONPATH=apps/api/src:packages/flybot_core/src:packages/flybot_body_flygym/src:packages/flybot_flycore/src:packages/flybot_safety/src:packages/flybot_perception/src:packages/flybot_brain/src:packages/flybot_executive/src:packages/flybot_telemetry/src uv run pytest tests/integration/test_flygym_adapter_smoke.py
     ```

If host initialization fails, use Docker fallback:

```bash
docker build -f docker/Dockerfile -t flybot-flygym .
docker run --rm -e BODY_BACKEND=flygym -e SIMULATION_BACKEND=flygym flybot-flygym make test
```

## Environment variables

See `.env.example`.

Key values:

- `BRAIN_PROVIDER=fake|openai`
- `OPENAI_MODEL=<model-name>` (model name is config-driven, never hard-coded in domain code)
- `BODY_BACKEND=mock|flygym` (preferred selector)
- `SIMULATION_BACKEND=mock|flygym`
- `ALLOW_GROUND_TRUTH_FOR_TESTS=true|false`
- `TEST_GROUND_TRUTH=true|false` (scenario/demo-only deterministic perception shortcut)
- `TELEMETRY_OUTPUT_JSONL=<path>` (optional telemetry JSONL sink)

## Contact calibration artifact

Generate calibration summary:

```bash
make calibrate-contact
```

Default output:

- `artifacts/calibration/contact_profile_mock.json`

## Test coverage in this pass

- Unit: safety clamping/geofence/contact-stop, FlyCore TTL handling
- Unit: config backend selection precedence
- Contract: BodyAdapter behavior under deterministic mock simulation
- Integration/scenario:
  - FlyGym adapter smoke when available (skips cleanly when unavailable)
  - D1 seeded mission progression
  - D6 e-stop path
  - D2 patrol/observe change detection
  - D3 touch once with target association
  - D4 follow min-distance policy + TARGET_LOST policy
  - D5 inspect then return-home
