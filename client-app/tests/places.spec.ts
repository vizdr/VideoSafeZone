// The camera list asks the directory where a customer's cameras are (DEALER-PANEL-PLAN.md D18, D19,
// Phase 9) and heads each place; staff aren't asked, and their list stays grouped by tag only.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Camera, Me } from "../src/api/types";

const fake = vi.hoisted(() => ({
  session: { signedIn: true, username: "anna@example.test" as string | null, claims: { vsz_role: "customer" } as Record<string, unknown>,
             onChange: () => () => {} },
  endpoints: { me: vi.fn() },
}));
vi.mock("../src/api", () => ({ session: fake.session, endpoints: fake.endpoints }));

import CameraList from "../src/components/CameraList.vue";
import { useCamerasStore } from "../src/stores/cameras";

const cam = (id: string, adapterId: string): Camera => ({
  id, adapterId, mode: "passthrough", hasIrControl: false, recordingMode: "manual", supportsDetection: true,
  audioCapable: false, audioEnabled: false, outageBufferSec: 0, videoCodec: "h264", liveView: "recorded",
});
const ME: Me = {
  email: "anna@example.test", role: "customer", org: null, accounts: [
    { id: "a-1", name: "Acme", role: "customer", dealer: "North", sites: [{ id: "s-1", name: "Home", timezone: "Europe/Berlin" }],
      adapters: [{ thingName: "adapter-01", name: "Hall", siteId: "s-1" }] },
    { id: "a-2", name: "Beta", role: "customer_user", dealer: "North", sites: [{ id: "s-2", name: "Office", timezone: "Europe/Berlin" }],
      adapters: [{ thingName: "adapter-09", name: null, siteId: "s-2" }] },
  ],
};

async function mountList(role: string) {
  fake.session.claims = { vsz_role: role };
  const store = useCamerasStore();
  store.cameras = [cam("cam-03", "adapter-01"), cam("cam-09", "adapter-09")];
  store.loaded = true;
  const w = mount(CameraList, { global: { plugins: [[OpenVue, { unstyled: true }]] } });
  await flushPromises();
  return w;
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
});

describe("the camera list's places", () => {
  it("a customer's list asks the directory once and heads each place", async () => {
    fake.endpoints.me.mockResolvedValue({ ok: true, status: 200, data: ME });
    const w = await mountList("customer");
    expect(fake.endpoints.me).toHaveBeenCalledTimes(1);
    expect(w.findAll("[data-test=place]").map(h => h.text())).toEqual(["Acme · Home · Hall", "Beta · Office"]);
  });

  it("staff aren't asked, and a directory that can't answer leaves the list as it was", async () => {
    await mountList("system_admin");
    expect(fake.endpoints.me).not.toHaveBeenCalled();
    setActivePinia(createPinia());
    fake.endpoints.me.mockResolvedValue({ ok: false, status: 502, data: { error: "the directory failed" } });
    const w = await mountList("customer");
    expect(w.findAll("[data-test=place]")).toHaveLength(0);
    expect(w.findAll("button.cam")).toHaveLength(2);
  });
});
