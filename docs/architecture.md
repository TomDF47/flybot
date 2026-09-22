# FlyBot architecture (v0.1 in progress)

Control path:

`Operator -> CognitiveBrain -> MissionExecutive -> IntentCommand -> FlyCore -> SafetyKernel -> BodyAdapter`

Safety path:

- `POST /api/estop` calls `BodyAdapter.safe_stop()` immediately.
- `SafetyKernel` runs deterministic clamping/veto logic on every control tick.
- Expired intents trigger safe stop semantics in `L0FlyCoreController`.

Backend:

- FastAPI app (`apps/api`) runs a control loop and exposes mission/state APIs.
- When `JEV_ENABLED` is on (the default), a separate decision loop asks JEV for
  one bounded `PlanAction` at a time until the mission is done. The planner sets
  the objective. JEV never emits joint targets, torques, or raw motor commands.
  The status text while that loop is active is `Running in a loop until you are done`.
  E-stop latches `SafetyKernel` and calls `BodyAdapter.safe_stop()` without waiting
  for a model response.
- `MissionExecutive` emits timeline events and per-mission metrics (change counts, follow distance, touch events).
- `POST /api/recording` toggles operator-requested recording state (default remains off).
- Body integration supports:
  - `MockBodyAdapter` (default CI/backend)
  - `FlyGymBodyAdapter` (FlyGym 2.x musculoskeletal simulation + custom arena geoms + camera frames)

Front-end:

- Vue 3/Vite dashboard (`apps/operator-ui`) polls `/api/state`, displays camera/pose/status, mission timeline, recording state, and exposes E-STOP/reset.
