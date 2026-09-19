# FlyBot architecture (v0.1 in progress)

Control path:

`Operator -> CognitiveBrain -> MissionExecutive -> IntentCommand -> FlyCore -> SafetyKernel -> BodyAdapter`

Safety path:

- `POST /api/estop` calls `BodyAdapter.safe_stop()` immediately.
- `SafetyKernel` runs deterministic clamping/veto logic on every control tick.
- Expired intents trigger safe stop semantics in `L0FlyCoreController`.

Backend:

- FastAPI app (`apps/api`) runs a control loop and exposes mission/state APIs.
- `MissionExecutive` emits timeline events and per-mission metrics (change counts, follow distance, touch events).
- `POST /api/recording` toggles operator-requested recording state (default remains off).
- Body integration currently defaults to `MockBodyAdapter` with an optional FlyGym 2.x adapter stub.

Front-end:

- Vue 3/Vite dashboard (`apps/operator-ui`) polls `/api/state`, displays camera/pose/status, mission timeline, recording state, and exposes E-STOP/reset.
