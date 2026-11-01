<script setup lang="ts">
import { computed, ref } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Select from "openvue/select";
import { endpoints } from "../../api";
import type { Camera, Day, Schedule, ScheduleEntry } from "../../api/types";
import {
  DAYS, DAY_LABEL, actionLabel, browserTimeZone, clash, daysLabel, hoursPerWeek, modeChangesByDay,
  recordingWindows, sortEntries, unattendedStart, windowsByDay,
} from "../../cameras/schedule";
import { MODE_OPTIONS, modeUnavailable } from "../../cameras/settings";
import { useSavedSetting } from "../../composables/useSavedSetting";
import { useCamerasStore } from "../../stores/cameras";
import SettingCard from "./SettingCard.vue";
import SettingRow from "./SettingRow.vue";

// The camera schedule (WEB-PANEL-PLAN.md D6, §4.2): weekly entries that start or stop cloud
// recording or set the recording mode. The Pi runs it every minute (adapter/schedule_runner.py),
// edge-triggered -- each entry acts once, when its time passes, so a manual change in between stands.
const props = defineProps<{ camera: Camera }>();
const cameras = useCamerasStore();

const schedule = computed<Schedule | null>(() => props.camera.schedule ?? null);
const entries = computed(() => sortEntries(schedule.value?.entries ?? []));
// An existing schedule keeps its zone; a new one takes this browser's.
const tz = computed(() => schedule.value?.tz ?? browserTimeZone());

const days = ref<Day[]>([]);
const at = ref("18:00");
const action = ref("start");
const problem = ref<string | null>(null);
const actions = computed(() => [
  { value: "start", label: actionLabel("start") },
  { value: "stop", label: actionLabel("stop") },
  ...MODE_OPTIONS.filter(o => !modeUnavailable(props.camera, o.value))
    .map(o => ({ value: `mode:${o.value}`, label: actionLabel(`mode:${o.value}`) })),
]);

const save = useSavedSetting<Schedule | null>({
  read: () => schedule.value,
  apply: v => cameras.patch(props.camera.id, { schedule: v }),
  send: v => endpoints.setCameraSettings(props.camera.id, { schedule: v }),
  saved: () => "Saved with the camera.",
});
const write = (list: ScheduleEntry[]) => save.save(list.length ? { tz: tz.value, entries: list } : null);

function toggleDay(d: Day): void {
  days.value = days.value.includes(d) ? days.value.filter(x => x !== d) : DAYS.filter(x => x === d || days.value.includes(x));
}
function add(): void {
  if (!days.value.length) { problem.value = "Pick at least one day."; return; }
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(at.value)) { problem.value = "Give a time, such as 18:00."; return; }
  const entry: ScheduleEntry = { days: [...days.value], at: at.value, action: action.value };
  problem.value = clash(entries.value, entry);
  if (problem.value) return;
  days.value = [];
  void write([...entries.value, entry]);
}
function remove(e: ScheduleEntry): void {
  void write(entries.value.filter(x => x !== e));
}

const windows = computed(() => windowsByDay(recordingWindows(entries.value)));
const marks = computed(() => modeChangesByDay(entries.value));
const hours = computed(() => hoursPerWeek(entries.value));
const unattended = computed(() => unattendedStart(entries.value));
const pct = (min: number) => `${(min / 1440) * 100}%`;
</script>

<template>
  <SettingCard icon="pi-calendar">
    <SettingRow title="Camera schedule" :status="problem ? { text: problem, tone: 'error' } : save.status.value">
      Starts and stops cloud recording, or changes the recording mode, at set times every week.
      <p class="pending">The Pi runs it every minute. Each entry acts once, when its time comes: a
        Start or Stop pressed in between stands until the next entry.</p>

      <div class="form" role="group" aria-label="New schedule entry">
        <div class="days" role="group" aria-label="Days">
          <button v-for="d in DAYS" :key="d" type="button" class="day" :class="{ on: days.includes(d) }"
                  :aria-pressed="days.includes(d)" @click="toggleDay(d)">{{ DAY_LABEL[d] }}</button>
        </div>
        <InputText v-model="at" type="time" size="small" aria-label="Time" class="time" />
        <Select v-model="action" :options="actions" option-label="label" option-value="value" size="small"
                aria-label="Action" class="action" />
        <Button label="Add" icon="pi pi-plus" size="small" :disabled="save.busy.value" @click="add" />
      </div>

      <div class="week" aria-hidden="true">
        <div v-for="d in DAYS" :key="d" class="row">
          <span class="dname">{{ DAY_LABEL[d] }}</span>
          <span class="bar">
            <span v-for="(w, i) in windows[d]" :key="i" class="on" :style="{ left: pct(w[0]), width: pct(w[1] - w[0]) }" />
            <span v-for="(m, i) in marks[d]" :key="`m${i}`" class="mark" :style="{ left: pct(m.at) }" :title="m.label" />
          </span>
        </div>
        <div class="row ticks"><span class="dname" /><span class="bar"><span v-for="h in [0, 6, 12, 18, 24]" :key="h" :style="{ left: pct(h * 60) }">{{ h }}</span></span></div>
      </div>

      <p class="summary">Cloud recording about {{ hours }} h a week · times in {{ tz }}</p>
      <p v-if="unattended" class="warn">A Start has no Stop within 24 hours: cloud recording would run
        unattended, and it costs money the whole time it runs.</p>

      <ul v-if="entries.length" class="entries">
        <li v-for="(e, i) in entries" :key="i">
          <span>{{ daysLabel(e.days) }} · {{ e.at }} · {{ actionLabel(e.action) }}</span>
          <Button icon="pi pi-trash" text rounded size="small" severity="danger" :disabled="save.busy.value"
                  :aria-label="`Delete: ${daysLabel(e.days)} ${e.at} ${actionLabel(e.action)}`" @click="remove(e)" />
        </li>
      </ul>
    </SettingRow>
  </SettingCard>
</template>

<style scoped>
.pending { margin: 0.4rem 0 0; }
.form { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin-top: 0.75rem; }
.days { display: flex; gap: 0.2rem; }
.day {
  min-width: 2.4rem;
  padding: 0.3rem 0.35rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 0.35rem;
  background: var(--p-surface-800);
  color: var(--p-text-muted-color);
  font: inherit;
  font-size: 0.8rem;
  cursor: pointer;
}
.day.on { background: var(--p-primary-color); color: var(--p-primary-contrast-color); border-color: var(--p-primary-color); }
.time { width: 7rem; }
.action { min-width: 14rem; }
.week { margin-top: 0.9rem; display: grid; gap: 0.25rem; }
.row { display: grid; grid-template-columns: 2.5rem 1fr; align-items: center; gap: 0.5rem; }
.dname { font-size: 0.75rem; color: var(--p-text-muted-color); }
.bar { position: relative; height: 0.8rem; border-radius: 0.2rem; background: var(--p-surface-900); }
.bar .on { position: absolute; top: 0; bottom: 0; background: var(--p-green-500); border-radius: 0.2rem; }
.bar .mark { position: absolute; top: -0.15rem; bottom: -0.15rem; width: 2px; background: var(--p-primary-color); }
.ticks .bar { background: none; height: 0.9rem; }
.ticks .bar span { position: absolute; transform: translateX(-50%); font-size: 0.7rem; color: var(--p-text-muted-color); }
.summary { margin: 0.6rem 0 0; }
.warn { color: var(--p-amber-400); margin: 0.3rem 0 0; }
.entries { list-style: none; margin: 0.6rem 0 0; padding: 0; }
.entries li { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.2rem 0;
              border-top: 1px solid var(--p-content-border-color); color: var(--p-text-color); font-size: 0.85rem; }
</style>
