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
  /** What the producer pulled at its last start: the stream and its picture size. */
  recordQualityActive?: { stream?: string; size?: string } | null;
  /** The streams the page may choose, the ceiling first; empty = nothing to choose. */
  recordQualityOptions?: string[];
  /** The Pi's latest frame of the camera (adapter/thumbnails.py): a link valid for an hour, and
   *  when it was taken. Null before the first one, or before provision.sh thumbnails. */
  thumbnailUrl?: string | null;
  thumbnailAt?: string | null;
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

// --- the directory (DEALER-PANEL-PLAN.md Phase 4; cloud/lambda/vsz_directory.py) -------------------

export type Role = "system_admin" | "system_owner" | "dealer" | "customer";
export type MemberRole = "system_admin" | "system_owner" | "dealer" | "customer" | "customer_admin" | "customer_user";
export type OrgStatus = "active" | "suspended" | "closed";

export interface Me {
  email: string;
  role: Role;
  org: { id: string; kind: "platform" | "dealer"; name: string } | null;
  accounts: Array<{ id: string; name: string; role: MemberRole; dealer: string }>;
}

export interface Overview {
  dealers: Partial<Record<OrgStatus, number>>;
  customers: Partial<Record<OrgStatus, number>>;
  users: Partial<Record<MemberRole, number>>;
  invited: number;
  adapters: Record<string, number>;
  plans: { trial: number; active: number; cancelled: number; monthlyCents: number; currency: string | null };
}

export interface Dealer {
  id: string; name: string; email: string | null; phone: string | null; status: OrgStatus;
  createdAt: string; customers: number; users: number; adapters: number;
}

export interface CustomerSummary {
  id: string; name: string; reference: string | null; status: OrgStatus; createdAt: string;
  dealer: { id: string; name: string }; adapters: number; users: number;
}

export interface AdapterSummary {
  id: string; thingName: string; status: string; label: string | null; lastSeenAt: string | null;
  dealer: { id: string; name: string } | null;
  activation: { name: string | null; activatedAt: string; customer: { id: string; name: string }; site: string } | null;
}

export interface DirectoryUser {
  id: string; email: string; name: string | null; status: "invited" | "active" | "disabled";
  lastSignInAt: string | null; createdAt: string;
  memberships: Array<{ orgId: string; org: string; kind: "platform" | "dealer" | "customer"; role: MemberRole }>;
}

export interface Price { resolution: "2mp" | "4mp" | "8mp"; recording: "motion" | "continuous"; retentionDays: number; centsMonth: number }
export interface Pricing {
  prices: Price[];
  settings: { retention_days: number[]; annual_discount_percent: number; trial_days: number;
              currency: string; placeholder_prices: boolean };
}

export type UserAction = "disable" | "enable" | "reset-password" | "resend-invite";

