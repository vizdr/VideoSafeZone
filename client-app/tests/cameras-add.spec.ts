// Adding and removing cameras through the adapter (DEALER-PANEL-PLAN.md Phase 7, D16): asking again
// until the adapter's answer is the one awaited, the Add cameras screen -- the scan, the devices
// marked, the login sent once and not kept, progress, a silent adapter -- and Remove in Settings.
// The server's side: tests/cloud/test_adapter_cameras.py and tests/adapter/test_agent.py.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdapterSummary, Camera, CameraScan, Role } from "../src/api/types";

const fake = vi.hoisted(() => {
  const fn = () => vi.fn();
  return {
    session: { signedIn: true, username: "holder@example.test", claims: { vsz_role: "customer" } as Record<string, unknown>,
               onChange: () => () => {} },
    endpoints: {
      listAdapters: fn(), scanForCameras: fn(), cameraScan: fn(), addCamera: fn(), cameraRequest: fn(), removeCamera: fn(),
      reregisterCamera: fn(),
      listCameras: fn(), setAudio: fn(), setIrMode: fn(), cmd: fn(), hls: fn(),
    },
  };
});
vi.mock("../src/api", () => ({ session: fake.session, endpoints: fake.endpoints }));

import { pollUntil } from "../src/cameras/poll";
import CameraSection from "../src/components/settings/CameraSection.vue";
import { makeRouter } from "../src/router";
import { useCamerasStore } from "../src/stores/cameras";
import AddCamerasView from "../src/views/AddCamerasView.vue";

const ok = <T>(data: T) => Promise.resolve({ ok: true, status: 200, data });
const refused = (status: number, error: string) => Promise.resolve({ ok: false, status, data: { error } });
const e = fake.endpoints;
const REQ = "11111111-2222-4333-8444-555555555555";

const ADAPTER: AdapterSummary = {
  id: "a-1", thingName: "adapter-01", status: "activated", label: null, lastSeenAt: "2026-11-07T20:00:00Z", dealer: null,
  activation: { name: "Hall", activatedAt: "2026-11-01T10:00:00Z", customer: { id: "c-1", name: "Acme" }, site: "Home", siteId: "s-1" },
  connected: true, hasCode: false, identity: true,
};
const SCAN: CameraScan = { requestId: REQ, at: "2026-11-07T21:00:00Z", devices: [
  { endpointRef: "urn:a", xaddr: "http://192.0.2.12/onvif/device_service", host: "192.0.2.12", port: 80, name: "Door cam",
    hardware: "IPC", state: "added", cameraId: "cam-02", displayName: "Door" },
  { endpointRef: "urn:b", xaddr: "http://192.0.2.15/onvif/device_service", host: "192.0.2.15", port: 80, name: null,
    hardware: "IPC", state: "removed", cameraId: "cam-05" },
  { endpointRef: "urn:c", xaddr: "http://192.0.2.19/onvif/device_service", host: "192.0.2.19", port: 80, name: null,
    hardware: "IPC", state: "elsewhere" },
  { endpointRef: "urn:d", xaddr: "http://192.0.2.20/onvif/device_service", host: "192.0.2.20", port: 80, name: "Yard cam",
    hardware: "DS-2CD", state: null },
] };

const plugins = (router: ReturnType<typeof makeRouter>) => [router, [OpenVue, { unstyled: true }]] as never;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  document.body.innerHTML = "";
  fake.session.claims = { vsz_role: "customer" };
  e.listCameras.mockReturnValue(ok({ cameras: [] }));
});
afterEach(() => vi.useRealTimers());

// --- asking again ------------------------------------------------------------------------------------

describe("pollUntil", () => {
  it("asks until the answer is the awaited one, then gives up at the deadline or when stopped", async () => {
    vi.useFakeTimers();
    let n = 0;
    const found = pollUntil(async () => ++n, v => v === 3, { every: 1000, within: 10_000 });
    await vi.advanceTimersByTimeAsync(2000);
    expect(await found).toBe(3);
    const late = pollUntil(async () => null, () => true, { every: 1000, within: 3000 });
    await vi.advanceTimersByTimeAsync(5000);
    expect(await late).toBe("timeout");
    let stop = false;
    const stopped = pollUntil(async () => { stop = true; return 0; }, () => false, { every: 1000, within: 10_000, stopped: () => stop });
    await vi.advanceTimersByTimeAsync(1000);
    expect(await stopped).toBe("stopped");
  });
});

// --- Add cameras -------------------------------------------------------------------------------------

async function screen(adapters: AdapterSummary[] = [ADAPTER]) {
  e.listAdapters.mockReturnValue(ok(adapters));
  const router = makeRouter(() => true, () => "customer");
  await router.push("/cameras/add");
  const w = mount(AddCamerasView, { global: { plugins: plugins(router) }, attachTo: document.body });
  await flushPromises();
  return { w, router };
}

async function scanned(w: Awaited<ReturnType<typeof screen>>["w"]) {
  e.scanForCameras.mockReturnValue(ok({ requestId: REQ }));
  e.cameraScan.mockReturnValueOnce(ok({ ...SCAN, requestId: "an-older-scan" })).mockReturnValue(ok(SCAN));
  await w.find("[data-test=scan]").trigger("click");
  await flushPromises();
  await vi.advanceTimersByTimeAsync(2000);
  await flushPromises();
}

describe("Add cameras", () => {
  it("asks the adapter to look, waits for its own scan, and marks what is added already", async () => {
    vi.useFakeTimers();
    const { w, router } = await screen();
    expect(router.currentRoute.value.name).toBe("add-cameras");
    await scanned(w);
    expect(e.scanForCameras).toHaveBeenCalledWith("adapter-01");
    expect(e.cameraScan).toHaveBeenCalledTimes(2);                                // the older scan wasn't it
    const actions = (host: string) => w.find(`[data-test="device-${host}"]`).text();
    expect(actions("192.0.2.12")).toContain("Added as Door");
    expect(actions("192.0.2.15")).toContain("Unregistered: cam-05");             // Re-register, from its row
    expect(actions("192.0.2.19")).toContain("On another adapter");
    expect(actions("192.0.2.20")).toContain("Yard cam");
    expect(w.find(`[data-test="device-192.0.2.20"]`).find("button").text()).toBe("Add");
  });

  it("an adapter that doesn't answer within a minute is reported, with when it was last seen", async () => {
    vi.useFakeTimers();
    const { w } = await screen([{ ...ADAPTER, connected: false }]);
    e.scanForCameras.mockReturnValue(ok({ requestId: REQ }));
    e.cameraScan.mockReturnValue(refused(404, "no scan yet: ask for one"));
    await w.find("[data-test=scan]").trigger("click");
    await vi.advanceTimersByTimeAsync(61_000);
    await flushPromises();
    expect(w.find("[data-test=error]").text()).toContain("Hall didn't answer within a minute");
    expect(w.find("[data-test=error]").text()).toContain("last seen");
  });

  it("sends the login once with the request, keeps nothing, and shows progress until it is added", async () => {
    vi.useFakeTimers();
    const { w } = await screen();
    await scanned(w);
    await w.find(`[data-test="device-192.0.2.20"]`).find("button").trigger("click");
    expect((w.find("[data-test=name]").element as HTMLInputElement).value).toBe("Yard cam");
    await w.find("[data-test=user]").setValue("admin");
    await w.find("#camera-password").setValue("s3cret-pw");
    e.addCamera.mockReturnValue(ok({ requestId: REQ }));
    e.cameraRequest.mockReturnValueOnce(ok({ requestId: REQ, status: "registering" }))
      .mockReturnValue(ok({ requestId: REQ, status: "done", cameraId: "cam-10" }));
    await w.find("[data-test=add-form]").trigger("submit");
    await flushPromises();
    expect(e.addCamera).toHaveBeenCalledWith("adapter-01", { endpointRef: "urn:d", xaddr: SCAN.devices[3].xaddr, user: "admin",
                                                            password: "s3cret-pw", displayName: "Yard cam" });
    expect(w.find("[data-test=progress]").text()).toContain("Registering");
    expect((w.find("#camera-password").element as HTMLInputElement).value).toBe("");   // not kept
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(w.find("[data-test=added]").text()).toContain("Added as cam-10");
    expect(e.listCameras).toHaveBeenCalled();                                      // Live and Settings know it
  });

  it("a request the adapter refuses goes back to the form with the reason", async () => {
    vi.useFakeTimers();
    const { w } = await screen();
    await scanned(w);
    await w.find(`[data-test="device-192.0.2.20"]`).find("button").trigger("click");
    await w.find("#camera-password").setValue("wrong");
    e.addCamera.mockReturnValue(ok({ requestId: REQ }));
    e.cameraRequest.mockReturnValue(ok({ requestId: REQ, status: "failed",
                                        error: "the camera didn't answer with that login (ONVIFError)" }));
    await w.find("[data-test=add-form]").trigger("submit");
    await flushPromises();
    expect(w.find("[data-test=error]").text()).toContain("didn't answer with that login");
    expect(w.find("[data-test=add-form]").exists()).toBe(true);
    expect((w.find("#camera-password").element as HTMLInputElement).value).toBe("");
  });

  it("without an activated adapter it points to Add adapter", async () => {
    const { w } = await screen([{ ...ADAPTER, status: "released" }]);
    expect(w.find("[data-test=no-adapter]").text()).toContain("Add your Cloud Adapter");
  });

  it("is an account's page only", async () => {
    for (const [role, home] of [["dealer", "dealer-customers"], ["system_owner", "system-overview"]] as const) {
      const router = makeRouter(() => true, () => role as Role);
      await router.push("/cameras/add");
      expect(router.currentRoute.value.name).toBe(home);
    }
  });
});

// --- Remove camera in Settings ------------------------------------------------------------------------

const cam = (over: Partial<Camera> = {}): Camera => ({
  id: "cam-03", mode: "passthrough", hasIrControl: false, recordingMode: "manual", supportsDetection: true,
  audioCapable: false, audioEnabled: false, outageBufferSec: 0, videoCodec: "h264", liveView: "recorded",
  access: "control", adapterId: "adapter-01", ...over,
});

function section(camera: Camera, role: Role = "customer") {
  fake.session.claims = { vsz_role: role };
  const store = useCamerasStore();
  store.cameras = [camera];
  store.loaded = true;
  return mount(CameraSection, { props: { camera: store.cameras[0] }, global: { plugins: [[OpenVue, { unstyled: true }]] } });
}

describe("Unregister camera (Settings)", () => {
  it("asks the camera's own adapter after asking the person, then waits for the list to show it", async () => {
    vi.useFakeTimers();
    e.removeCamera.mockReturnValue(ok({ cameraId: "cam-03" }));
    e.listCameras.mockReturnValue(ok({ cameras: [cam({ unregisteredAt: "2026-11-08T10:00:00Z" })] }));
    const w = section(cam());
    await w.find("[data-test=remove-camera]").trigger("click");
    expect(e.removeCamera).not.toHaveBeenCalled();
    await w.find("[data-test=confirm-remove-camera]").trigger("click");
    await flushPromises();
    expect(e.removeCamera).toHaveBeenCalledWith("adapter-01", "cam-03");
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(e.listCameras).toHaveBeenCalled();
    expect(w.text()).toContain("Unregistered.");
  });

  it.each([
    ["the USB camera", cam({ mode: "transcode" }), "customer"],
    ["an administrator", cam(), "system_admin"],
  ] as const)("is offered for %s", (_, camera, role) => {
    expect(section(camera, role as Role).find("[data-test=remove-camera]").exists()).toBe(true);
  });

  it.each([
    ["a camera shared to watch", cam({ access: "view" }), "customer"],
    ["an administrator's own camera (no adapter to ask)", cam({ adapterId: null }), "customer"],
  ] as const)("isn't offered for %s", (_, camera, role) => {
    expect(section(camera, role as Role).find("[data-test=remove-camera]").exists()).toBe(false);
  });

  it("shows the API's refusal", async () => {
    e.removeCamera.mockReturnValue(refused(404, "no camera cam-03 on adapter-01"));
    const w = section(cam());
    await w.find("[data-test=remove-camera]").trigger("click");
    await w.find("[data-test=confirm-remove-camera]").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("no camera cam-03 on adapter-01");
  });
});

// --- unregistered cameras: listed, out of Live, re-registered (the user, 2026-11-08) ------------------

describe("an unregistered camera", () => {
  const USB = cam({ id: "cam-01", mode: "transcode", displayName: null as never });
  const GONE = cam({ id: "cam-05", unregisteredAt: "2026-11-08T10:00:00Z" });

  it("stays in the camera list, marked, and out of Live", async () => {
    const store = useCamerasStore();
    store.cameras = [USB, GONE];
    store.loaded = true;
    expect(store.active.map(c => c.id)).toEqual(["cam-01"]);
    const { default: CameraList } = await import("../src/components/CameraList.vue");
    const w = mount(CameraList, { global: { plugins: [[OpenVue, { unstyled: true }]] } });
    expect(w.findAll("button.cam").map(b => b.text())).toEqual(["cam-01", "cam-05unregistered"]);
  });

  it("comes back with Re-register in its Settings", async () => {
    vi.useFakeTimers();
    e.reregisterCamera.mockReturnValue(ok({ cameraId: "cam-05" }));
    e.listCameras.mockReturnValue(ok({ cameras: [cam({ id: "cam-05" })] }));
    const store = useCamerasStore();
    store.cameras = [GONE];
    const { default: UnregisteredCard } = await import("../src/components/settings/UnregisteredCard.vue");
    const w = mount(UnregisteredCard, { props: { camera: GONE }, global: { plugins: [[OpenVue, { unstyled: true }]] } });
    expect(w.text()).toContain("isn't in Live and records nothing");
    await w.find("[data-test=reregister]").trigger("click");
    await flushPromises();
    expect(e.reregisterCamera).toHaveBeenCalledWith("adapter-01", "cam-05");
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(w.text()).toContain("Back. Press Start to record it again.");
  });

  it("the adapter's Cameras page unregisters any of them, the USB camera too, and re-registers", async () => {
    vi.useFakeTimers();
    const store = useCamerasStore();
    store.cameras = [USB, GONE];
    store.loaded = true;
    e.removeCamera.mockReturnValue(ok({ cameraId: "cam-01" }));
    e.reregisterCamera.mockReturnValue(ok({ cameraId: "cam-05" }));
    // The list as the adapter leaves it: after the Unregister, then after the Re-register.
    e.listCameras.mockReturnValueOnce(ok({ cameras: [{ ...USB, unregisteredAt: "2026-11-08T11:00:00Z" }, GONE] }))
      .mockReturnValue(ok({ cameras: [{ ...USB, unregisteredAt: "2026-11-08T11:00:00Z" }, { ...GONE, unregisteredAt: null }] }));
    const { w } = await screen();
    const row = (id: string) => w.find(`[data-test=own-${id}]`);
    expect(row("cam-01").text()).toContain("USB");
    expect(row("cam-01").text()).toContain("in Live");
    expect(row("cam-05").text()).toContain("unregistered");
    await row("cam-01").findAll("button").find(b => b.text() === "Unregister")!.trigger("click");
    expect(e.removeCamera).not.toHaveBeenCalled();
    await w.find("[data-test=confirm-unregister]").trigger("click");
    await flushPromises();
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(e.removeCamera).toHaveBeenCalledWith("adapter-01", "cam-01");
    expect(row("cam-01").text()).toContain("Unregistered.");
    await row("cam-05").findAll("button").find(b => b.text() === "Re-register")!.trigger("click");
    await flushPromises();
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(e.reregisterCamera).toHaveBeenCalledWith("adapter-01", "cam-05");
    expect(row("cam-05").text()).toContain("in Live");
  });
});
