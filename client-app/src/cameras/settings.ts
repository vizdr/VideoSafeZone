// The Settings screen's choices and wording, ported from client/index.html so the two pages say
// the same thing. The value lists must match what the Lambdas accept: tests/settings.spec.ts reads
// them out of cloud/lambda/*.py and fails when they drift.
import type { Camera, IrMode, RecordingMode } from "../api/types";

export const MODE_NAMES: Record<Exclude<RecordingMode, "manual">, string> = {
  motion: "Motion", cellMotion: "Cell grid", human: "Human",
};

// set_camera_mode.py VALID_MODES. "manual" is always possible; the other three need ONVIF events.
export const MODE_OPTIONS: { value: RecordingMode; label: string }[] = [
  { value: "manual", label: "Manual only" },
  { value: "motion", label: "Motion detected" },
  { value: "cellMotion", label: "Motion in zones (cell grid)" },
  { value: "human", label: "Human detected" },
];

/** Why a mode can't be chosen for this camera, or null when it can (FoundAndFixed.md #65:
 *  a mode the camera's ONVIF event list doesn't offer would never record anything). */
export function modeUnavailable(cam: Camera, mode: RecordingMode): string | null {
  if (mode === "manual") return null;
  if (!cam.supportsDetection) return "this camera has no ONVIF events";
  if (Array.isArray(cam.detectionModes) && !cam.detectionModes.includes(mode)) {
    return "this camera doesn't publish the events for this mode";
  }
  return null;   // detectionModes unknown (null): allowed, as the Lambda allows it
}

// One place for the mode wording, so the first render and the post-save message can't drift
// apart. The stream caveat matters: the event watcher publishes a clip trigger on detection, but
// clip_to_s3 pulls from KVS, which only has footage while Cloud recording runs -- a detection
// with it off produces nothing.
export function recModeHint(mode: RecordingMode, supportsDetection: boolean): string {
  if (!supportsDetection) return "no ONVIF — manual only";
  if (mode === "manual") return "manual only — clips are made with Record clip";
  return `${MODE_NAMES[mode]} detection armed — a clip is written ~40 s after a detection ` +
    "(post-roll + processing) and appears in Events within 30 s. Requires Cloud recording to be on.";
}

export interface TriggerNote {
  /** The camera's own settings page; a LAN address, so the link works only from home. */
  webUrl: string | null;
  /** "Motion ✓ 31/10/2026" for each mode tested from the admin GUI. */
  lastTests: string[];
}

/** Detection triggers may need switching on in the camera's own settings (FoundAndFixed.md #65). */
export function triggerNote(cam: Camera, locale?: string): TriggerNote {
  const u = typeof cam.webUrl === "string" && /^http:\/\/[A-Za-z0-9.-]+(:\d+)?\/$/.test(cam.webUrl)
    ? cam.webUrl : null;
  const tests = cam.detectionTests || {};
  const lastTests = (Object.keys(MODE_NAMES) as (keyof typeof MODE_NAMES)[])
    .filter(m => tests[m])
    .map(m => `${MODE_NAMES[m]} ${tests[m]!.result === "seen" ? "✓" : "✗"} ` +
              new Date(tests[m]!.at).toLocaleDateString(locale));
  return { webUrl: u, lastTests };
}

// set_camera_outage_buffer.py VALID_LIMITS (and adapter/outage_buffer.py). The disk figures are for
// cam-01 with audio (the expensive case, ~1.26 Mbps measured) and are shown because 24 h is
// 13.6 GB of a 57 GB stick -- a number worth seeing before choosing it, not after.
export const OUTAGE_LIMITS: { sec: number; label: string }[] = [
  { sec: 0, label: "Off" },
  { sec: 30, label: "30 seconds" },
  { sec: 120, label: "2 minutes" },
  { sec: 300, label: "5 minutes" },
  { sec: 600, label: "10 minutes" },
  { sec: 1800, label: "30 minutes" },
  { sec: 3600, label: "1 hour  (~570 MB)" },
  { sec: 18000, label: "5 hours  (~2.8 GB)" },
  { sec: 43200, label: "12 hours  (~6.8 GB)" },
  { sec: 86400, label: "24 hours  (~13.6 GB)" },
];

export function outageLabel(sec: number): string {
  const m = OUTAGE_LIMITS.find(o => o.sec === Number(sec));
  return m ? m.label.replace(/\s*\(.*\)$/, "") : `${sec}s`;
}

export function outageHint(sec: number): string {
  if (!Number(sec)) return "off — footage is lost if the connection drops for more than ~2 minutes";
  return `on — up to ${outageLabel(sec)} after a drop, plus the 2 minutes before it`;
}

// The "next Start" caveat is not a UI nicety and must not be softened: the producer reads
// audioEnabled once at startup and never re-reads, because KVS rejects a stream whose fragments
// change from video-only to audio+video mid-stream (guide §18). Applying it live would break clips
// across the boundary, so a restart is the correct behaviour, not a limitation.
export function audioHint(enabled: boolean, capable: boolean): string {
  if (!capable) return "this camera has no microphone";
  return enabled
    ? "microphone is included — you can hear it live and in clips"
    : "video only — no microphone in the live view or in clips";
}

export function audioSaved(enabled: boolean, streaming: boolean): string {
  const target = enabled ? "sound will be recorded" : "video only";
  return streaming
    ? `Saved — ${target} from the next Start. Switch Cloud recording off and on to apply it now.`
    : `Saved — ${target} from the next Start.`;
}

// publish_cmd.py ALLOWED_IR_MODES. ONVIF IrCutFilter: ON = the IR-cut filter is in (day: colour,
// IR LEDs off), OFF = it is out (night: black-and-white, IR on). The old labels had it the other
// way round (FoundAndFixed.md #64).
export const IR_MODES: { value: IrMode; label: string; title: string }[] = [
  { value: "AUTO", label: "Auto", title: "The camera switches by itself" },
  { value: "ON", label: "Day", title: "Force day: colour, IR off" },
  { value: "OFF", label: "Night", title: "Force night: black-and-white, IR on" },
];

export const CLOUD_RECORDING_HINT =
  "Pushes this camera to the cloud archive — needed for clips and for detection recording. " +
  "Costs while it runs: switch it off when done.";
