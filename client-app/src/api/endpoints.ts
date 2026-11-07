// One function per API route the page uses, with the request bodies the Lambdas expect.
import type { ApiClient } from "./client";
import type {
  ActivationRequest, Activation, AdapterChange, AdapterSummary, Address, NewAdapters, BillingInterval, Camera, CameraSettings, ClipsPage, CommandAction, CustomerAdapters,
  CustomerDetail, CustomerPerson, CustomerSummary, Dealer, DealerDetails, DirectoryUser, HlsSession, IrMode, Me,
  MemberRole, NewCustomer, Overview, Plan, PlanLine, Price, Pricing, RecordingMode, StorageTier, UserAction,
  UserSettings,
} from "./types";

const q = encodeURIComponent;

export function makeEndpoints(api: ApiClient) {
  return {
    listCameras: () => api.request<{ cameras: Camera[] }>("GET", "/cameras"),

    /** since: seconds since this page's Start, so a session holds only the new run (FoundAndFixed.md #66). */
    hls: (camera: string, view: "live" | "recorded", since?: string) =>
      api.request<HlsSession>("GET",
        `/hls?stream=${q(camera)}&view=${view}${since === undefined ? "" : `&since=${since}`}`),

    cmd: (camera: string, action: CommandAction, keepalive = false) =>
      api.request("POST", "/cmd", { action, camera }, { keepalive }),

    setIrMode: (camera: string, mode: IrMode) =>
      api.request("POST", "/cmd", { action: "ir", camera, mode }),

    setRecordingMode: (cameraId: string, recordingMode: RecordingMode) =>
      api.request("POST", "/cameras/mode", { cameraId, recordingMode }),

    setAudio: (cameraId: string, audioEnabled: boolean) =>
      api.request("POST", "/cameras/audio", { cameraId, audioEnabled }),

    setOutageBuffer: (cameraId: string, outageBufferSec: number) =>
      api.request("POST", "/cameras/outage-buffer", { cameraId, outageBufferSec }),

    setCameraSettings: (cameraId: string, settings: CameraSettings) =>
      api.request<{ saved?: CameraSettings }>("POST", "/cameras/settings", { cameraId, ...settings }),

    listClips: (camera: string, limit: number, page: number | "last") =>
      api.request<ClipsPage>("GET", `/clips?camera=${q(camera)}&limit=${limit}&page=${page}`),

    recordClip: (stream: string, startTs: string, endTs: string) =>
      api.request<{ keys?: string[] }>("POST", "/clips/record", { stream, startTs, endTs }),

    setClipTier: (s3Key: string, tier: StorageTier) =>
      api.request("POST", "/clips/tier", { s3Key, tier }),

    deleteClip: (s3Key: string, startTs: string) =>
      api.request("POST", "/clips/delete", { s3Key, startTs }),

    getMySettings: () => api.request<{ settings?: UserSettings }>("GET", "/me/settings"),

    /** Only the settings named are written; the others stay as they are. */
    putMySettings: (settings: UserSettings) =>
      api.request<{ settings?: UserSettings }>("PUT", "/me/settings", settings),

    playClip: (s3Key: string) =>
      api.request<{ status?: string; url?: string; eta_hours?: number }>("POST", "/clips/play", { s3Key }),

    // The directory (DEALER-PANEL-PLAN.md §6): vsz-directory-api decides who may do what.
    me: () => api.request<Me>("GET", "/directory/me"),
    overview: () => api.request<Overview>("GET", "/directory/overview"),
    listDealers: () => api.request<Dealer[]>("GET", "/directory/dealers"),
    createDealer: (dealer: { name: string; email?: string; phone?: string }) =>
      api.request<{ id: string }>("POST", "/directory/dealers", dealer),
    updateDealer: (id: string, change: Partial<Pick<Dealer, "name" | "email" | "phone" | "status">>) =>
      api.request<{ id: string; changed: string[] }>("PATCH", `/directory/dealers/${q(id)}`, change),
    listCustomers: (dealerId?: string) =>
      api.request<CustomerSummary[]>("GET", `/directory/customers${dealerId ? `?dealerId=${q(dealerId)}` : ""}`),
    listAdapters: () => api.request<AdapterSummary[]>("GET", "/directory/adapters"),
    listUsers: () => api.request<DirectoryUser[]>("GET", "/directory/users"),
    inviteUser: (invite: { email: string; name?: string; orgId: string; role: MemberRole }) =>
      api.request<{ userId: string; invited: string }>("POST", "/directory/users", invite),
    userAction: (userId: string, action: UserAction) =>
      api.request<{ userId: string; status: string }>("POST", `/directory/users/${q(userId)}/${action}`),
    getPricing: () => api.request<Pricing>("GET", "/directory/pricing"),
    putPricing: (prices: Price[], settings: Pick<Pricing["settings"], "retention_days" | "annual_discount_percent" | "trial_days">) =>
      api.request<{ prices: number }>("PUT", "/directory/pricing", { prices, settings }),

    // The dealer portal (DEALER-PANEL-PLAN.md Phase 5).
    createCustomer: (customer: NewCustomer) =>
      api.request<{ id: string; siteId: string; holder?: { userId: string; invited: string }; holderError?: string }>(
        "POST", "/directory/customers", customer),
    getCustomer: (id: string) => api.request<CustomerDetail>("GET", `/directory/customers/${q(id)}`),
    updateCustomer: (id: string, change: Partial<Pick<CustomerDetail, "name" | "reference" | "email" | "phone">
                                                & { status: "active" | "suspended" }>) =>
      api.request<{ id: string; changed: string[] }>("PATCH", `/directory/customers/${q(id)}`, change),
    customerAdapters: (id: string) => api.request<CustomerAdapters>("GET", `/directory/customers/${q(id)}/adapters`),
    customerUsers: (id: string) => api.request<CustomerPerson[]>("GET", `/directory/customers/${q(id)}/users`),
    /** basedOn: the version edited -- the server refuses (409) if another was saved meanwhile. */
    putPlan: (adapterId: string, plan: { lines: PlanLine[]; retentionDays: number; interval: BillingInterval; basedOn: number }) =>
      api.request<{ changed: boolean; plan: Plan }>("PUT", `/directory/adapters/${q(adapterId)}/plan`, plan),
    cancelPlan: (adapterId: string, basedOn: number) =>
      api.request<{ changed: boolean; plan: Plan }>("POST", `/directory/adapters/${q(adapterId)}/plan/cancel`, { basedOn }),
    planHistory: (adapterId: string) =>
      api.request<{ adapter: { thingName: string; name: string | null; site: string }; plans: Plan[] }>(
        "GET", `/directory/adapters/${q(adapterId)}/plans`),
    getDealer: () => api.request<DealerDetails>("GET", "/directory/dealer"),
    updateOwnDealer: (change: Partial<Pick<DealerDetails, "name" | "email" | "phone">> & { address?: Address | null }) =>
      api.request<{ id: string; changed: string[] }>("PATCH", "/directory/dealer", change),

    // Adapters (DEALER-PANEL-PLAN.md Phase 6, D15).
    generateAdapters: (count: number, label?: string, dealerId?: string) =>
      api.request<NewAdapters>("POST", "/directory/adapters", { count, label, dealerId }),
    activateAdapter: (activation: ActivationRequest) =>
      api.request<Activation>("POST", "/directory/adapters/activate", activation),
    allocateAdapter: (id: string, dealerId: string | null) =>
      api.request<AdapterChange>("POST", `/directory/adapters/${q(id)}/allocate`, { dealerId }),
    adapterAction: (id: string, action: "new-code" | "release" | "remove" | "revoke" | "sync") =>
      api.request<AdapterChange>("POST", `/directory/adapters/${q(id)}/${action}`),
  };
}

export type Endpoints = ReturnType<typeof makeEndpoints>;
