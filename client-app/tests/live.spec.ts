// The Live grid (WEB-PANEL-PLAN.md 2.3) and the account's layout (D3, D4), with the API faked.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import Select from "openvue/select";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Camera } from "../src/api/types";

const fake = vi.hoisted(() => ({
  endpoints: {
    cmd: vi.fn(), hls: vi.fn(), recordClip: vi.fn(), listCameras: vi.fn(),
    getMySettings: vi.fn(), putMySettings: vi.fn(),
  },
}));
vi.mock("../src/api", () => ({
  endpoints: fake.endpoints,
  session: { signedIn: true, username: "demo", onChange: () => () => {} },
}));

import { makeRouter } from "../src/router";
import { useCamerasStore } from "../src/stores/cameras";
import { usePlayersStore } from "../src/stores/players";
import { useUserSettingsStore } from "../src/stores/userSettings";
import LiveView from "../src/views/LiveView.vue";
import LiveViewSettings from "../src/views/LiveViewSettings.vue";

const ok = { ok: true, status: 200, data: {} };
const cam = (id: string, over: Partial<Camera> = {}): Camera => ({
  id, mode: "passthrough", hasIrControl: false, recordingMode: "manual", supportsDetection: true,
  audioCapable: false, audioEnabled: false, outageBufferSec: 0, videoCodec: "h264", liveView: "sub",
  liveVideoCodec: "h264", ...over,
});
const four = ["cam-01", "cam-02", "cam-03", "cam-04"].map(id => cam(id));
const sent = (action: string) => fake.endpoints.cmd.mock.calls.filter(c => c[1] === action).map(c => c[0]);

async function mountLive(cameras: Camera[] = four, path = "/live") {
  const store = useCamerasStore();
  store.cameras = cameras;
  store.loaded = true;
  usePlayersStore().sync(cameras);                    // what the shell does when the list arrives
  const router = makeRouter(() => true);
  await router.push(path);
  const w = mount(LiveView, { global: { plugins: [router, [OpenVue, { unstyled: true }]] }, attachTo: document.body });
  await flushPromises();
  return { w, router };
}
const tileNames = (w: Awaited<ReturnType<typeof mountLive>>["w"]) =>
  w.findAll(".tile").map(t => (t.classes("empty") ? "-" : t.find(".label").text()));

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  fake.endpoints.cmd.mockResolvedValue(ok);
  fake.endpoints.hls.mockResolvedValue({ ok: false, status: 503, data: {} });
  fake.endpoints.putMySettings.mockResolvedValue(ok);
  fake.endpoints.recordClip.mockResolvedValue(ok);
});
afterEach(() => {
  usePlayersStore().clear();
  document.body.innerHTML = "";
});

describe("the Live grid", () => {
  it("lays out 2 + 6 by default: two large tiles, then the rest, then empty cells", async () => {
    const { w } = await mountLive();
    expect(tileNames(w)).toEqual(["cam-01", "cam-02", "cam-03", "cam-04", "-", "-", "-", "-"]);
    expect(w.findAll(".tile").map(t => t.classes("large"))).toEqual([true, true, false, false, false, false, false, false]);
  });

  it("plays every camera on the page, and ends each one when the tab is left", async () => {
    const { w } = await mountLive();
    expect(sent("live").sort()).toEqual(["cam-01", "cam-02", "cam-03", "cam-04"]);
    w.unmount();
    expect(sent("live-stop").sort()).toEqual(["cam-01", "cam-02", "cam-03", "cam-04"]);
  });

  it("doesn't start a live view this browser can't decode, and says why", async () => {
    const { w } = await mountLive([cam("cam-01"), cam("cam-02", { liveVideoCodec: "h265" })]);
    expect(sent("live")).toEqual(["cam-01"]);
    expect(w.findAll(".tile")[1].text()).toContain("This browser can't play this camera's H.265 live view.");
  });

  it("a click on a small tile brings it into the first large cell, without restarting anything", async () => {
    const { w, router } = await mountLive();
    await w.findAll(".tile")[3].find("button.surface").trigger("click");
    await flushPromises();
    expect(tileNames(w).slice(0, 4)).toEqual(["cam-04", "cam-02", "cam-03", "cam-01"]);
    expect(router.currentRoute.value.params.cameraId).toBe("cam-04");
    expect(w.findAll(".tile")[0].classes("selected")).toBe(true);
    expect(sent("live")).toHaveLength(4);
    expect(sent("live-stop")).toHaveLength(0);
  });

  it("puts the account's large-cell picks in the large cells", async () => {
    const s = useUserSettingsStore();
    s.largeCells = ["cam-03", ""];
    const { w } = await mountLive();
    expect(tileNames(w).slice(0, 4)).toEqual(["cam-03", "cam-01", "cam-02", "cam-04"]);
  });

  it("settings that arrive after the first layout start the selection at the first tile", async () => {
    const { w } = await mountLive();
    useUserSettingsStore().largeCells = ["cam-03", ""];
    await flushPromises();
    expect(tileNames(w)[0]).toBe("cam-03");
    expect(w.findAll(".tile")[0].classes("selected")).toBe(true);
  });

  it("changing the layout saves it to the account and re-lays the grid", async () => {
    const { w } = await mountLive();
    await w.find('button[title="Layout 16"]').trigger("click");
    await flushPromises();
    expect(fake.endpoints.putMySettings).toHaveBeenCalledWith({ liveLayout: "16" });
    expect(w.findAll(".tile")).toHaveLength(16);
    expect(w.findAll(".tile.large")).toHaveLength(0);
    expect(sent("live-stop")).toHaveLength(0);         // the same cameras stay on screen
  });

  it("pages when there are more cameras than tiles; turning the page ends the tiles that go", async () => {
    const ten = Array.from({ length: 10 }, (_, i) => cam(`cam-${String(i + 1).padStart(2, "0")}`));
    const { w } = await mountLive(ten);
    expect(w.text()).toContain("Page 1 of 2");
    expect(sent("live")).toHaveLength(8);
    await w.find('button[aria-label="Next page"]').trigger("click");
    await flushPromises();
    expect(tileNames(w).slice(0, 2)).toEqual(["cam-09", "cam-10"]);
    expect(sent("live-stop")).toHaveLength(8);
    expect(sent("live")).toHaveLength(10);
  });

  it("a camera picked in the list opens its page and comes into the first large cell", async () => {
    const twelve = Array.from({ length: 12 }, (_, i) => cam(`cam-${String(i + 1).padStart(2, "0")}`));
    const { w } = await mountLive(twelve, "/live/cam-12");   // page 2: cam-09..12, cam-12 small
    expect(w.text()).toContain("Page 2 of 2");
    expect(tileNames(w).slice(0, 4)).toEqual(["cam-12", "cam-10", "cam-11", "cam-09"]);
    expect(w.findAll(".tile")[0].classes("selected")).toBe(true);
  });

  it("the selected tile has the controls; Record clip waits for cloud recording (#83)", async () => {
    const { w } = await mountLive();
    const tiles = w.findAll(".tile");
    expect(tiles[0].find(".controls").exists()).toBe(true);
    expect(tiles[1].find(".controls").exists()).toBe(false);
    expect(tiles[0].find('button[aria-label="Record clip"]').attributes("disabled")).toBeDefined();
  });
});

describe("the account's Live settings", () => {
  it("loads the saved layout; ignores one it doesn't know", async () => {
    const s = useUserSettingsStore();
    fake.endpoints.getMySettings.mockResolvedValueOnce({ ok: true, status: 200, data: { settings: { liveLayout: "1+12", largeCells: ["cam-02"] } } });
    await s.load();
    expect([s.liveLayout, s.largeCells, s.problem]).toEqual(["1+12", ["cam-02"], null]);
    fake.endpoints.getMySettings.mockResolvedValueOnce({ ok: true, status: 200, data: { settings: { liveLayout: "9x9" } } });
    await s.load();
    expect(s.liveLayout).toBe("1+12");
  });

  it("before provision.sh has run, works with the defaults and keeps a change for this visit", async () => {
    const s = useUserSettingsStore();
    const missing = { ok: false, status: 403, data: { message: "Missing Authentication Token" } };
    fake.endpoints.getMySettings.mockResolvedValueOnce(missing);
    await s.load();
    expect(s.liveLayout).toBe("2+6");
    expect(s.problem).toMatch(/isn't set up yet/);
    fake.endpoints.putMySettings.mockResolvedValueOnce(missing);
    expect((await s.save({ liveLayout: "16" })).tone).toBe("info");
    expect(s.liveLayout).toBe("16");
  });

  it("a refused save puts the old value back", async () => {
    const s = useUserSettingsStore();
    fake.endpoints.putMySettings.mockResolvedValueOnce({ ok: false, status: 400, data: { error: "liveLayout must be one of" } });
    const status = await s.save({ liveLayout: "1+12" });
    expect(status).toEqual({ text: "Not saved: liveLayout must be one of", tone: "error" });
    expect(s.liveLayout).toBe("2+6");
  });

  it("Settings → Live view saves each large cell in its place", async () => {
    const store = useCamerasStore();
    store.cameras = four;
    store.loaded = true;
    const w = mount(LiveViewSettings, { global: { plugins: [[OpenVue, { unstyled: true }]] } });
    const selects = w.findAllComponents(Select);
    expect(selects).toHaveLength(2);                    // 2 + 6 has two large cells
    expect(selects[0].props("modelValue")).toBe("next");   // shown as chosen, stored as ""
    selects[1].vm.$emit("update:modelValue", "cam-03");
    await flushPromises();
    expect(fake.endpoints.putMySettings).toHaveBeenCalledWith({ largeCells: ["", "cam-03"] });
    expect(w.text()).toContain("Saved to your account.");
  });
});
