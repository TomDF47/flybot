# Safety notes (prototype)

This repository is a **private research prototype** and is **not safety-certified**.

Current deterministic safety controls:

- E-stop endpoint directly calls `BodyAdapter.safe_stop()`
- Latched safety stop in `SafetyKernel`
- Speed and turn clamping
- Geofence stop
- Contact force stop
- Intent TTL expiry stop

Privacy defaults:

- Recording is off by default
- No face recognition / covert identity tracking is implemented
