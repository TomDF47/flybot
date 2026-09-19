from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from flybot_api.service import FlyBotRuntime

app = FastAPI(title="FlyBot API", version="0.1.0")
runtime = FlyBotRuntime()


class ResetRequest(BaseModel):
    seed: int = 42


class MissionRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)


class RecordingRequest(BaseModel):
    enabled: bool


@app.on_event("startup")
async def startup_event() -> None:
    await runtime.start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await runtime.stop()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
async def api_state() -> dict[str, object]:
    status = runtime.status()
    return {
        "backend": status.backend,
        "body": status.body_state,
        "world": status.world_state.model_dump(mode="json"),
        "controller": status.controller_state,
        "mission": status.mission_state,
        "safety_flags": status.safety_flags,
        "frame": status.frame.model_dump(mode="json"),
    }


@app.post("/api/estop")
async def api_estop() -> dict[str, str]:
    await runtime.e_stop()
    return {"result": "e-stop latched"}


@app.post("/api/reset")
async def api_reset(reset_request: ResetRequest) -> dict[str, object]:
    await runtime.reset(seed=reset_request.seed)
    return {"result": "reset", "seed": reset_request.seed}


@app.post("/api/missions")
async def api_missions(mission_request: MissionRequest) -> dict[str, str]:
    mission_id = await runtime.submit_instruction(mission_request.instruction)
    return {"mission_id": mission_id}


@app.post("/api/recording")
async def api_recording(recording_request: RecordingRequest) -> dict[str, object]:
    runtime.set_recording(recording_request.enabled)
    return {"recording_enabled": recording_request.enabled}


@app.get("/api/missions/{mission_id}")
async def api_mission_state(mission_id: str) -> dict[str, object]:
    mission_state = runtime.status().mission_state
    if mission_state["mission_id"] != mission_id:
        raise HTTPException(status_code=404, detail="mission not found")
    return mission_state


@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            status = runtime.status()
            await websocket.send_json(
                {
                    "body": status.body_state,
                    "world": status.world_state.model_dump(mode="json"),
                    "controller": status.controller_state,
                    "safety_flags": status.safety_flags,
                    "mission": status.mission_state,
                    "frame": status.frame.model_dump(mode="json"),
                }
            )
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return
