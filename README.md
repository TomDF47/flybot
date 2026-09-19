# flybot

FlyBot is a simulation-first embodied-agent research prototype implementing:

`Operator -> CognitiveBrain -> MissionExecutive -> IntentCommand -> FlyCore -> SafetyKernel -> BodyAdapter`

This is a **private research prototype** and **not safety-certified**.

## Current milestone status

### Completed in this repository pass

- **M0 bootstrap**
  - Python 3.12+ `uv` project with pinned dependencies and `uv.lock`
  - `Makefile` targets: `sim`, `api`, `ui`, `test`, `check`, `demo`
  - typed schemas (Pydantic v2), lint/type/test setup (`ruff`, `mypy`, `pyright`, `pytest`)
  - required architecture guardrails documented in root `AGENTS.md`
- **M1 runnable body/API/UI slice**
  - `BodyAdapter` protocol and deterministic `MockBodyAdapter`
  - optional FlyGym adapter entry point (runtime stub with explicit install assumptions)
  - arena-style world with colored targets, obstacle, home marker, moving target
  - body pose, joint state, contact telemetry, camera frame stream (PNG data URL)
  - FastAPI endpoints:
    - `GET /health`
    - `GET /api/state`
    - `POST /api/estop`
    - `POST /api/reset`
    - `POST /api/missions`
    - `GET /api/missions/{id}`
    - `WS /ws/telemetry`
  - Vue 3 + Vite operator UI with mission box, camera frame, telemetry, reset, and e-stop
- **M2–M4 progress toward D1 + D6**
  - L0 FlyCore supports STOP/NAVIGATE/TURN/FORWARD/RETREAT semantics via typed intents
  - MissionExecutive dispatches typed intents with IDs, TTL, and timeout semantics
  - deterministic color/shape target resolver
  - CognitiveProvider interface with:
    - deterministic FakeBrain (default, no API key required)
    - optional OpenAI provider (`BRAIN_PROVIDER=openai`) behind interface
- **M5–M8 prototype behaviors toward D2/D3/D4/D5**
  - PATROL + OBSERVE mission steps with deterministic change detection counters
  - mission timeline events and recording state surfaced through API/UI (`recording` remains off by default)
  - FOLLOW action with min/max distance constraints and `TARGET_LOST` failure policy
  - TOUCH/NUDGE step runner with approach/contact/retract sequencing and force-limit failure path
  - INSPECT multi-viewpoint arc and RETURN_HOME mission chaining

### Not complete yet

- Full FlyGym 2.x runtime integration is still environment-dependent and currently stubbed.
- OpenAI-driven planning quality for D2–D5 is not validated yet; CI defaults to FakeBrain + deterministic mock simulation.
- Touch calibration and benchmark/replay are prototype-level (mock calibration hook implemented, real FlyGym force calibration pending).

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
make sim    # scripted D1-like walk-to-red-cube demo (enables TEST_GROUND_TRUTH=true)
make demo   # mission demo (instruction configurable in script args)
make demo-d2
make demo-d3
make demo-d4
make demo-d5
make test
make test-demos
make check
```

## FlyGym 2.x assumptions and fallback

- This code targets FlyGym **2.1+** API shape and avoids legacy `flygym-gymnasium`.
- In environments where FlyGym/MuJoCo cannot be installed, the default backend is `mock`.
- To attempt FlyGym mode:
  1. install extras: `uv sync --all-extras --dev`
  2. set `SIMULATION_BACKEND=flygym`
  3. validate local MuJoCo runtime dependencies
- If FlyGym is unavailable, development and tests still run deterministically via `MockBodyAdapter`.

## Environment variables

See `.env.example`.

Key values:

- `BRAIN_PROVIDER=fake|openai`
- `OPENAI_MODEL=<model-name>` (model name is config-driven, never hard-coded in domain code)
- `SIMULATION_BACKEND=mock|flygym`
- `ALLOW_GROUND_TRUTH_FOR_TESTS=true|false`
- `TEST_GROUND_TRUTH=true|false` (scenario/demo-only deterministic perception shortcut)

## Test coverage in this pass

- Unit: safety clamping/geofence/contact-stop, FlyCore TTL handling
- Contract: BodyAdapter behavior under deterministic mock simulation
- Integration/scenario:
  - D1 seeded mission progression
  - D6 e-stop path
  - D2 patrol/observe change detection
  - D3 touch once with target association
  - D4 follow min-distance policy + TARGET_LOST policy
  - D5 inspect then return-home

## Next milestone focus

- Implement real FlyGym 2.x `BodyAdapter` execution path (currently explicit TODO/stub)
- Replace deterministic parser heuristics with robust structured OpenAI plan generation for D2–D5
- Expand benchmark/replay tooling for full M9 acceptance metrics
