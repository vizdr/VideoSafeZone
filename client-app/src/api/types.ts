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
  tags?: string[];                             // WEB-PANEL-PLAN.md §5.1, Phase 4
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

export type CommandAction = "start" | "stop" | "live" | "live-stop";
export type IrMode = "AUTO" | "ON" | "OFF";
