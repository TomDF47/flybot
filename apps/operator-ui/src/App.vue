<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

const state = ref(null);
const instruction = ref("Walk to the red cube.");
const message = ref("");
let pollTimer = null;

const poseText = computed(() => {
  if (!state.value?.body?.pose) {
    return "pose unavailable";
  }
  const pose = state.value.body.pose;
  return `x=${pose.x.toFixed(2)}, y=${pose.y.toFixed(2)}, heading=${pose.heading_rad.toFixed(2)}rad`;
});

const timelineEvents = computed(() => state.value?.mission?.timeline ?? []);

async function fetchState() {
  const response = await fetch("/api/state");
  state.value = await response.json();
}

async function triggerEstop() {
  await fetch("/api/estop", { method: "POST" });
  message.value = "E-STOP latched";
  await fetchState();
}

async function resetSimulation() {
  await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seed: 42 })
  });
  message.value = "Simulation reset";
  await fetchState();
}

async function sendMission() {
  const response = await fetch("/api/missions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction: instruction.value })
  });
  const payload = await response.json();
  message.value = `Mission submitted: ${payload.mission_id}`;
}

async function toggleRecording() {
  const currentRecordingState = Boolean(state.value?.mission?.recording_enabled);
  const response = await fetch("/api/recording", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: !currentRecordingState })
  });
  const payload = await response.json();
  message.value = `Recording ${payload.recording_enabled ? "enabled" : "disabled"}`;
  await fetchState();
}

onMounted(async () => {
  await fetchState();
  pollTimer = setInterval(() => {
    fetchState().catch(() => {
      message.value = "Unable to load /api/state";
    });
  }, 500);
});

onUnmounted(() => {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
  }
});
</script>

<template>
  <main class="layout">
    <section class="panel panel-command">
      <h1>FlyBot Operator UI</h1>
      <div class="command-row">
        <input v-model="instruction" type="text" />
        <button class="primary" @click="sendMission">Send</button>
      </div>
      <p class="message">{{ message }}</p>
    </section>
    <section class="panel panel-camera">
      <h2>Camera</h2>
      <img
        v-if="state?.frame?.data_url"
        class="camera"
        :src="state.frame.data_url"
        alt="FlyBot camera"
      />
      <div v-else class="placeholder">No camera frame available</div>
    </section>
    <section class="panel panel-controls">
      <h2>Safety controls</h2>
      <button class="danger" @click="triggerEstop">E-STOP</button>
      <button @click="resetSimulation">Reset</button>
      <button @click="toggleRecording">
        {{ state?.mission?.recording_enabled ? "Disable recording" : "Enable recording" }}
      </button>
      <p>Backend: {{ state?.backend || "n/a" }}</p>
      <p>Safety flags: {{ (state?.safety_flags || []).join(", ") || "none" }}</p>
      <p>Recording: {{ state?.mission?.recording_enabled ? "ON" : "OFF" }}</p>
    </section>
    <section class="panel panel-telemetry">
      <h2>Telemetry</h2>
      <p>{{ poseText }}</p>
      <p>Linear speed: {{ state?.body?.linear_speed ?? "n/a" }}</p>
      <p>Angular speed: {{ state?.body?.angular_speed ?? "n/a" }}</p>
      <p>Controller mode: {{ state?.controller?.mode ?? "n/a" }}</p>
      <p>Mission id: {{ state?.mission?.mission_id ?? "none" }}</p>
      <p>Completed steps: {{ (state?.mission?.completed_steps || []).length }}</p>
      <p>Change events: {{ state?.mission?.change_events_count ?? 0 }}</p>
      <p>Follow min distance: {{ state?.mission?.follow_min_distance_observed ?? "n/a" }}</p>
    </section>
    <section class="panel panel-timeline">
      <h2>Mission timeline</h2>
      <ul class="timeline">
        <li v-for="(timelineEvent, index) in timelineEvents" :key="index">
          <strong>{{ timelineEvent.event }}</strong>
          <span>{{ JSON.stringify(timelineEvent.payload) }}</span>
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
