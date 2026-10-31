import { mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import type { Camera } from "../src/api/types";
import { codecLabel, groupCameras, matchesQuery } from "../src/cameras/list";
import CameraList from "../src/components/CameraList.vue";
import { makeRouter } from "../src/router";
import { useCamerasStore } from "../src/stores/cameras";

const cam = (id: string, over: Partial<Camera> = {}): Camera => ({
  id, mode: "passthrough", hasIrControl: false, recordingMode: "manual", supportsDetection: true,
  audioCapable: false, audioEnabled: false, outageBufferSec: 0, videoCodec: "h264", liveView: "recorded",
  ...over,
});

describe("camera list helpers", () => {
  const list = [
    cam("cam-01", { mode: "transcode" }),
    cam("cam-02", { displayName: "Front gate", tags: ["outdoor"] }),
    cam("cam-03", { displayName: "Yard", tags: ["outdoor", "gate"] }),
    cam("cam-04", { tags: ["indoor"] }),
  ];

  it("searches name, ID and tags, every word, case ignored", () => {
    expect(list.filter(c => matchesQuery(c, "front")).map(c => c.id)).toEqual(["cam-02"]);
    expect(list.filter(c => matchesQuery(c, "OUTDOOR gate")).map(c => c.id)).toEqual(["cam-02", "cam-03"]);
    expect(list.filter(c => matchesQuery(c, "cam-0")).length).toBe(4);
    expect(list.filter(c => matchesQuery(c, "  ")).length).toBe(4);
  });

  it("groups by first tag, untagged last, keeping list order", () => {
    expect(groupCameras(list).map(g => [g.tag, g.cameras.map(c => c.id)])).toEqual([
      ["indoor", ["cam-04"]], ["outdoor", ["cam-02", "cam-03"]], [null, ["cam-01"]],
    ]);
  });

  it("labels the codec as the old page did", () => {
    expect(codecLabel(cam("x", { videoCodec: "h265", videoEncode: "camera" }))).toBe("video H.265 (camera encoder)");
    expect(codecLabel(cam("x", { videoEncode: "pi-hw" }))).toBe("video H.264 (adapter hardware encoder)");
  });
});

describe("CameraList", () => {
  beforeEach(() => setActivePinia(createPinia()));

  function mountList(cameras: Camera[], selected?: string) {
    const store = useCamerasStore();
    store.cameras = cameras;
    store.loaded = true;
    return mount(CameraList, { props: { selected }, global: { plugins: [[OpenVue, { unstyled: true }]] } });
  }

  it("shows every camera, marks the selected one and emits a click", async () => {
    const w = mountList([cam("cam-01"), cam("cam-02", { displayName: "Front gate" })], "cam-02");
    const buttons = w.findAll("button.cam");
    expect(buttons.map(b => b.text())).toEqual(["cam-01", "Front gatecam-02"]);
    expect(buttons[1].attributes("aria-current")).toBe("true");
    expect(w.find("h3").exists()).toBe(false);          // no tags: one group, no heading
    await buttons[0].trigger("click");
    expect(w.emitted("select")).toEqual([["cam-01"]]);
  });

  it("filters as the viewer types and says when nothing matches", async () => {
    const w = mountList([cam("cam-01"), cam("cam-02", { displayName: "Front gate" })]);
    await w.find("input").setValue("gate");
    expect(w.findAll("button.cam").map(b => b.text())).toEqual(["Front gatecam-02"]);
    await w.find("input").setValue("nothing");
    expect(w.text()).toContain("No camera matches “nothing”.");
  });

  it("says so when no camera is registered", () => {
    expect(mountList([]).text()).toContain("No cameras registered yet.");
  });
});

describe("router", () => {
  it("sends a signed-out viewer to sign-in, remembering where they were going", async () => {
    const router = makeRouter(() => false);
    await router.push("/settings/cam-01");
    expect(router.currentRoute.value.name).toBe("login");
    expect(router.currentRoute.value.query.next).toBe("/settings/cam-01");
  });

  it("sends a signed-in viewer from sign-in to Live, and unknown paths to Live", async () => {
    const router = makeRouter(() => true);
    await router.push("/login");
    expect(router.currentRoute.value.name).toBe("live");
    await router.push("/no/such/page");
    expect(router.currentRoute.value.name).toBe("live");
  });
});
