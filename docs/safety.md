# Safety notes (prototype)

This repository is a **private research prototype** and is **not safety-certified**.

Current deterministic safety controls:

- E-stop endpoint directly calls `BodyAdapter.safe_stop()`
- Latched safety stop in `SafetyKernel`
- Speed and turn clamping
- Geofence stop
- Contact force stop
- Intent TTL expiry stop

JEV, when enabled, selects only typed intents (`NAVIGATE`, `STOP`, and the other
`PlanAction` values). It does not receive camera bytes and it does not emit motor
commands. `SafetyKernel` still clamps or vetoes every control tick. E-stop does
not wait for a JEV response. API keys are kept in process memory and are not
written to telemetry. Recording stays off unless an operator or scenario turns it on.

Privacy defaults:

- Recording is off by default
- No face recognition / covert identity tracking is implemented
