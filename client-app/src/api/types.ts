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
  /** The adapter that serves it (DEALER-PANEL-PLAN.md D4); null for an administrator's own. */
  adapterId?: string | null;
  /** "control", or "view" for a camera shared with the caller to watch (DEALER-PANEL-PLAN.md D18). */
  access?: "control" | "view";
  /** Unregistered (the soft mark): listed, out of Live, with Re-register. */
  unregisteredAt?: string | null;
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
  accounts: Array<{ id: string; name: string; role: MemberRole; dealer: string;
                    sites: Array<{ id: string; name: string; timezone: string }>;
                    /** Where the account's cameras are (the camera list's groups): every adapter for a
                     *  holder or Super Admin, those of the cameras shared for a user. */
                    adapters?: Array<{ thingName: string; name: string | null; siteId: string }> }>;
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
  holder: { email: string; status: DirectoryUser["status"] } | null;
  /** The adapters' Thing names; registered: their cameras in the registry (null: couldn't be read). */
  things: string[]; registered: number | null;
  /** Cameras in the current plans, and the plans' monthly total (trial and active ones). */
  inPlans: number; centsMonth: number; currency: string;
}

export interface AdapterSummary {
  id: string; thingName: string; status: "provisioned" | "activated" | "released" | "revoked";
  label: string | null; lastSeenAt: string | null; dealer: { id: string; name: string } | null;
  activation: { name: string | null; activatedAt: string; customer: { id: string; name: string }; site: string;
                siteId: string } | null;
  /** Connected now (the last IoT lifecycle event); null: not seen since Phase 6. */
  connected: boolean | null;
  /** A Cloud ID is waiting to be used; identity: the owner's script made its certificate. */
  hasCode: boolean; identity: boolean;
}

// --- adapters' activation (DEALER-PANEL-PLAN.md Phase 6, D15) ---------------------------------------

/** What follows a change: cameras taken out of view, and what couldn't be done (Sync does it again). */
export interface AdapterFollowUp {
  camerasReleased?: number; registryError?: string; configUnsent?: string;
  /** For the owner: the script to run (a new identity, or deactivating one). */
  identityCommand?: string;
}
export interface NewAdapters extends AdapterFollowUp {
  adapters: Array<{ id: string; thingName: string; cloudId: string }>;
  identityCommand: string;
}
export interface AdapterChange extends AdapterFollowUp {
  id: string; thingName: string; cloudId?: string; dealerId?: string | null; activated?: boolean;
}
export interface Activation extends AdapterFollowUp {
  adapterId: string; thingName: string; customerId: string; siteId: string; activationId: string;
}
export interface ActivationRequest {
  cloudId: string; accountId?: string; customerId?: string;
  siteId?: string; newSite?: { name: string; timezone: string }; name?: string;
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


// --- the dealer portal (DEALER-PANEL-PLAN.md Phase 5, §7.1, D19) -------------------------------------

export type Resolution = Price["resolution"];
export type RecordingClass = Price["recording"];
export type BillingInterval = "monthly" | "annual";
export type PlanStatus = "trial" | "active" | "cancelled" | "ended";
export interface PlanLine { resolution: Resolution; recording: RecordingClass; quantity: number }

export interface Plan {
  id: string; version: number; status: PlanStatus;
  /** A trial whose end has passed is an active plan. */
  effectiveStatus: PlanStatus;
  retentionDays: number; interval: BillingInterval;
  /** The price for the period, set by the server; centsMonth: what it is a month (0 unless billed). */
  centsPeriod: number; centsMonth: number; currency: string;
  trialEndsAt: string | null; createdAt: string; createdBy: string | null;
  lines: PlanLine[]; cameras: number;
}

export interface InventoryCamera {
  id: string; name: string | null; codec: string; size: string | null;
  class: Resolution | null; recordingMode: string; recording: RecordingClass; thumbnailAt: string | null;
}

export interface CustomerAdapter {
  id: string; activationId: string; thingName: string; label: string | null; name: string | null;
  lastSeenAt: string | null; activatedAt: string; site: { id: string; name: string; timezone: string };
  plan: Plan | null;
  /** From the registry; null when it couldn't be read. byClass keys: "4mp/motion". */
  cameras: InventoryCamera[] | null;
  actual: { total: number; byClass: Record<string, number>; unclassified: number } | null;
}

export interface CustomerAdapters {
  customer: { id: string; name: string };
  adapters: CustomerAdapter[];
  total: { adapters: number; centsMonth: number; cameras: number; currency: string };
  registryError?: string;
}

export interface CustomerDetail {
  id: string; name: string; reference: string | null; email: string | null; phone: string | null;
  status: OrgStatus; createdAt: string; dealer: { id: string; name: string };
  sites: Array<{ id: string; name: string; timezone: string }>;
  holder: { id: string; email: string; name: string | null; status: DirectoryUser["status"] } | null;
  users: number; adapters: number;
}

export interface NewCustomer {
  name: string; reference?: string; email?: string; phone?: string; dealerId?: string;
  site: { name: string; timezone: string }; holder?: { email: string; name?: string };
}

export interface CustomerPerson {
  id: string; email: string; name: string | null; status: DirectoryUser["status"];
  lastSignInAt: string | null; role: MemberRole; manageable: boolean;
}

export interface Address { line1?: string; line2?: string; city?: string; postcode?: string; country?: string }
export interface DealerDetails {
  id: string; name: string; email: string | null; phone: string | null; address: Address | null; createdAt: string;
}

// --- adding cameras from the web page (DEALER-PANEL-PLAN.md Phase 7, D16) -----------------------------

/** A device the adapter's scan found, marked against the registry: added here, removed here (adding
 *  it brings it back), another adapter's, or new (null). */
export interface FoundDevice {
  endpointRef: string; xaddr: string | null; host: string | null; port: number | null;
  name: string | null; hardware: string | null; state: "added" | "removed" | "elsewhere" | null;
  cameraId?: string; displayName?: string | null;
}
export interface CameraScan { requestId: string; at: string; devices: FoundDevice[]; error?: string }
export interface CameraRequest {
  requestId: string; status: "sent" | "registering" | "done" | "failed";
  cameraId?: string; error?: string; reactivated?: boolean;
}

// --- billing (DEALER-PANEL-PLAN.md Phase 8, D9): data real, money fake -------------------------------

/** A line of an invoice -- or of the estimate of the next one: an adapter's plan, billed for a month. */
export interface InvoiceLine {
  planId: string; activationId: string;
  /** What this month costs: an annual plan's line is a twelfth of its year (centsPeriod). */
  cents: number; currency: string;
  version: number; interval: BillingInterval; centsPeriod: number; retentionDays: number;
  thingName: string; name: string | null; site: string; cameras: number;
}
export interface Invoice {
  id: string; periodStart: string; periodEnd: string; cents: number; currency: string;
  status: "draft" | "issued" | "paid" | "void"; provider: string; createdAt: string;
  customer: { id: string; name: string }; dealer: { id: string; name: string }; lines: InvoiceLine[];
}
export interface InvoiceList { invoices: Invoice[]; truncated: boolean }
/** The provider's quote: what a run would bill for the month under way, now. */
export interface NextInvoice {
  period: string; periodStart: string; periodEnd: string; billedAt: string;
  lines: InvoiceLine[]; cents: number; currency: string;
  /** The month's invoice, when it has been billed already. */
  invoiceId: string | null;
}
export interface CustomerInvoices extends InvoiceList { next: NextInvoice }
export interface BillingRun {
  period: string; periodStart: string; periodEnd: string; billedAt: string;
  invoices: Array<{ id: string; customer: { id: string; name: string }; cents: number; currency: string; lines: number }>;
  alreadyInvoiced: number; nothingToBill: number;
  skipped: Array<{ customer: { id: string; name: string }; reason: string }>;
}

// --- an account's own pages (DEALER-PANEL-PLAN.md Phase 9, D18) ---------------------------------------

/** Plan & Billing, for the holder: each adapter's plan and history, read-only; the invoices. */
export interface AccountPlans extends InvoiceList {
  account: { id: string; name: string };
  dealer: { name: string; email: string | null; phone: string | null };
  adapters: Array<{ id: string; thingName: string; name: string | null; activatedAt: string; site: string;
                    plan: Plan | null; history: Plan[] }>;
  total: { adapters: number; centsMonth: number; cameras: number; currency: string };
  next: NextInvoice;
}
export interface AccountPerson {
  id: string; email: string; name: string | null; status: DirectoryUser["status"]; lastSignInAt: string | null;
  role: "customer" | "customer_admin" | "customer_user"; addedAt: string;
  /** The cameras shared with a user (none for the holder or a Super Admin, who see every one). */
  cameras: string[];
  /** The signed-in person themselves. */
  you: boolean;
}
export interface AccountUsers {
  account: { id: string; name: string };
  /** The viewer's role here (a system administrator's, for support). */
  role: "customer" | "customer_admin" | "system_admin";
  things: string[];
  people: AccountPerson[];
  /** The account's cameras to share, from the registry; null when it couldn't be read. */
  cameras: Array<{ id: string; name: string | null; thing: string }> | null;
  registryError?: string;
}
export interface AccountAccess { superAdmin: boolean; cameras: string[] }
