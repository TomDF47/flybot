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
- JEV decision loop (default on): one bounded intent per iteration until the mission
  is done, with stale-response rejection, failure cooldown, and an offline selector
  when no JEV API key is configured
- FlyGym 2.x adapter path with custom arena geoms, joint state telemetry, contact extraction, and camera frames

## Stubbed / partial

- OpenAI-driven plan quality for complex missions is not acceptance-tested; deterministic FakeBrain is default for CI.
- Live JEV quality is not acceptance-tested. CI pins `JEV_ENABLED=false` for the scripted demos and covers the loop with the offline selector.
- Contact calibration currently uses mock-force profiling hook; real FlyGym force scale calibration remains pending.
