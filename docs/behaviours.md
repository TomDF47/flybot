# Behaviour coverage status

## Implemented now

- STOP/e-stop safety path
- NAVIGATE (target pose + heading correction)
- TURN/FORWARD/RETREAT intent handling in `L0FlyCoreController`
- Deterministic color/shape target resolution via world snapshot
- PATROL waypoint traversal and OBSERVE dwell with change counting
- FOLLOW with bounded min/max distance behavior and TARGET_LOST failure handling
- TOUCH/NUDGE approach-contact-retract sequencing with force-limit checks
- INSPECT multi-viewpoint arc sequencing plus RETURN_HOME chaining
- Mission timeline events surfaced to operator API/UI

## Stubbed / partial

- Full FlyGym 2.x body stepping implementation (`FlyGymBodyAdapter`) is still TODO.
- OpenAI-driven plan quality for complex missions is not acceptance-tested; deterministic FakeBrain is default for CI.
- Contact calibration currently uses mock-force profiling hook; real FlyGym force scale calibration remains pending.
