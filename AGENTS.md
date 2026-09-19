# FlyBot engineering instructions

## Goal
Build a reproducible embodied-agent research prototype. Natural-language missions are
converted into typed plans. The LLM must never directly control joints or torques.
All motion goes through `MissionExecutive -> IntentCommand -> FlyCore -> SafetyKernel -> BodyAdapter`.

## Non-negotiable architecture
- Keep OpenAI/model-specific code behind `CognitiveProvider`.
- Keep FlyGym-specific code behind `BodyAdapter`.
- Keep connectome fidelity behind `FlyCore` / `NeuralCircuitProvider`.
- STOP and e-stop must not require an LLM call.
- `SafetyKernel` is deterministic and has final veto/clamp authority.
- Recording is off by default.
- No face recognition or covert identity tracking.

## Engineering rules
- Python 3.12+, typed code, Pydantic schemas, asyncio at service boundaries.
- Use `uv`. Do not add a dependency without explaining why in the PR/task note.
- Prefer the smallest working vertical slice over speculative abstraction.
- Every behaviour has deterministic unit tests plus at least one simulation scenario test.
- Seed simulation tests. Record failing seeds.
- Never expose raw motor commands as LLM tools.
- All intents have TTL, timestamps, IDs, and explicit completion/failure semantics.
- Do not use simulator ground truth outside tests unless a scenario explicitly enables it.
- Run `make check` before declaring a task complete.

## Definition of done for each change
1. Implementation complete.
2. Tests added/updated and passing.
3. Types/lint passing.
4. Relevant scenario runnable.
5. Telemetry emitted for new states/actions.
6. Documentation updated if schema or behaviour changed.
7. Safety implications reviewed.
