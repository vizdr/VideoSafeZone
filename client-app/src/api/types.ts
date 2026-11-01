// The shapes the Lambdas return. The source of truth is cloud/lambda/*.py; list_cameras.py
// documents each camera field where it is built.

export type RecordingMode = "manual" | "motion" | "cellMotion" | "human";
export type VideoCodec = "h264" | "h265";

export interface DetectionTest {
  result: string;   // "seen" or not
  at: string;       // ISO time
}

/** One camera from GET /cameras. Never carries a credential (no rtspUrl, no liveRtspUrl). */
export interface Camera {
  id: string;                                  // "cam-01": KVS stream, registry key, API field
  mode: "transcode" | "passthrough" | null;
  hasIrControl: boolean;
  recordingMode: RecordingMode;
  supportsDetection: boolean;
  audioCapable: boolean;
  audioEnabled: boolean;
  outageBufferSec: number;
  videoCodec: VideoCodec;
  videoEncode?: "camera" | "pi-hw" | null;
  videoCodecActive?: VideoCodec | null;
  displayName?: string | null;
  detectionModes?: RecordingMode[] | null;
  detectionTests?: Partial<Record<RecordingMode, DetectionTest>> | null;
  webUrl?: string | null;
  liveView: "sub" | "recorded";
  liveVideoCodec?: VideoCodec | null;
  // The web panel's per-camera settings (set-camera-settings, WEB-PANEL-PLAN.md §5.1)
  tags?: string[];
  schedule?: Schedule | null;
  /** The recorded-quality ceiling, set in the admin GUI (D5): stream, picture size, frame rate. */
  recordStream?: string | null;
  recordSize?: string | null;
  recordFps?: number | null;
  /** The cloud's choice below the ceiling; null = the ceiling. */
  recordQuality?: { stream: string } | null;
  recordQualityActive?: { stream?: string } | null;
  /** The streams the page may choose, the ceiling first; empty = nothing to choose. */
  recordQualityOptions?: string[];
}

export type Day = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";

/** One weekly entry: on these days at this time, start or stop cloud recording, or set a mode. */
export interface ScheduleEntry {
  days: Day[];
  at: string;                                  // "HH:MM", 24-hour, in the schedule's time zone
  action: string;                              // "start" | "stop" | "mode:<recordingMode>"
}

export interface Schedule {
  tz: string;                                  // IANA name, e.g. "Europe/Berlin"
  entries: ScheduleEntry[];
}

/** POST /cameras/settings: only the settings named are written; null or empty clears one. */
export interface CameraSettings {
  tags?: string[];
  schedule?: Schedule | null;
  recordQuality?: { stream: string } | null;
}

export type StorageTier = "STANDARD" | "STANDARD_IA" | "DEEP_ARCHIVE" | "GLACIER" | "UNKNOWN";

/** One evidence clip from GET /clips. */
export interface Clip {
  s3Key: string;
  startTs: string;
  tier: StorageTier;
  restoring?: boolean;
  durationSec?: number | null;
  labels?: string[];
  videoCodec?: VideoCodec | null;
}

export interface ClipsPage {
  clips: Clip[];
  page?: number;
  pages?: number;
  total?: number;
}

/** GET /hls: a KVS HLS session for the recorded stream or the live view. */
export interface HlsSession {
  url?: string;
  expires_in?: number;
}

/** GET/PUT /me/settings: the signed-in user's own settings (user_settings.py). */
export interface UserSettings {
  liveLayout?: string;                         // one of layouts/grid.ts LAYOUT_IDS
  largeCells?: string[];                       // camera IDs; "" = the next camera in list order
}

export type CommandAction = "start" | "stop" | "live" | "live-stop";
export type IrMode = "AUTO" | "ON" | "OFF";
