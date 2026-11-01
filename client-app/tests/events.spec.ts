// Events (WEB-PANEL-PLAN.md 2.4): the old page's clip list, with the API faked.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import Select from "openvue/select";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Camera, Clip } from "../src/api/types";

const fake = vi.hoisted(() => ({
  endpoints: { listClips: vi.fn(), setClipTier: vi.fn(), deleteClip: vi.fn(), playClip: vi.fn() },
}));
vi.mock("../src/api", () => ({
  endpoints: fake.endpoints,
  session: { signedIn: true, username: "demo", onChange: () => () => {} },
}));

import { clipPageNumbers, formatDuration, targetPage, tierChoice } from "../src/clips/format";
import ClipList from "../src/components/events/ClipList.vue";

const cam = (over: Partial<Camera> = {}): Camera => ({
  id: "cam-02", mode: "passthrough", hasIrControl: false, recordingMode: "manual", supportsDetection: true,
  audioCapable: false, audioEnabled: false, outageBufferSec: 0, videoCodec: "h264", liveView: "sub", ...over,
});
const clip = (n: number, over: Partial<Clip> = {}): Clip => ({
  s3Key: `clips/cam-02/${n}.mp4`, startTs: `2026-10-3${n}T10:00:00Z`, tier: "STANDARD",
  durationSec: 45, labels: ["motion"], videoCodec: "h264", ...over,
});
const pageOf = (clips: Clip[], page = 1, pages = 1, total = clips.length) =>
  ({ ok: true, status: 200, data: { clips, page, pages, total } });

function mountList(camera = cam()) {
  return mount(ClipList, { props: { camera }, attachTo: document.body,
                           global: { plugins: [[OpenVue, { unstyled: true }]] } });
}
const button = (w: ReturnType<typeof mountList>, label: string, row = 0) =>
  w.findAll("li")[row].findAll("button").find(b => b.text().includes(label))!;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  fake.endpoints.listClips.mockResolvedValue(pageOf([clip(1), clip(0, { tier: "GLACIER", durationSec: 7200 })], 1, 3, 25));
  fake.endpoints.setClipTier.mockResolvedValue({ ok: true, status: 200, data: {} });
  fake.endpoints.deleteClip.mockResolvedValue({ ok: true, status: 200, data: {} });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("clip wording and paging, as on the old page", () => {
  it("formats durations from seconds to hours", () => {
    expect([45, 90, 3600, 7260].map(formatDuration)).toEqual(["45 sec.", "2 min.", "1 h", "2 h 1 min."]);
    expect(formatDuration(null)).toBe("duration unknown");
  });

  it("offers every page up to 9, else the ends and two either side, with gaps", () => {
    expect(clipPageNumbers(1, 3)).toEqual([1, 2, 3]);
    expect(clipPageNumbers(10, 20)).toEqual([1, null, 8, 9, 10, 11, 12, null, 20]);
    expect(clipPageNumbers(1, 20)).toEqual([1, 2, 3, null, 20]);
  });

  it("turns a request into the page to ask for", () => {
    expect([targetPage("first", 4), targetPage("older", 4), targetPage("newer", 1), targetPage("last", 4), targetPage(7, 4)])
      .toEqual([1, 5, 1, "last", 7]);
  });

  it("preselects Deep Archive for a Glacier clip", () => {
    expect([tierChoice("GLACIER"), tierChoice("STANDARD_IA"), tierChoice("UNKNOWN")]).toEqual(["DEEP_ARCHIVE", "STANDARD_IA", "STANDARD"]);
  });
});

describe("the clip list", () => {
  it("shows the newest page with tier, duration, codec and labels, and pages through the rest", async () => {
    const w = mountList();
    await flushPromises();
    expect(fake.endpoints.listClips).toHaveBeenCalledWith("cam-02", 10, 1);
    expect(w.text()).toContain("25 clips");
    const rows = w.findAll("li");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("Standard");
    expect(rows[0].text()).toContain("45 sec.");
    expect(rows[0].text()).toContain("H.264");
    expect(rows[0].text()).toContain("motion");
    expect(rows[1].text()).toContain("Glacier");
    expect(rows[1].text()).toContain("2 h");
    expect(w.findAllComponents(Select)[1].props("modelValue")).toBe("DEEP_ARCHIVE");

    await w.findAll("nav button").find(b => b.text() === "Older ›")!.trigger("click");
    expect(fake.endpoints.listClips).toHaveBeenLastCalledWith("cam-02", 10, 2);
  });

  it("applies a tier, then reloads the page being viewed", async () => {
    const w = mountList();
    await flushPromises();
    w.findAllComponents(Select)[0].vm.$emit("update:modelValue", "STANDARD_IA");
    await button(w, "Apply").trigger("click");
    await flushPromises();
    expect(fake.endpoints.setClipTier).toHaveBeenCalledWith("clips/cam-02/1.mp4", "STANDARD_IA");
    expect(fake.endpoints.listClips).toHaveBeenLastCalledWith("cam-02", 10, 1);
  });

  it("deletes only after the viewer confirms", async () => {
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    const w = mountList();
    await flushPromises();
    await button(w, "Delete").trigger("click");
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/^Delete the clip from .*This cannot be undone\.$/s));
    expect(fake.endpoints.deleteClip).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    await button(w, "Delete").trigger("click");
    await flushPromises();
    expect(fake.endpoints.deleteClip).toHaveBeenCalledWith("clips/cam-02/1.mp4", "2026-10-31T10:00:00Z");
  });

  it("explains a clip in Deep Archive instead of playing it", async () => {
    fake.endpoints.playClip.mockResolvedValueOnce({ ok: false, status: 202, data: { status: "restore_requested", eta_hours: 12 } });
    const w = mountList();
    await flushPromises();
    await button(w, "Play", 1).trigger("click");
    await flushPromises();
    expect(fake.endpoints.playClip).toHaveBeenCalledWith("clips/cam-02/0.mp4");
    expect(document.body.textContent).toContain("A restore has been requested — check back in about 12 hours");
  });

  it("plays a ready clip in the dialog", async () => {
    fake.endpoints.playClip.mockResolvedValueOnce({ ok: true, status: 200, data: { status: "ready", url: "https://s3.example/c.mp4" } });
    const w = mountList();
    await flushPromises();
    await button(w, "Play").trigger("click");
    await flushPromises();
    expect(document.querySelector<HTMLVideoElement>(".clip-video")!.src).toBe("https://s3.example/c.mp4");
  });

  it("with a detection mode, notices new clips without reloading the list", async () => {
    vi.useFakeTimers();
    const w = mountList(cam({ recordingMode: "motion" }));
    await vi.advanceTimersByTimeAsync(0);
    fake.endpoints.listClips.mockResolvedValue(pageOf([clip(2)]));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(fake.endpoints.listClips).toHaveBeenLastCalledWith("cam-02", 1, 1);
    expect(w.text()).toContain("New clips are available.");
    expect(w.findAll("li")).toHaveLength(2);           // still the list the viewer was looking at
  });

  it("with manual recording, doesn't poll", async () => {
    vi.useFakeTimers();
    mountList();
    await vi.advanceTimersByTimeAsync(90_000);
    expect(fake.endpoints.listClips).toHaveBeenCalledTimes(1);
  });
});
