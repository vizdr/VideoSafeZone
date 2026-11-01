// The camera schedule (WEB-PANEL-PLAN.md D6): weekly entries that start or stop cloud recording or
// set the recording mode, run by the Pi from Phase 5. Edge-triggered: an entry acts once, when its
// time passes -- so recording runs from a Start until the next Stop, and a manual change in between
// stands. This file draws and checks a schedule; the rules it shares with set_camera_settings.py
// (days, time format, one change of a kind per moment) are checked there too.
import type { Day, RecordingMode, ScheduleEntry } from "../api/types";
import { MODE_OPTIONS } from "./settings";

export const DAYS: Day[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];   // set_camera_settings.py DAYS
export const DAY_LABEL: Record<Day, string> = {
  mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun",
};
const DAY_MIN = 1440;
export const WEEK_MIN = 7 * DAY_MIN;

export interface Window { start: number; end: number }     // minutes since Monday 00:00

export function minutesOf(at: string): number {
  const [h, m] = at.split(":").map(Number);
  return h * 60 + m;
}

export function isRecordingAction(action: string): boolean {
  return action === "start" || action === "stop";
}

export function actionLabel(action: string): string {
  if (action === "start") return "Start cloud recording";
  if (action === "stop") return "Stop cloud recording";
  const mode = action.replace(/^mode:/, "") as RecordingMode;
  return `Recording mode: ${MODE_OPTIONS.find(o => o.value === mode)?.label ?? mode}`;
}

/** "Mon–Fri", "Sat, Sun", "Mon, Wed–Fri": consecutive days as ranges, in week order. */
export function daysLabel(days: Day[]): string {
  const idx = DAYS.map((d, i) => (days.includes(d) ? i : -1)).filter(i => i >= 0);
  const runs: number[][] = [];
  for (const i of idx) {
    const last = runs[runs.length - 1];
    if (last && i === last[last.length - 1] + 1) last.push(i); else runs.push([i]);
  }
  return runs.map(r => (r.length > 2 ? `${DAY_LABEL[DAYS[r[0]]]}–${DAY_LABEL[DAYS[r[r.length - 1]]]}`
    : r.map(i => DAY_LABEL[DAYS[i]]).join(", "))).join(", ");
}

/** Entries in week order of their first day, then time. */
export function sortEntries(entries: ScheduleEntry[]): ScheduleEntry[] {
  const key = (e: ScheduleEntry) => Math.min(...e.days.map(d => DAYS.indexOf(d))) * DAY_MIN + minutesOf(e.at);
  return [...entries].sort((a, b) => key(a) - key(b));
}

function events(entries: ScheduleEntry[]): { t: number; action: string }[] {
  return entries.flatMap(e => (isRecordingAction(e.action)
    ? e.days.map(d => ({ t: DAYS.indexOf(d) * DAY_MIN + minutesOf(e.at), action: e.action })) : []))
    .sort((a, b) => a.t - b.t);
}

/** When cloud recording runs over a week: from each Start until the next Stop, wrapping from
 *  Sunday into Monday. No Start: never. Starts but no Stop: always, once started. */
export function recordingWindows(entries: ScheduleEntry[]): Window[] {
  const ev = events(entries);
  if (!ev.some(e => e.action === "start")) return [];
  if (!ev.some(e => e.action === "stop")) return [{ start: 0, end: WEEK_MIN }];
  let on = ev[ev.length - 1].action === "start";          // the week's last change carries over
  let openAt = 0;
  const out: Window[] = [];
  for (const e of ev) {
    if (e.action === "start" && !on) { on = true; openAt = e.t; }
    else if (e.action === "stop" && on) { if (e.t > openAt) out.push({ start: openAt, end: e.t }); on = false; }
  }
  if (on) out.push({ start: openAt, end: WEEK_MIN });
  return out;
}

export function hoursPerWeek(entries: ScheduleEntry[]): number {
  const min = recordingWindows(entries).reduce((n, w) => n + w.end - w.start, 0);
  return Math.round(min / 6) / 10;
}

/** A Start with no Stop in the 24 hours after it: cloud recording would run unattended, and a
 *  producer left running is what costs money (guide §1.2). */
export function unattendedStart(entries: ScheduleEntry[]): boolean {
  const ev = events(entries);
  const stops = ev.filter(e => e.action === "stop").map(e => e.t);
  return ev.filter(e => e.action === "start").some(s =>
    !stops.length || Math.min(...stops.map(t => (t - s.t + WEEK_MIN) % WEEK_MIN || WEEK_MIN)) > DAY_MIN);
}

/** The windows cut at midnight, per day, for drawing: [from, to] minutes within each day. */
export function windowsByDay(windows: Window[]): Record<Day, [number, number][]> {
  const out = Object.fromEntries(DAYS.map(d => [d, [] as [number, number][]])) as Record<Day, [number, number][]>;
  DAYS.forEach((d, i) => {
    const from = i * DAY_MIN, to = from + DAY_MIN;
    for (const w of windows) {
      const a = Math.max(w.start, from), b = Math.min(w.end, to);
      if (b > a) out[d].push([a - from, b - from]);
    }
  });
  return out;
}

/** The mode changes per day, for the week view's marks. */
export function modeChangesByDay(entries: ScheduleEntry[]): Record<Day, { at: number; label: string }[]> {
  const out = Object.fromEntries(DAYS.map(d => [d, [] as { at: number; label: string }[]])) as
    Record<Day, { at: number; label: string }[]>;
  for (const e of entries) {
    if (isRecordingAction(e.action)) continue;
    for (const d of e.days) out[d].push({ at: minutesOf(e.at), label: `${e.at} ${actionLabel(e.action)}` });
  }
  return out;
}

/** Why `entry` can't be added: another entry changes the same thing at the same moment. */
export function clash(entries: ScheduleEntry[], entry: ScheduleEntry): string | null {
  const kind = (a: string) => (isRecordingAction(a) ? "recording" : "mode");
  const other = entries.find(e => e.at === entry.at && kind(e.action) === kind(entry.action)
                                  && e.days.some(d => entry.days.includes(d)));
  if (!other) return null;
  const day = DAY_LABEL[entry.days.find(d => other.days.includes(d))!];
  return `Another entry already changes the ${kind(entry.action)} on ${day} at ${entry.at}.`;
}

/** This browser's time zone, for a new schedule (an existing one keeps its own). */
export function browserTimeZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}
