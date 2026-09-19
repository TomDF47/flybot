import pytest

from flybot_body_flygym import MockBodyAdapter
from flybot_core.models import MotorCommand


@pytest.mark.asyncio
async def test_mock_body_adapter_contract() -> None:
    adapter = MockBodyAdapter(allow_ground_truth_for_tests=True, seed=42)
    body_state = await adapter.reset(seed=42)
    assert body_state.e_stop_latched is False

    next_state = await adapter.step(MotorCommand(forward=0.4, turn=0.0), dt_s=0.1)
    assert next_state.timestamp_ns >= body_state.timestamp_ns

    frame = adapter.low_rate_frame()
    assert frame.data_url is not None
    observation = adapter.high_rate_observation()
    assert observation.timestamp_ns > 0
    world_snapshot = adapter.world_snapshot()
    assert world_snapshot is not None

    await adapter.safe_stop()
    stopped_state = await adapter.step(MotorCommand(forward=0.8, turn=0.5), dt_s=0.1)
    assert stopped_state.linear_speed == 0.0
