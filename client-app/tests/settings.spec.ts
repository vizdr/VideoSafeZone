// The Settings screen (WEB-PANEL-PLAN.md Phase 1): each control saves on the viewer's change only,
// puts the old value back when the server refuses, and says the same as the old page. The API is
// faked here; that the value lists match the Lambdas is tests/test_value_lists.py's job (pytest).
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import Select from "openvue/select";
import ToggleSwitch from "openvue/toggleswitch";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DefineComponent } from "vue";
import type { Camera } from "../src/api/types";

const fake = vi.hoisted(() => {
  const ok = { ok: true, status: 200, data: {} };
  return {
    ok,
    endpoints: {
      setAudio: vi.fn(async () => ok),
      setRecordingMode: vi.fn(async () => ok),
      setOutageBuffer: vi.fn(async () => ok),
      setIrMode: vi.fn(async () => ok),
      cmd: vi.fn(async () => ok),
      hls: vi.fn(async () => ({ ok: false, status: 503, data: {} })),
      listCameras: vi.fn(),
    },
  };
});
vi.mock("../src/api", () => ({
  endpoints: fake.endpoints,
  session: { signedIn: true, username: "demo", onChange: () => () => {} },
}));

import {
  audioHint, modeUnavailable, outageHint, outageLabel, recModeHint, triggerNote,
} from "../src/cameras/settings";
import CameraSection from "../src/components/settings/CameraSection.vue";
import CloudRecording from "../src/components/settings/CloudRecording.vue";
import RecordingSection from "../src/components/settings/RecordingSection.vue";
import { useCamerasStore } from "../src/stores/cameras";
import { usePlayersStore } from "../src/stores/players";

const cam = (over: Partial<Camera> = {}): Camera => ({
  id: "cam-02", mode: "passthrough", hasIrControl: true, recordingMode: "manual", supportsDetection: true,
  audioCapable: true, audioEnabled: false, outageBufferSec: 0, videoCodec: "h264", videoEncode: "camera",
  liveView: "sub", liveVideoCodec: "h264", detectionModes: ["motion", "cellMotion"], ...over,
});
const refused = { ok: false, status: 409, data: { error: "'cam-02' is unregistered: re-register it in the admin GUI first" } };

/** Mounts a section for the camera held in the store, as SettingsView does. */
function mountSection(component: unknown, camera: Camera) {
  const store = useCamerasStore();
  store.cameras = [camera];
  store.loaded = true;
  return mount(component as DefineComponent<{ camera: Camera }>, {
    props: { camera: store.cameras[0] },
    global: { plugins: [[OpenVue, { unstyled: true }]] },
  });
}
/** The section follows the store, as it does under SettingsView's computed camera. */
async function refresh(w: ReturnType<typeof mountSection>) {
  await w.setProps({ camera: useCamerasStore().cameras[0] });
}
const saves = () => Object.entries(fake.endpoints)
  .filter(([n]) => n.startsWith("set")).flatMap(([, f]) => f.mock.calls);

const notLive = { ok: false, status: 503, data: {} };

beforeEach(() => {
  setActivePinia(createPinia());
  // Every test starts from the same answers: accepted saves, and no footage in the archive.
  vi.resetAllMocks();
  for (const f of [fake.endpoints.setAudio, fake.endpoints.setRecordingMode, fake.endpoints.setOutageBuffer,
                   fake.endpoints.setIrMode, fake.endpoints.cmd]) f.mockResolvedValue(fake.ok);
  fake.endpoints.hls.mockResolvedValue(notLive);
});
afterEach(() => usePlayersStore().clear());

describe("wording, as on the old page", () => {
  it("explains each mode, and which ones a camera can't do (#65)", () => {
    expect(recModeHint("manual", false)).toBe("no ONVIF — manual only");
    expect(recModeHint("motion", true)).toMatch(/^Motion detection armed — /);
    expect(modeUnavailable(cam(), "manual")).toBeNull();
    expect(modeUnavailable(cam(), "human")).toBe("this camera doesn't publish the events for this mode");
    expect(modeUnavailable(cam({ detectionModes: null }), "human")).toBeNull();
    expect(modeUnavailable(cam({ supportsDetection: false }), "motion")).toBe("this camera has no ONVIF events");
  });

  it("names outage durations and audio states", () => {
    expect(outageLabel(3600)).toBe("1 hour");
    expect(outageHint(0)).toMatch(/^off — footage is lost/);
    expect(outageHint(120)).toBe("on — up to 2 minutes after a drop, plus the 2 minutes before it");
    expect(audioHint(true, false)).toBe("this camera has no microphone");
    expect(audioHint(false, true)).toBe("video only — no microphone in the live view or in clips");
  });

  it("links the camera's page only when it is a plain LAN address, and lists the last tests", () => {
    const n = triggerNote(cam({
      webUrl: "http://192.168.1.64/",
      detectionTests: { motion: { result: "seen", at: "2026-10-30T10:00:00Z" }, human: { result: "none", at: "2026-10-30T10:00:00Z" } },
    }), "en-GB");
    expect(n).toEqual({ webUrl: "http://192.168.1.64/", lastTests: ["Motion ✓ 30/10/2026", "Human ✗ 30/10/2026"] });
    expect(triggerNote(cam({ webUrl: "javascript:alert(1)" })).webUrl).toBeNull();
  });
});

describe("Camera section", () => {
  it("saves nothing just by showing the settings", async () => {
    mountSection(CameraSection, cam());
    mountSection(RecordingSection, cam({ recordingMode: "motion", outageBufferSec: 120 }));
    await flushPromises();
    expect(saves()).toEqual([]);
  });

  it("Record microphone saves on change and says when it applies", async () => {
    const w = mountSection(CameraSection, cam());
    expect(w.text()).toContain("video only — no microphone in the live view or in clips");
    w.findComponent(ToggleSwitch).vm.$emit("update:modelValue", true);
    await flushPromises();
    await refresh(w);
    expect(fake.endpoints.setAudio).toHaveBeenCalledWith("cam-02", true);
    expect(useCamerasStore().cameras[0].audioEnabled).toBe(true);
    expect(w.findComponent(ToggleSwitch).props("modelValue")).toBe(true);
    expect(w.text()).toContain("Saved — sound will be recorded from the next Start.");
  });

  it("a refused save puts the switch back and shows why", async () => {
    fake.endpoints.setAudio.mockResolvedValueOnce(refused);
    const w = mountSection(CameraSection, cam());
    w.findComponent(ToggleSwitch).vm.$emit("update:modelValue", true);
    await flushPromises();
    await refresh(w);
    expect(useCamerasStore().cameras[0].audioEnabled).toBe(false);
    expect(w.findComponent(ToggleSwitch).props("modelValue")).toBe(false);
    expect(w.text()).toContain("Not saved: 'cam-02' is unregistered: re-register it in the admin GUI first");
  });

  it("a camera with no microphone can't switch it on", () => {
    const w = mountSection(CameraSection, cam({ audioCapable: false }));
    expect(w.findComponent(ToggleSwitch).props("disabled")).toBe(true);
    expect(w.text()).toContain("This camera has no microphone.");
  });

  it("IR buttons send the mode the label means (#64), and only for a camera with IR", async () => {
    const w = mountSection(CameraSection, cam());
    const night = w.findAll("button").find(b => b.text() === "Night")!;
    await night.trigger("click");
    await flushPromises();
    expect(fake.endpoints.setIrMode).toHaveBeenCalledWith("cam-02", "OFF");
    expect(w.text()).toContain("Sent to the Pi: Night.");
    expect(mountSection(CameraSection, cam({ hasIrControl: false })).text()).not.toContain("IR illumination");
  });

  it("warns when this browser can't play the camera's H.265", () => {
    expect(mountSection(CameraSection, cam({ videoCodec: "h265" })).text())
      .toContain("This browser can't play H.265 video — live view and clips of this camera won't show here.");
    expect(mountSection(CameraSection, cam({ liveVideoCodec: "h265" })).text())
      .toContain("This camera's live view (its sub stream) is H.265");
    expect(mountSection(CameraSection, cam()).text()).not.toContain("can't play");
  });
});

describe("Recording section", () => {
  it("greys out the modes the camera doesn't publish, and saves a chosen one", async () => {
    const w = mountSection(RecordingSection, cam());
    const select = w.findAllComponents(Select)[0];
    const disabled = (select.props("options") as { value: string; disabled: boolean }[])
      .filter(o => o.disabled).map(o => o.value);
    expect(disabled).toEqual(["human"]);
    select.vm.$emit("update:modelValue", "motion");
    await flushPromises();
    expect(fake.endpoints.setRecordingMode).toHaveBeenCalledWith("cam-02", "motion");
    expect(w.text()).toContain("Saved — Motion detection armed");
  });

  it("a camera without ONVIF events is manual only", () => {
    const w = mountSection(RecordingSection, cam({ supportsDetection: false }));
    expect(w.findAllComponents(Select)[0].props("disabled")).toBe(true);
    expect(w.text()).toContain("no ONVIF — manual only");
  });

  it("If the internet drops saves the number of seconds", async () => {
    const w = mountSection(RecordingSection, cam());
    w.findAllComponents(Select)[1].vm.$emit("update:modelValue", 120);
    await flushPromises();
    expect(fake.endpoints.setOutageBuffer).toHaveBeenCalledWith("cam-02", 120);
    expect(w.text()).toContain("Saved — on — up to 2 minutes after a drop");
  });
});

describe("Cloud recording", () => {
  function mountSwitch(camera = cam()) {
    const w = mountSection(CloudRecording, camera);
    usePlayersStore().sync(useCamerasStore().cameras);
    return w;
  }

  it("shows Off until KVS reports footage, and On when it does (#83)", async () => {
    const w = mountSwitch();
    await flushPromises();
    expect(w.text()).toContain("Off — nothing is sent to the cloud.");
    expect(fake.endpoints.hls).toHaveBeenCalledWith("cam-02", "recorded", undefined);

    fake.endpoints.hls.mockResolvedValue({ ok: true, status: 200, data: { url: "u" } });
    usePlayersStore().get("cam-02")!.onVisible();
    await flushPromises();
    expect(w.findComponent(ToggleSwitch).props("modelValue")).toBe(true);
    expect(w.text()).toContain("On — footage is reaching the cloud archive.");
  });

  it("switching on sends Start and waits for the first footage", async () => {
    const w = mountSwitch();
    await flushPromises();
    w.findComponent(ToggleSwitch).vm.$emit("update:modelValue", true);
    await flushPromises();
    expect(fake.endpoints.cmd).toHaveBeenCalledWith("cam-02", "start");
    expect(w.findComponent(ToggleSwitch).props("modelValue")).toBe(true);
    expect(w.text()).toContain("Starting — waiting for the first footage to reach the cloud (up to 45 s).");
  });

  it("a refused Start says why and leaves the switch off", async () => {
    fake.endpoints.cmd.mockResolvedValueOnce(refused);
    const w = mountSwitch();
    await flushPromises();
    w.findComponent(ToggleSwitch).vm.$emit("update:modelValue", true);
    await flushPromises();
    expect(w.findComponent(ToggleSwitch).props("modelValue")).toBe(false);
    expect(w.text()).toContain("Not sent: 'cam-02' is unregistered");
  });
});
