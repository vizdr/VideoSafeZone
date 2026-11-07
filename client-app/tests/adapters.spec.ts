// Adapters' activation on the page (DEALER-PANEL-PLAN.md Phase 6, D15): the Cloud ID field, Enter
// Cloud ID for a customer and for a dealer's customer, the account's own adapters, and the System
// console's new adapters, codes shown once, release, revoke, allocation and sync. The server's side:
// tests_db/test_adapters.py and tests/cloud/test_directory_api.py.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import type { AdapterSummary, CustomerAdapters, CustomerDetail, Role } from "../src/api/types";

const fake = vi.hoisted(() => {
  const fn = () => vi.fn();
  return {
    session: { signedIn: true, username: "holder@example.test" as string | null,
               claims: { vsz_role: "customer" } as Record<string, unknown>, onChange: () => () => {}, refresh: vi.fn() },
    endpoints: {
      me: fn(), listAdapters: fn(), listDealers: fn(), generateAdapters: fn(), activateAdapter: fn(), allocateAdapter: fn(),
      adapterAction: fn(), listCameras: fn(), getPricing: fn(), putPlan: fn(), cancelPlan: fn(),
    },
  };
});
vi.mock("../src/api", () => ({ session: fake.session, endpoints: fake.endpoints }));

import ActivateAdapter from "../src/components/adapters/ActivateAdapter.vue";
import { CUSTOMER, type CustomerContext } from "../src/dealer/customer";
import { formatCloudId, readCloudId } from "../src/dealer/cloudid";
import { makeRouter } from "../src/router";
import AddAdapterView from "../src/views/AddAdapterView.vue";
import AdaptersSettings from "../src/views/AdaptersSettings.vue";
import CustomerPlan from "../src/views/dealer/CustomerPlan.vue";
import SystemAdapters from "../src/views/system/SystemAdapters.vue";

const ok = <T>(data: T) => Promise.resolve({ ok: true, status: 200, data });
const refused = (status: number, error: string) => Promise.resolve({ ok: false, status, data: { error } });
const e = fake.endpoints;

const HOME = { id: "s-1", name: "Home", timezone: "Europe/Berlin" };
const adapter = (over: Partial<AdapterSummary> = {}): AdapterSummary => ({
  id: "a-1", thingName: "adapter-01", status: "activated", label: null, lastSeenAt: "2026-11-07T20:00:00Z", dealer: { id: "d-1", name: "North" },
  activation: { name: "Hall", activatedAt: "2026-11-01T10:00:00Z", customer: { id: "c-1", name: "Acme" }, site: "Home", siteId: "s-1" },
  connected: true, hasCode: false, identity: true, ...over,
});
const ACTIVATION = { adapterId: "a-2", thingName: "adapter-02", customerId: "c-1", siteId: "s-9", activationId: "act-2" };

async function routed(path: string, role: Role) {
  fake.session.claims = { vsz_role: role };
  const router = makeRouter(() => true, () => role);
  await router.push(path);
  return router;
}
const plugins = (router: ReturnType<typeof makeRouter>) => [router, [OpenVue, { unstyled: true }]] as never;
const button = (w: { findAll: (s: string) => Array<{ text: () => string }> }, label: string) =>
  w.findAll("button").find(b => b.text() === label) as unknown as { trigger: (e: string) => Promise<void>; attributes: (a: string) => string | undefined };

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  document.body.innerHTML = "";
  fake.session.refresh.mockResolvedValue(undefined);
  e.listCameras.mockReturnValue(ok({ cameras: [] }));
});

// --- the Cloud ID field ----------------------------------------------------------------------------

describe("the Cloud ID", () => {
  it.each([["k7qm4xrpz9", "K7QM4-XRPZ9"], ["K7Q", "K7Q"], ["k7qm4-xrpz9-extra", "K7QM4-XRPZ9"], ["k7 qm4/xr", "K7QM4-XR"]])(
    "%s is shown as %s", (typed, shown) => expect(formatCloudId(typed)).toBe(shown));

  it("is read as the server reads it: O is 0, I and L are 1, U is never one", () => {
    expect(readCloudId("O1LI2-ABCDE")).toBe("01112ABCDE");
    expect(readCloudId("K7QM4-XRPZU")).toBeNull();
    expect(readCloudId("K7QM4-XRPZ")).toBeNull();
  });
});

// --- Enter Cloud ID --------------------------------------------------------------------------------

describe("Enter Cloud ID", () => {
  async function form(props: Record<string, unknown>) {
    const router = await routed("/live", "customer");
    const w = mount(ActivateAdapter, { props: { sites: [HOME], ...props }, global: { plugins: plugins(router) }, attachTo: document.body });
    return w;
  }

  it("formats the code as it is typed and goes on only with ten characters", async () => {
    const w = await form({ accountId: "c-1" });
    await w.find("[data-test=cloud-id]").setValue("k7qm4xrpz");
    expect((w.find("[data-test=cloud-id]").element as HTMLInputElement).value).toBe("K7QM4-XRPZ");
    expect(button(w, "Continue").attributes("disabled")).toBeDefined();
    await w.find("[data-test=cloud-id]").setValue("k7qm4xrpz9");
    await button(w, "Continue").trigger("click");
    expect(w.find("[data-test=code-shown]").text()).toBe("K7QM4-XRPZ9");
  });

  it("activates at one of the account's locations, or a new one, with a name", async () => {
    e.activateAdapter.mockReturnValue(ok(ACTIVATION));
    const w = await form({ accountId: "c-1" });
    await w.find("[data-test=cloud-id]").setValue("K7QM4XRPZ9");
    await button(w, "Continue").trigger("click");
    await w.find("[data-test=new-site]").setValue(true);
    await w.find("[data-test=site-name]").setValue(" Garage ");
    await w.find("[data-test=site-zone]").setValue("Europe/Vienna");
    await w.find("[data-test=adapter-name]").setValue("Door");
    await w.find("[data-test=activate]").trigger("click");
    await flushPromises();
    expect(e.activateAdapter).toHaveBeenCalledWith({ cloudId: "K7QM4-XRPZ9", accountId: "c-1",
      newSite: { name: "Garage", timezone: "Europe/Vienna" }, name: "Door" });
    expect(w.emitted("activated")).toEqual([[ACTIVATION]]);
  });

  it("for a dealer's customer it names the customer and an existing location", async () => {
    e.activateAdapter.mockReturnValue(ok(ACTIVATION));
    const w = await form({ customerId: "c-1" });
    await w.find("[data-test=cloud-id]").setValue("K7QM4XRPZ9");
    await button(w, "Continue").trigger("click");
    await w.find("[data-test=activate]").trigger("click");
    await flushPromises();
    expect(e.activateAdapter).toHaveBeenCalledWith({ cloudId: "K7QM4-XRPZ9", customerId: "c-1", siteId: "s-1", name: undefined });
  });

  it("a code that can't be activated goes back to the code, and too many tries say so", async () => {
    e.activateAdapter.mockReturnValueOnce(refused(400, "this Cloud ID can't be activated"))
      .mockReturnValueOnce(refused(429, "too many wrong Cloud IDs: try again in an hour"));
    const w = await form({ accountId: "c-1" });
    await w.find("[data-test=cloud-id]").setValue("K7QM4XRPZ9");
    await button(w, "Continue").trigger("click");
    await w.find("[data-test=activate]").trigger("click");
    await flushPromises();
    expect(w.find("[data-test=activate-error]").text()).toBe("this Cloud ID can't be activated");
    expect(w.find("[data-test=cloud-id]").exists()).toBe(true);
    await button(w, "Continue").trigger("click");
    await w.find("[data-test=activate]").trigger("click");
    await flushPromises();
    expect(w.find("[data-test=activate-error]").text()).toContain("try again in an hour");
    expect(w.find("[data-test=code-shown]").exists()).toBe(true);                  // not the code's fault
  });
});

// --- the customer's pages --------------------------------------------------------------------------

describe("Add adapter", () => {
  const me = (accounts: unknown[]) => ok({ email: "holder@example.test", role: "customer", org: null, accounts });

  it("activates for the account, renews the token and loads the cameras", async () => {
    e.me.mockReturnValue(me([{ id: "c-1", name: "Acme", role: "customer", dealer: "North", sites: [HOME] }]));
    e.activateAdapter.mockReturnValue(ok(ACTIVATION));
    const router = await routed("/adapters/add", "customer");
    expect(router.currentRoute.value.name).toBe("add-adapter");
    const w = mount(AddAdapterView, { global: { plugins: plugins(router) }, attachTo: document.body });
    await flushPromises();
    expect(w.find("[data-test=account]").exists()).toBe(false);                     // one account: no question
    await w.find("[data-test=cloud-id]").setValue("K7QM4XRPZ9");
    await button(w, "Continue").trigger("click");
    await w.find("[data-test=activate]").trigger("click");
    await flushPromises();
    expect(e.activateAdapter.mock.calls[0][0]).toMatchObject({ accountId: "c-1", siteId: "s-1" });
    expect(fake.session.refresh).toHaveBeenCalled();
    expect(e.listCameras).toHaveBeenCalled();
    expect(w.find("[data-test=activated]").text()).toBe("adapter-02 is activated. It starts on a trial plan until your dealer sets one.");
  });

  it("a user with shared cameras is told who adds adapters", async () => {
    e.me.mockReturnValue(me([{ id: "c-1", name: "Acme", role: "customer_user", dealer: "North", sites: [HOME] }]));
    const router = await routed("/adapters/add", "customer");
    const w = mount(AddAdapterView, { global: { plugins: plugins(router) } });
    await flushPromises();
    expect(w.find("[data-test=not-yours]").text()).toContain("holder or a Super Admin");
  });

  it("is a customer's page only", async () => {
    expect((await routed("/adapters/add", "dealer")).currentRoute.value.name).toBe("dealer-customers");
    expect((await routed("/settings/adapters", "system_admin")).currentRoute.value.name).toBe("live");
  });
});

describe("Settings → Cloud adapters", () => {
  it("lists the account's adapters and removes one after asking", async () => {
    e.listAdapters.mockReturnValue(ok([adapter(), adapter({ id: "a-2", thingName: "adapter-02", connected: false,
      activation: { ...adapter().activation!, name: null, site: "Garage" } })]));
    e.adapterAction.mockReturnValue(ok({ id: "a-2", thingName: "adapter-02" }));
    const router = await routed("/settings/adapters", "customer");
    const w = mount(AdaptersSettings, { global: { plugins: plugins(router) }, attachTo: document.body });
    await flushPromises();
    const second = w.find("[data-test=adapter-adapter-02]");
    expect(w.find("[data-test=adapter-adapter-01]").text()).toContain("Hall · adapter-01");
    expect(second.text()).toContain("last seen");
    await second.findAll("button")[0].trigger("click");
    expect(e.adapterAction).not.toHaveBeenCalled();
    await w.find("[data-test=confirm-remove]").trigger("click");
    await flushPromises();
    expect(e.adapterAction).toHaveBeenCalledWith("a-2", "remove");
    expect(fake.session.refresh).toHaveBeenCalled();
    expect(w.text()).toContain("adapter-02 is removed from your account.");
    expect(w.find("[data-test=add-adapter]").attributes("href")).toBe("#/adapters/add");
  });
});

// --- the dealer activates for a customer -----------------------------------------------------------

describe("the customer's Plan page", () => {
  it("activates an adapter for the customer and opens its plan", async () => {
    e.getPricing.mockReturnValue(ok({ prices: [], settings: { retention_days: [7, 30, 90], annual_discount_percent: 10,
      trial_days: 30, currency: "EUR", placeholder_prices: true } }));
    e.activateAdapter.mockReturnValue(ok(ACTIVATION));
    const detail = { id: "c-1", name: "Acme", sites: [HOME] } as unknown as CustomerDetail;
    const adapters = ref<CustomerAdapters | null>({ customer: { id: "c-1", name: "Acme" }, adapters: [],
      total: { adapters: 0, centsMonth: 0, cameras: 0, currency: "EUR" } });
    const ctx: CustomerContext = { customerId: ref("c-1"), detail: ref(detail), adapters, error: ref(""), reload: vi.fn(async () => {}) };
    const router = await routed("/dealer/customers/c-1/plan", "dealer");
    const w = mount(CustomerPlan, { global: { plugins: plugins(router), provide: { [CUSTOMER as symbol]: ctx } }, attachTo: document.body });
    await flushPromises();
    await w.find("[data-test=start-activate]").trigger("click");
    await w.find("[data-test=cloud-id]").setValue("K7QM4XRPZ9");
    await button(w, "Continue").trigger("click");
    await w.find("[data-test=activate]").trigger("click");
    await flushPromises();
    expect(e.activateAdapter).toHaveBeenCalledWith({ cloudId: "K7QM4-XRPZ9", customerId: "c-1", siteId: "s-1", name: undefined });
    expect(ctx.reload).toHaveBeenCalled();
    expect(router.currentRoute.value.params.adapterId).toBe("a-2");
  });
});

// --- the System console ----------------------------------------------------------------------------

describe("System → Adapters", () => {
  async function page(role: Role = "system_owner", list: AdapterSummary[] = [adapter()]) {
    e.listAdapters.mockReturnValue(ok(list));
    e.listDealers.mockReturnValue(ok([{ id: "d-1", name: "North" }, { id: "d-0", name: "Direct" }]));
    const router = await routed(role === "dealer" ? "/dealer/adapters" : "/system/adapters", role);
    const w = mount(SystemAdapters, { global: { plugins: plugins(router) }, attachTo: document.body });
    await flushPromises();
    return w;
  }
  const row = (w: Awaited<ReturnType<typeof page>>, thing: string) => w.find(`[data-test=adapter-${thing}]`);

  it("makes new adapters and shows their Cloud IDs, the labels and the owner's script once", async () => {
    e.generateAdapters.mockReturnValue(ok({ adapters: [{ id: "a-2", thingName: "adapter-02", cloudId: "K7QM4-XRPZ9" }],
      identityCommand: "bash cloud/adapter-identity.sh create adapter-02" }));
    const w = await page();
    await w.find("[data-test=count]").setValue("1");
    await w.find("[data-test=label]").setValue(" Batch 1 ");
    await w.find("[data-test=stock]").setValue("d-1");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(e.generateAdapters).toHaveBeenCalledWith(1, "Batch 1", "d-1");
    const made = w.find("[data-test=made]");
    expect(made.text()).toContain("K7QM4-XRPZ9");
    expect(made.text()).toContain("Shown this once");
    expect(w.find("[data-test=identity-command]").text()).toBe("bash cloud/adapter-identity.sh create adapter-02");
    expect(w.find(".print-labels").text()).toContain("K7QM4-XRPZ9");
    expect(e.listAdapters).toHaveBeenCalledTimes(2);
    await button(w, "Done").trigger("click");
    expect(w.find("[data-test=made]").exists()).toBe(false);                       // gone for good
  });

  it("shows each adapter's state and the actions it allows", async () => {
    const w = await page("system_owner", [adapter(), adapter({ id: "a-2", thingName: "adapter-02", status: "provisioned",
      activation: null, connected: null, lastSeenAt: null, hasCode: true, identity: false, dealer: null })]);
    const actions = (thing: string) => row(w, thing).findAll("button").map(b => b.text());
    expect(actions("adapter-01")).toEqual(["Release", "Revoke", "Sync"]);
    expect(actions("adapter-02")).toEqual(["Allocate", "New code", "Revoke", "Sync"]);
    expect(row(w, "adapter-01").text()).toContain("online");
    expect(row(w, "adapter-02").text()).toContain("not seen yet");
    expect(row(w, "adapter-02").text()).toContain("code waiting · no identity yet");
  });

  it("a release, after asking, shows the new code once and what happened to the cameras", async () => {
    e.adapterAction.mockReturnValue(ok({ id: "a-1", thingName: "adapter-01", cloudId: "NEWCO-DE123", camerasReleased: 4 }));
    const w = await page();
    await button(w, "Release").trigger("click");
    expect(e.adapterAction).not.toHaveBeenCalled();
    await w.find("[data-test=confirm-release]").trigger("click");
    await flushPromises();
    expect(e.adapterAction).toHaveBeenCalledWith("a-1", "release");
    expect(w.find("[data-test=new-code]").text()).toBe("NEWCO-DE123");
    expect(w.find("[data-test=outcome]").text()).toContain("4 cameras taken out of its customer's view.");
  });

  it("a revoke points at the owner's script for the certificate", async () => {
    e.adapterAction.mockReturnValue(ok({ id: "a-1", thingName: "adapter-01", wasActivated: true,
      identityCommand: "bash cloud/adapter-identity.sh revoke adapter-01", configUnsent: "the adapter wasn't told (x): sync it" }));
    const w = await page();
    await button(w, "Revoke").trigger("click");
    await w.find("[data-test=confirm-revoke]").trigger("click");
    await flushPromises();
    expect(w.find("[data-test=outcome-command]").text()).toBe("bash cloud/adapter-identity.sh revoke adapter-01");
    expect(w.find("[data-test=outcome]").text()).toContain("sync it");
  });

  it("allocates to a dealer's stock, or back to nobody's", async () => {
    e.allocateAdapter.mockReturnValue(ok({ id: "a-2", thingName: "adapter-02", dealerId: "d-0" }));
    const w = await page("system_admin", [adapter({ id: "a-2", thingName: "adapter-02", status: "released", activation: null, dealer: null })]);
    await button(w, "Allocate").trigger("click");
    await w.find("[data-test=allocate-to]").setValue("d-0");
    await w.find("[data-test=allocate]").trigger("click");
    await flushPromises();
    expect(e.allocateAdapter).toHaveBeenCalledWith("a-2", "d-0");
    expect(w.find("[data-test=outcome]").text()).toContain("adapter-02 is in Direct's stock");
  });

  it("is read-only for a dealer", async () => {
    const w = await page("dealer");
    expect(w.find("[data-test=count]").exists()).toBe(false);
    expect(row(w, "adapter-01").findAll("button")).toHaveLength(0);
    expect(w.text()).toContain("the customer's Plan page");
    expect(e.listDealers).not.toHaveBeenCalled();
  });
});
