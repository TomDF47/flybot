<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

const state = ref(null);
const instruction = ref("Walk to the red cube.");
const message = ref("");
const apiError = ref("");
const cameraDataUrl = ref(null);
const cameraError = ref("");
let pollTimer = null;
let cameraPollTimer = null;
let latestCameraTimestampNs = 0;

const STATE_POLL_INTERVAL_MS = 500;
const CAMERA_POLL_INTERVAL_MS = 2000;

function asObject(value) {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return value;
  }
  return {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asFiniteNumber(value) {
  if (typeof value !== "number" || Number.isNaN(value) || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

function formatPayload(payload) {
  try {
    return JSON.stringify(payload);
  } catch (error) {
    return `[unable to render payload: ${String(error)}]`;
  }
}

function extractErrorMessage(error, fallbackMessage) {
  if (error instanceof Error && error.message) {
    return `${fallbackMessage}: ${error.message}`;
  }
  return fallbackMessage;
}

const safeState = computed(() => {
  const normalizedState = asObject(state.value);
  return {
    backend: typeof normalizedState.backend === "string" ? normalizedState.backend : "n/a",
    body: asObject(normalizedState.body),
    controller: asObject(normalizedState.controller),
    mission: asObject(normalizedState.mission),
    safety_flags: asArray(normalizedState.safety_flags),
    frame: asObject(normalizedState.frame)
  };
});

const poseText = computed(() => {
  const pose = asObject(safeState.value.body.pose);
  const x = asFiniteNumber(pose.x);
  const y = asFiniteNumber(pose.y);
  const headingRadians = asFiniteNumber(pose.heading_rad);
  if (x === null || y === null || headingRadians === null) {
    return "pose unavailable";
  }
  return `x=${x.toFixed(2)}, y=${y.toFixed(2)}, heading=${headingRadians.toFixed(2)}rad`;
});

const timelineEvents = computed(() => asArray(safeState.value.mission.timeline));

const completedStepsCount = computed(() => asArray(safeState.value.mission.completed_steps).length);

const linearSpeedText = computed(() => {
  const linearSpeed = asFiniteNumber(safeState.value.body.linear_speed);
  return linearSpeed === null ? "n/a" : linearSpeed.toFixed(3);
});

const angularSpeedText = computed(() => {
  const angularSpeed = asFiniteNumber(safeState.value.body.angular_speed);
  return angularSpeed === null ? "n/a" : angularSpeed.toFixed(3);
});

const safetyFlagsText = computed(() => {
  const normalizedFlags = safeState.value.safety_flags.filter((flag) => typeof flag === "string");
  return normalizedFlags.length === 0 ? "none" : normalizedFlags.join(", ");
});

const missionIdentifier = computed(() => {
  const missionId = safeState.value.mission.mission_id;
  return typeof missionId === "string" && missionId.length > 0 ? missionId : "none";
});

async function fetchState() {
  try {
    const response = await fetch("/api/state?include_frame=0");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("payload is not a JSON object");
    }
    state.value = payload;
    apiError.value = "";
  } catch (error) {
    apiError.value = extractErrorMessage(error, "Unable to load /api/state");
  }
}

async function fetchCameraFrame() {
  try {
    const response = await fetch("/api/camera");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = asObject(await response.json());
    const frame = asObject(payload.frame);
    const frameDataUrl = typeof frame.data_url === "string" ? frame.data_url : null;
    const frameTimestampNs = asFiniteNumber(frame.timestamp_ns);

    if (frameTimestampNs !== null && frameTimestampNs <= latestCameraTimestampNs) {
      return;
    }
    if (frameTimestampNs !== null) {
      latestCameraTimestampNs = frameTimestampNs;
    }
    cameraDataUrl.value = frameDataUrl;
    cameraError.value = "";
  } catch (error) {
    cameraError.value = extractErrorMessage(error, "Unable to load /api/camera");
  }
}

async function triggerEstop() {
  try {
    const response = await fetch("/api/estop", { method: "POST" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    message.value = "E-STOP latched";
    await fetchState();
  } catch (error) {
    message.value = extractErrorMessage(error, "Failed to latch E-STOP");
  }
}

async function resetSimulation() {
  try {
    const response = await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seed: 42 })
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    message.value = "Simulation reset";
    await fetchState();
  } catch (error) {
    message.value = extractErrorMessage(error, "Reset failed");
  }
}

async function sendMission() {
  try {
    const response = await fetch("/api/missions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction: instruction.value })
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = asObject(await response.json());
    const missionId = typeof payload.mission_id === "string" ? payload.mission_id : "unknown";
    message.value = `Mission submitted: ${missionId}`;
  } catch (error) {
    message.value = extractErrorMessage(error, "Mission submit failed");
  }
}

async function toggleRecording() {
  const currentRecordingState = Boolean(safeState.value.mission.recording_enabled);
  try {
    const response = await fetch("/api/recording", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !currentRecordingState })
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = asObject(await response.json());
    const recordingEnabled = Boolean(payload.recording_enabled);
    message.value = `Recording ${recordingEnabled ? "enabled" : "disabled"}`;
    await fetchState();
  } catch (error) {
    message.value = extractErrorMessage(error, "Recording toggle failed");
  }
}

onMounted(() => {
  void fetchState();
  void fetchCameraFrame();
  pollTimer = setInterval(() => {
    void fetchState();
  }, STATE_POLL_INTERVAL_MS);
  cameraPollTimer = setInterval(() => {
    void fetchCameraFrame();
  }, CAMERA_POLL_INTERVAL_MS);
});

onUnmounted(() => {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
  }
  if (cameraPollTimer !== null) {
    clearInterval(cameraPollTimer);
  }
});
</script>

<template>
  <main class="layout">
    <section class="panel panel-command">
      <h1>FlyBot Operator UI</h1>
      <p v-if="apiError" class="error-banner">{{ apiError }}</p>
      <div class="command-row">
        <input v-model="instruction" type="text" />
        <button class="primary" @click="sendMission">Send</button>
      </div>
      <p class="message">{{ message }}</p>
    </section>
    <section class="panel panel-camera">
      <h2>Camera</h2>
      <p v-if="cameraError" class="error-banner">{{ cameraError }}</p>
      <img
        v-if="cameraDataUrl"
        class="camera"
        :src="cameraDataUrl"
        alt="FlyBot camera"
      />
      <div v-else class="placeholder">No camera frame available</div>
    </section>
    <section class="panel panel-controls">
      <h2>Safety controls</h2>
      <button class="danger" @click="triggerEstop">E-STOP</button>
      <button @click="resetSimulation">Reset</button>
      <button @click="toggleRecording">
        {{ safeState.mission.recording_enabled ? "Disable recording" : "Enable recording" }}
      </button>
      <p>Backend: {{ safeState.backend }}</p>
      <p>Safety flags: {{ safetyFlagsText }}</p>
      <p>Recording: {{ safeState.mission.recording_enabled ? "ON" : "OFF" }}</p>
    </section>
    <section class="panel panel-telemetry">
      <h2>Telemetry</h2>
      <p>{{ poseText }}</p>
      <p>Linear speed: {{ linearSpeedText }}</p>
      <p>Angular speed: {{ angularSpeedText }}</p>
      <p>Controller mode: {{ safeState.controller.mode ?? "n/a" }}</p>
      <p>Mission id: {{ missionIdentifier }}</p>
      <p>Completed steps: {{ completedStepsCount }}</p>
      <p>Change events: {{ safeState.mission.change_events_count ?? 0 }}</p>
      <p>Follow min distance: {{ safeState.mission.follow_min_distance_observed ?? "n/a" }}</p>
    </section>
    <section class="panel panel-timeline">
      <h2>Mission timeline</h2>
      <ul class="timeline">
        <li v-for="(timelineEvent, index) in timelineEvents" :key="index">
          <strong>{{ asObject(timelineEvent).event ?? "event" }}</strong>
          <span>{{ formatPayload(asObject(timelineEvent).payload) }}</span>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: repeat(2, minmax(340px, 1fr));
  gap: 1rem;
  padding: 1rem;
}

.panel {
  background: #111830;
  border: 1px solid #2a365f;
  border-radius: 10px;
  padding: 1rem;
}

.command-row {
  display: flex;
  gap: 0.5rem;
}

input {
  flex: 1;
  padding: 0.5rem;
  border-radius: 8px;
  border: 1px solid #42507d;
  background: #0a1024;
  color: #edf2ff;
}

button {
  padding: 0.6rem 0.9rem;
  border-radius: 8px;
  border: none;
  font-weight: 700;
  cursor: pointer;
}

.primary {
  background: #3c6ef2;
  color: #fff;
}

.danger {
  background: #ff304f;
  color: #fff;
  margin-right: 0.5rem;
}

.camera,
.placeholder {
  width: 100%;
  aspect-ratio: 4 / 3;
  border-radius: 8px;
  background: #000;
  border: 1px solid #334173;
}

.placeholder {
  display: grid;
  place-items: center;
}

.message {
  color: #8fb8ff;
  min-height: 1.2rem;
}

.error-banner {
  margin: 0 0 0.75rem;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #8b1c2f;
  background: #34101b;
  color: #ff9fb0;
}

.timeline {
  margin: 0;
  padding-left: 1.1rem;
  display: grid;
  gap: 0.35rem;
}

.timeline li {
  color: #d5e2ff;
}

.timeline span {
  margin-left: 0.35rem;
  color: #9cb2e8;
}
</style>
