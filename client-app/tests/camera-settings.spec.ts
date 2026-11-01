// Phase 4's per-camera settings (WEB-PANEL-PLAN.md D5, D6, §5.1): tags, recorded quality and the
// schedule -- their rules, and the Settings cards that save them. The API is faked.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import Select from "openvue/select";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DefineComponent } from "vue";
import type { Camera, ScheduleEntry } from "../src/api/types";

const fake = vi.hoisted(() => ({ endpoints: { setCameraSettings: vi.fn() } }));
vi.mock("../src/api", () => ({
  endpoints: fake.endpoints,
  session: { signedIn: true, username: "demo", onChange: () => () => {} },
}));

import {
  clash, daysLabel, hoursPerWeek, recordingWindows, unattendedStart, windowsByDay, WEEK_MIN,
} from "../src/cameras/schedule";
import { normalizeTag, tagProblem } from "../src/cameras/tags";
import QualityCard from "../src/components/settings/QualityCard.vue";
import ScheduleCard from "../src/components/settings/ScheduleCard.vue";
import TagsCard from "../src/components/settings/TagsCard.vue";
import { useCamerasStore } from "../src/stores/cameras";

const e = (days: string[], at: string, action: string) => ({ days, at, action }) as ScheduleEntry;
const WEEKNIGHTS = [
  e(["mon", "tue", "wed", "thu", "fri"], "18:00", "start"),
  e(["tue", "wed", "thu", "fri", "sat"], "07:00", "stop"),
];

describe("schedule rules", () => {
  it("records from each Start to the next Stop", () => {
    expect(recordingWindows(WEEKNIGHTS)).toHaveLength(5);
    expect(hoursPerWeek(WEEKNIGHTS)).toBe(65);                    // 5 nights of 13 h
    expect(unattendedStart(WEEKNIGHTS)).toBe(false);
  });

  it("wraps from Sunday into Monday", () => {
    const weekend = [e(["sun"], "22:00", "start"), e(["mon"], "06:00", "stop")];
    expect(recordingWindows(weekend)).toEqual([{ start: 0, end: 360 }, { start: 6 * 1440 + 1320, end: WEEK_MIN }]);
    const byDay = windowsByDay(recordingWindows(weekend));
    expect([byDay.mon, byDay.sun, byDay.wed]).toEqual([[[0, 360]], [[1320, 1440]], []]);
    expect(hoursPerWeek(weekend)).toBe(8);
  });

  it("no Start is never; Starts without a Stop are always, and unattended", () => {
    expect(recordingWindows([e(["mon"], "07:00", "stop")])).toEqual([]);
    expect(recordingWindows([e(["mon"], "07:00", "start")])).toEqual([{ start: 0, end: WEEK_MIN }]);
    expect(unattendedStart([e(["mon"], "07:00", "start")])).toBe(true);
  });

  it("warns about a Start whose Stop is more than 24 hours later", () => {
    expect(unattendedStart([e(["fri"], "18:00", "start"), e(["mon"], "07:00", "stop")])).toBe(true);
    expect(unattendedStart([e(["fri"], "18:00", "start"), e(["sat"], "17:00", "stop")])).toBe(false);
  });

  it("ignores mode changes for recording, and refuses two changes of a kind at one moment", () => {
    expect(hoursPerWeek([...WEEKNIGHTS, e(["mon"], "18:00", "mode:motion")])).toBe(65);
    expect(clash(WEEKNIGHTS, e(["fri", "sun"], "18:00", "stop"))).toBe("Another entry already changes the recording on Fri at 18:00.");
    expect(clash(WEEKNIGHTS, e(["mon"], "18:00", "mode:motion"))).toBeNull();
  });

  it("names days as ranges", () => {
    expect(daysLabel(["mon", "tue", "wed", "thu", "fri"] as never)).toBe("Mon–Fri");
    expect(daysLabel(["sat", "sun"] as never)).toBe("Sat, Sun");
    expect(daysLabel(["fri", "mon", "wed", "thu"] as never)).toBe("Mon, Wed–Fri");
  });
});

describe("tag rules (set_camera_settings.py's)", () => {
  it("tidies spaces and refuses bad, repeated or too many tags", () => {
    expect(normalizeTag("  Gate   1 ")).toBe("Gate 1");
    expect(tagProblem("ok", [])).toBeNull();
    expect(tagProblem("", [])).toBe("Type a tag first.");
    expect(tagProblem("a/b", [])).toMatch(/letters, digits/);
    expect(tagProblem("gate", ["Gate"])).toBe("“gate” is already there.");
    expect(tagProblem("x", Array.from({ length: 10 }, (_, i) => `t${i}`))).toBe("At most 10 tags.");
  });
});

const cam = (over: Partial<Camera> = {}): Camera => ({
  id: "cam-02", mode: "passthrough", hasIrControl: false, recordingMode: "manual", supportsDetection: true,
  audioCapable: false, audioEnabled: false, outageBufferSec: 0, videoCodec: "h264", liveView: "sub",
  detectionModes: ["motion"], tags: [], recordStream: "main", recordSize: "2560x1440", recordFps: 15,
  recordQuality: null, recordQualityOptions: ["main", "sub"], ...over,
});
function mountCard(component: unknown, camera: Camera) {
  const store = useCamerasStore();
  store.cameras = [camera];
  store.loaded = true;
  return mount(component as DefineComponent<{ camera: Camera }>,
    { props: { camera: store.cameras[0] }, global: { plugins: [[OpenVue, { unstyled: true }]] } });
}
const refresh = (w: ReturnType<typeof mountCard>) => w.setProps({ camera: useCamerasStore().cameras[0] });
const ok = { ok: true, status: 200, data: {} };

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  fake.endpoints.setCameraSettings.mockResolvedValue(ok);
});

describe("Tags card", () => {
  it("adds a tidied tag and removes one, saving the whole list each time", async () => {
    const w = mountCard(TagsCard, cam({ tags: ["outdoor"] }));
    await w.find("input").setValue("  Gate   1 ");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(fake.endpoints.setCameraSettings).toHaveBeenLastCalledWith("cam-02", { tags: ["outdoor", "Gate 1"] });
    await refresh(w);
    await w.find('button[aria-label="Remove tag outdoor"]').trigger("click");
    await flushPromises();
    expect(fake.endpoints.setCameraSettings).toHaveBeenLastCalledWith("cam-02", { tags: ["Gate 1"] });
  });

  it("refuses a repeated tag without asking the server; a refused save puts the list back", async () => {
    const w = mountCard(TagsCard, cam({ tags: ["Gate"] }));
    await w.find("input").setValue("gate");
    await w.find("form").trigger("submit");
    expect(w.text()).toContain("“gate” is already there.");
    expect(fake.endpoints.setCameraSettings).not.toHaveBeenCalled();
    fake.endpoints.setCameraSettings.mockResolvedValueOnce({ ok: false, status: 409, data: { error: "unregistered" } });
    await w.find("input").setValue("yard");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(useCamerasStore().cameras[0].tags).toEqual(["Gate"]);
    expect(w.text()).toContain("Not saved: unregistered");
  });
});

describe("Recorded quality card (D5)", () => {
  it("offers the ceiling and the sub stream; choosing sub saves it, the ceiling clears it", async () => {
    const w = mountCard(QualityCard, cam());
    const sel = w.findComponent(Select);
    expect((sel.props("options") as { label: string }[]).map(o => o.label))
      .toEqual(["Main stream · 2560x1440 · 15 fps", "Sub stream (as in Live)"]);
    expect(sel.props("modelValue")).toBe("main");
    sel.vm.$emit("update:modelValue", "sub");
    await flushPromises();
    expect(fake.endpoints.setCameraSettings).toHaveBeenLastCalledWith("cam-02", { recordQuality: { stream: "sub" } });
    expect(w.text()).toContain("Not applied yet");
    await refresh(w);
    w.findComponent(Select).vm.$emit("update:modelValue", "main");
    await flushPromises();
    expect(fake.endpoints.setCameraSettings).toHaveBeenLastCalledWith("cam-02", { recordQuality: null });
  });

  it("offers nothing for the USB camera, a camera on its sub stream, or one whose ceiling isn't known", () => {
    expect(mountCard(QualityCard, cam({ mode: "transcode", supportsDetection: false, recordQualityOptions: [] })).text())
      .toContain("Fixed: 720p at 15 fps, encoded on the Pi.");
    const sub = mountCard(QualityCard, cam({ recordStream: "sub", recordQualityOptions: ["sub"] }));
    expect(sub.findComponent(Select).exists()).toBe(false);
    expect(sub.text()).toContain("the camera already records its sub stream");
    expect(mountCard(QualityCard, cam({ recordStream: null, recordQualityOptions: [] })).text())
      .toContain("hasn't recorded this camera's stream yet");
  });
});

describe("Schedule card (D6)", () => {
  async function addEntry(w: ReturnType<typeof mountCard>, days: string[], at: string, action: string) {
    for (const d of days) await w.findAll("button.day").find(b => b.text() === d)!.trigger("click");
    await w.find('input[type="time"]').setValue(at);
    w.findAllComponents(Select).find(s => s.props("ariaLabel") === "Action")!.vm.$emit("update:modelValue", action);
    await w.vm.$nextTick();
    await w.findAll("button").find(b => b.text() === "Add")!.trigger("click");
    await flushPromises();
    await refresh(w);
  }

  it("adds entries in this browser's time zone, draws the week, and warns while a Start has no Stop", async () => {
    const w = mountCard(ScheduleCard, cam());
    expect(w.text()).toContain("Not run yet");
    await addEntry(w, ["Mon", "Tue"], "18:00", "start");
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(fake.endpoints.setCameraSettings).toHaveBeenLastCalledWith("cam-02",
      { schedule: { tz, entries: [{ days: ["mon", "tue"], at: "18:00", action: "start" }] } });
    expect(w.text()).toContain("A Start has no Stop within 24 hours");
    await addEntry(w, ["Tue", "Wed"], "07:00", "stop");
    expect(w.text()).not.toContain("A Start has no Stop within 24 hours");
    expect(w.text()).toContain("about 26 h a week");
    expect(w.findAll(".bar .on").length).toBe(4);       // two nights, each across midnight
  });

  it("offers only the modes the camera publishes; refuses a clash before asking the server", async () => {
    const w = mountCard(ScheduleCard, cam({ schedule: { tz: "Europe/Berlin", entries: [e(["mon"], "18:00", "start")] } }));
    const actions = w.findAllComponents(Select).find(s => s.props("ariaLabel") === "Action")!;
    expect((actions.props("options") as { value: string }[]).map(o => o.value))
      .toEqual(["start", "stop", "mode:manual", "mode:motion"]);
    expect(w.text()).toContain("times in Europe/Berlin");          // an existing schedule keeps its zone
    await addEntry(w, ["Mon"], "18:00", "stop");
    expect(w.text()).toContain("Another entry already changes the recording on Mon at 18:00.");
    expect(fake.endpoints.setCameraSettings).not.toHaveBeenCalled();
  });

  it("deleting the last entry clears the schedule", async () => {
    const w = mountCard(ScheduleCard, cam({ schedule: { tz: "UTC", entries: [e(["sat"], "09:00", "start")] } }));
    await w.find('button[aria-label^="Delete:"]').trigger("click");
    await flushPromises();
    expect(fake.endpoints.setCameraSettings).toHaveBeenLastCalledWith("cam-02", { schedule: null });
  });
});
