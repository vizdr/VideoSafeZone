// The dealer portal (DEALER-PANEL-PLAN.md Phase 5, §7.1, D19) against a faked directory API: where
// it opens, the customer list and New customer, the plan editor's live price and what it sends,
// plan history's changes, the cameras by adapter, a customer's people, details, billing and the
// dealer's own details. The server's side is tests/cloud/test_directory_api.py and tests_db/test_portal.py.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref, type Component } from "vue";
import { viewDepthKey } from "vue-router";
import type { CustomerAdapters, CustomerDetail, CustomerSummary, Plan, Price, Pricing, Role } from "../src/api/types";

const fake = vi.hoisted(() => {
  const fn = () => vi.fn();
  return {
    session: { signedIn: true, username: "dealer@example.test" as string | null,
               claims: { vsz_role: "dealer" } as Record<string, unknown>, onChange: () => () => {} },
    endpoints: {
      me: fn(), listDealers: fn(), listCustomers: fn(), createCustomer: fn(), getCustomer: fn(), updateCustomer: fn(),
      customerAdapters: fn(), customerUsers: fn(), putPlan: fn(), cancelPlan: fn(), planHistory: fn(), getPricing: fn(),
      inviteUser: fn(), userAction: fn(), getDealer: fn(), updateOwnDealer: fn(), listAdapters: fn(),
    },
  };
});
vi.mock("../src/api", () => ({ session: fake.session, endpoints: fake.endpoints }));

import { CUSTOMER, type CustomerContext } from "../src/dealer/customer";
import { camerasText, linesOf, planChanges } from "../src/dealer/plans";
import { money } from "../src/pricing";
import { makeRouter } from "../src/router";
import CustomerList from "../src/components/dealer/CustomerList.vue";
import CustomerCameras from "../src/views/dealer/CustomerCameras.vue";
import CustomerDetails from "../src/views/dealer/CustomerDetails.vue";
import CustomerHistory from "../src/views/dealer/CustomerHistory.vue";
import CustomerPlan from "../src/views/dealer/CustomerPlan.vue";
import CustomerUsers from "../src/views/dealer/CustomerUsers.vue";
import CustomerView from "../src/views/dealer/CustomerView.vue";
import DealerBilling from "../src/views/dealer/DealerBilling.vue";
import DealerDetails from "../src/views/dealer/DealerDetails.vue";
import PortalView from "../src/views/dealer/PortalView.vue";

const ok = <T>(data: T) => Promise.resolve({ ok: true, status: 200, data });
const refused = (status: number, error: string) => Promise.resolve({ ok: false, status, data: { error } });
const e = fake.endpoints;
const EUR = (cents: number) => money(cents, "EUR");

// The seed's placeholder price list (001_directory.sql): base by resolution, more for 24/7 and longer storage.
const PRICES: Price[] = (["2mp", "4mp", "8mp"] as const).flatMap((r, i) => (["motion", "continuous"] as const).flatMap(c =>
  ([[7, 100], [30, 160], [90, 250]] as const).map(([d, pct]) =>
    ({ resolution: r, recording: c, retentionDays: d, centsMonth: ([300, 350, 500][i] + (c === "continuous" ? 200 : 0)) * pct / 100 }))));
const PRICING: Pricing = { prices: PRICES, settings: { retention_days: [7, 30, 90], annual_discount_percent: 10, trial_days: 30,
                                                     currency: "EUR", placeholder_prices: true } };

const plan = (over: Partial<Plan> = {}): Plan => ({
  id: "p-2", version: 2, status: "trial", effectiveStatus: "trial", retentionDays: 30, interval: "monthly",
  centsPeriod: 2240, centsMonth: 2240, currency: "EUR", trialEndsAt: "2026-12-01T10:00:00Z", createdAt: "2026-11-07T10:00:00Z",
  createdBy: "dealer@example.test", lines: linesOf({ lines: [{ resolution: "4mp", recording: "motion", quantity: 4 }] }), cameras: 4,
  ...over,
});
const ADAPTERS: CustomerAdapters = {
  customer: { id: "c-1", name: "Acme" },
  total: { adapters: 2, centsMonth: 2240, cameras: 4, currency: "EUR" },
  adapters: [
    { id: "a-1", activationId: "act-1", thingName: "adapter-01", label: null, name: "Hall", lastSeenAt: null,
      activatedAt: "2026-11-01T10:00:00Z", site: { id: "s-1", name: "Home", timezone: "Europe/Berlin" }, plan: plan(),
      cameras: [
        { id: "cam-01", name: null, codec: "h264", size: null, class: null, recordingMode: "manual", recording: "continuous", thumbnailAt: null },
        { id: "cam-02", name: "Gate", codec: "h265", size: "2560x1440", class: "4mp", recordingMode: "motion", recording: "motion",
          thumbnailAt: "2026-11-07T09:30:00Z" },
        { id: "cam-03", name: null, codec: "h264", size: "2560x1440", class: "4mp", recordingMode: "human", recording: "motion", thumbnailAt: null },
      ],
      actual: { total: 3, byClass: { "4mp/motion": 2 }, unclassified: 1 } },
    { id: "a-2", activationId: "act-2", thingName: "adapter-0302", label: null, name: null, lastSeenAt: null,
      activatedAt: "2026-11-02T10:00:00Z", site: { id: "s-2", name: "Garage", timezone: "Europe/Berlin" }, plan: null,
      cameras: [], actual: { total: 0, byClass: {}, unclassified: 0 } },
  ],
};
const DETAIL: CustomerDetail = {
  id: "c-1", name: "Acme", reference: "A-7", email: null, phone: null, status: "active", createdAt: "2026-11-01T10:00:00Z",
  dealer: { id: "d-1", name: "North" }, sites: [{ id: "s-1", name: "Home", timezone: "Europe/Berlin" }],
  holder: { id: "u-1", email: "boss@acme.example", name: null, status: "active" }, users: 2, adapters: 2,
};
const ACME: CustomerSummary = {
  id: "c-1", name: "Acme", reference: "A-7", status: "active", createdAt: "2026-11-01T10:00:00Z", dealer: { id: "d-1", name: "North" },
  adapters: 2, users: 2, holder: null, things: ["adapter-01", "adapter-0302"], registered: 3, inPlans: 4, centsMonth: 2240, currency: "EUR",
};
const BETA: CustomerSummary = { ...ACME, id: "c-2", name: "Beta", reference: null, status: "suspended", adapters: 1,
  holder: { email: "ann@beta.example", status: "invited" }, registered: null, inPlans: 2, centsMonth: 1000, things: ["adapter-09"] };

function signIn(role: Role) {
  fake.session.claims = { vsz_role: role };
}

async function routed(path: string, role: Role = "dealer") {
  signIn(role);
  const router = makeRouter(() => true, () => role);
  await router.push(path);
  return router;
}

const plugins = (router: ReturnType<typeof makeRouter>) => [router, [OpenVue, { unstyled: true }]] as never;

function context(adapters: CustomerAdapters | null = ADAPTERS, detail: CustomerDetail | null = DETAIL): CustomerContext {
  return { customerId: ref("c-1"), detail: ref(detail), adapters: ref(adapters), error: ref(""), reload: vi.fn(async () => {}) };
}

/** A customer's page with the context CustomerView would give it. */
async function page(view: Component, path: string, ctx = context(), role: Role = "dealer") {
  const router = await routed(path, role);
  const w = mount(view, { global: { plugins: plugins(router), provide: { [CUSTOMER as symbol]: ctx } }, attachTo: document.body });
  await flushPromises();
  return { w, router, ctx };
}
const byText = (w: { findAll: (s: string) => Array<{ text: () => string }> }, label: string) =>
  w.findAll("button").find(b => b.text() === label) as unknown as { trigger: (e: string) => Promise<void>; attributes: (a: string) => string | undefined };

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  document.body.innerHTML = "";
  e.getPricing.mockReturnValue(ok(PRICING));
});

// --- where the portal opens ----------------------------------------------------------------------

describe("the portal's routes", () => {
  it("opens on the customer list, and a customer on its plan", async () => {
    const router = await routed("/dealer");
    expect(router.currentRoute.value.name).toBe("dealer-customers");
    await router.push("/dealer/customers/c-1");
    expect(router.currentRoute.value.name).toBe("dealer-plan");
    expect(router.currentRoute.value.params.customerId).toBe("c-1");
  });

  it("is closed to customers, and the dealer's own details to everyone but dealers", async () => {
    expect((await routed("/dealer/customers/c-1/plan", "customer")).currentRoute.value.name).toBe("live");
    expect((await routed("/dealer/details", "system_admin")).currentRoute.value.name).toBe("live");
    expect((await routed("/dealer/customers/c-1/cameras", "system_owner")).currentRoute.value.name).toBe("dealer-cameras");
  });

  it.each([["dealer", ["Customers", "Cloud adapters", "Billing", "Pricing", "Dealer details"]],
           ["system_owner", ["Customers", "Cloud adapters", "Billing", "Pricing"]]] as const)(
    "%s's tabs: %j, Customers marked on a customer's pages", async (role, tabs) => {
      const router = await routed("/dealer/customers/c-1/users", role);
      const w = mount(PortalView, { global: { plugins: plugins(router), stubs: { RouterView: true } } });
      expect(w.findAll(".console-tabs a").map(a => a.text())).toEqual(tabs);
      expect(w.find(".console-tabs a.router-link-active").text()).toBe("Customers");
    });
});

// --- the customer list ---------------------------------------------------------------------------

describe("the customer list", () => {
  async function list(role: Role = "dealer") {
    e.listCustomers.mockReturnValue(ok([ACME, BETA]));
    e.listDealers.mockReturnValue(ok([{ id: "d-0", name: "Direct" }, { id: "d-1", name: "North" }]));
    const router = await routed("/dealer/customers", role);
    const w = mount(CustomerList, { global: { plugins: plugins(router) }, attachTo: document.body });
    await flushPromises();
    return w;
  }

  it("shows each customer's holder, adapters, cameras in plans / registered and monthly total", async () => {
    const w = await list();
    expect(w.findAll("thead th").map(t => t.text())).not.toContain("Dealer");     // a dealer's own
    expect(e.listDealers).not.toHaveBeenCalled();
    const acme = w.find("[data-test=customer-Acme]");
    expect(acme.text()).toContain("none yet");
    expect(acme.findAll("td").map(t => t.text()).slice(2, 5)).toEqual(["2", "4 / 3", EUR(2240)]);
    const beta = w.find("[data-test=customer-Beta]");
    expect(beta.text()).toContain("2 / —");                                         // the registry unreadable: "—"
    expect(beta.text()).toContain("ann@beta.example");
    expect(beta.text()).toContain("invited");
    expect(acme.find("a").attributes("href")).toBe("#/dealer/customers/c-1/plan");
  });

  it("searches name, reference and holder, and filters by status", async () => {
    const w = await list();
    await w.find("[data-test=search]").setValue("ann@");
    expect(w.findAll("tbody tr[data-test]").map(r => r.attributes("data-test"))).toEqual(["customer-Beta"]);
    await w.find("[data-test=search]").setValue("");
    await w.find("[data-test=status-filter]").setValue("active");
    expect(w.findAll("tbody tr[data-test]").map(r => r.attributes("data-test"))).toEqual(["customer-Acme"]);
  });

  it("makes a new customer with its first site, and invites its holder", async () => {
    e.createCustomer.mockReturnValue(ok({ id: "c-3", siteId: "s-3", holder: { userId: "u-3", invited: "jo@smith.example" } }));
    const w = await list();
    await byText(w, "New customer").trigger("click");
    await w.find("[data-test=new-name]").setValue(" Smith Ltd ");
    await w.find("[data-test=new-reference]").setValue("S-1");
    await w.find("[data-test=new-site]").setValue("Shop");
    await w.find("[data-test=new-timezone]").setValue("Europe/Vienna");
    await w.find("[data-test=new-holder]").setValue("jo@smith.example");
    await w.find("[data-test=new-customer]").trigger("submit");
    await flushPromises();
    expect(e.createCustomer).toHaveBeenCalledWith({ name: "Smith Ltd", reference: "S-1", email: undefined, phone: undefined,
      site: { name: "Shop", timezone: "Europe/Vienna" }, holder: { email: "jo@smith.example", name: undefined } });
    expect(w.text()).toContain("Customer Smith Ltd created. Invitation sent to jo@smith.example.");
    expect(e.listCustomers).toHaveBeenCalledTimes(2);
  });

  it("says when the customer was made but its holder wasn't invited", async () => {
    e.createCustomer.mockReturnValue(ok({ id: "c-3", siteId: "s-3", holderError: "can't add this address" }));
    const w = await list();
    await byText(w, "New customer").trigger("click");
    await w.find("[data-test=new-name]").setValue("Smith");
    await w.find("[data-test=new-holder]").setValue("taken@example.test");
    await w.find("[data-test=new-customer]").trigger("submit");
    await flushPromises();
    expect(w.find("[data-test=warning]").text()).toContain("The holder wasn't invited: can't add this address");
  });

  it("owners choose the dealer, Direct first offered, and see every dealer's customers", async () => {
    e.createCustomer.mockReturnValue(ok({ id: "c-3", siteId: "s-3" }));
    const w = await list("system_owner");
    expect(w.findAll("thead th").map(t => t.text())).toContain("Dealer");
    await byText(w, "New customer").trigger("click");
    expect((w.find("[data-test=new-dealer]").element as HTMLSelectElement).value).toBe("d-0");
    await w.find("[data-test=new-name]").setValue("Walk-in");
    await w.find("[data-test=new-customer]").trigger("submit");
    await flushPromises();
    expect(e.createCustomer.mock.calls[0][0]).toMatchObject({ name: "Walk-in", dealerId: "d-0" });
  });
});

// --- one customer's frame --------------------------------------------------------------------------

describe("a customer's pages", () => {
  it("load the customer and its adapters once and show whose they are", async () => {
    e.getCustomer.mockReturnValue(ok(DETAIL));
    e.customerAdapters.mockReturnValue(ok(ADAPTERS));
    const router = await routed("/dealer/customers/c-1/cameras", "system_admin");
    const w = mount(CustomerView, { global: { plugins: plugins(router), provide: { [viewDepthKey as symbol]: 3 } } });
    await flushPromises();
    expect(w.find("h1").text()).toBe("Acme — A-7");
    expect(w.text()).toContain("dealer: North");
    expect(w.find(".side-menu a.active").text()).toBe("Cameras");
    expect(w.find("[data-test=adapter-adapter-01]").exists()).toBe(true);        // the child page, given the context
    expect(e.customerAdapters).toHaveBeenCalledTimes(1);
  });

  it("show the API's refusal", async () => {
    e.getCustomer.mockReturnValue(refused(404, "no such customer"));
    e.customerAdapters.mockReturnValue(refused(404, "no such customer"));
    const router = await routed("/dealer/customers/c-9/plan");
    const w = mount(CustomerView, { global: { plugins: plugins(router), provide: { [viewDepthKey as symbol]: 3 } } });
    await flushPromises();
    expect(w.find("[data-test=customer-error]").text()).toBe("no such customer");
  });
});

// --- the plan editor -------------------------------------------------------------------------------

describe("the plan", () => {
  const qty = (w: Awaited<ReturnType<typeof page>>["w"], r: string, c: string) => w.find(`[data-test=qty-${r}-${c}]`);

  it("shows the adapter's plan, its status and price, plan vs. actual and the customer's total", async () => {
    const { w } = await page(CustomerPlan, "/dealer/customers/c-1/plan");
    expect(w.findAll("[data-test=adapter] option").map(o => o.text())).toEqual(["Home · Hall — adapter-01", "Garage — adapter-0302"]);
    expect(w.find("[data-test=plan-status]").text()).toBe(
      `Trial until ${new Date("2026-12-01T10:00:00Z").toLocaleDateString()} · version 2 · ${EUR(2240)} a month`);
    expect((qty(w, "4mp", "motion").element as HTMLInputElement).value).toBe("4");
    expect((w.find("[data-test=days-30]").element as HTMLInputElement).checked).toBe(true);
    expect(w.find("[data-test=price]").text()).toBe(EUR(2240));
    expect(w.find("[data-test=actual]").text()).toBe("4 in plan, 3 on this adapter (1 of unknown picture size) — plans aren't enforced.");
    expect(w.find("[data-test=total]").text()).toBe(`Customer total: 2 adapters, ${EUR(2240)} a month`);
    expect(w.text()).toContain("2 on the adapter");                                   // 4MP motion, from the registry
    expect(byText(w, "Save plan").attributes("disabled")).toBeDefined();             // nothing changed yet
  });

  it("prices the plan as it is edited", async () => {
    const { w } = await page(CustomerPlan, "/dealer/customers/c-1/plan");
    await qty(w, "8mp", "continuous").setValue("1");                                  // + 1120 at 30 days
    expect(w.find("[data-test=price]").text()).toBe(EUR(3360));
    await w.find("[data-test=days-90]").setValue(true);                              // 4 x 875 + 1750
    expect(w.find("[data-test=price]").text()).toBe(EUR(5250));
    await w.find("[data-test=annual]").setValue(true);                               // 5250 x 12 less 10 %
    expect(w.find("[data-test=price]").text()).toBe(EUR(56700));
    expect(w.text()).toContain("/ year");
  });

  it("saves the plan as six lines with the version it was based on, then reloads", async () => {
    e.putPlan.mockReturnValue(ok({ changed: true, plan: plan({ version: 3, centsPeriod: 3360 }) }));
    const { w, ctx } = await page(CustomerPlan, "/dealer/customers/c-1/plan");
    await qty(w, "8mp", "continuous").setValue("1");
    await byText(w, "Save plan").trigger("click");
    await flushPromises();
    const [adapterId, sent] = e.putPlan.mock.calls[0];
    expect(adapterId).toBe("a-1");
    expect(sent).toMatchObject({ retentionDays: 30, interval: "monthly", basedOn: 2 });
    expect(sent.lines).toHaveLength(6);
    expect(sent.lines.filter((l: { quantity: number }) => l.quantity)).toEqual([
      { resolution: "4mp", recording: "motion", quantity: 4 }, { resolution: "8mp", recording: "continuous", quantity: 1 }]);
    expect(w.text()).toContain(`Saved: version 3, ${EUR(3360)} a month.`);
    expect(ctx.reload).toHaveBeenCalled();
  });

  it("an edit someone else overtook is refused, and the plan reloaded", async () => {
    e.putPlan.mockReturnValue(refused(409, "the plan was changed meanwhile (now version 3): reload it"));
    const { w, ctx } = await page(CustomerPlan, "/dealer/customers/c-1/plan");
    await qty(w, "2mp", "motion").setValue("2");
    await byText(w, "Save plan").trigger("click");
    await flushPromises();
    expect(w.find("[data-test=error]").text()).toContain("changed meanwhile");
    expect(ctx.reload).toHaveBeenCalled();
  });

  it("won't save a count that isn't 0 to 100", async () => {
    const { w } = await page(CustomerPlan, "/dealer/customers/c-1/plan");
    await qty(w, "2mp", "motion").setValue("101");
    expect(byText(w, "Save plan").attributes("disabled")).toBeDefined();
    expect(w.find("[data-test=price]").text()).toBe("—");
  });

  it("another adapter has its own plan: none yet, so everything starts at 0", async () => {
    e.putPlan.mockReturnValue(ok({ changed: true, plan: plan({ id: "p-9", version: 1 }) }));
    const { w, router } = await page(CustomerPlan, "/dealer/customers/c-1/plan");
    await w.find("[data-test=adapter]").setValue("a-2");
    await flushPromises();
    expect(router.currentRoute.value.params.adapterId).toBe("a-2");
    expect(w.find("[data-test=plan-status]").text()).toBe("No plan yet.");
    expect((qty(w, "4mp", "motion").element as HTMLInputElement).value).toBe("0");
    expect(w.find("[data-test=price]").text()).toBe(EUR(0));
    await byText(w, "Save plan").trigger("click");
    await flushPromises();
    expect(e.putPlan.mock.calls[0][0]).toBe("a-2");
    expect(e.putPlan.mock.calls[0][1].basedOn).toBe(0);
  });

  it("cancels the subscription after asking", async () => {
    e.cancelPlan.mockReturnValue(ok({ changed: true, plan: plan({ version: 3, status: "cancelled", effectiveStatus: "cancelled" }) }));
    const { w } = await page(CustomerPlan, "/dealer/customers/c-1/plan");
    await byText(w, "Cancel subscription").trigger("click");
    expect(e.cancelPlan).not.toHaveBeenCalled();
    await w.find("[data-test=confirm-cancel]").trigger("click");
    await flushPromises();
    expect(e.cancelPlan).toHaveBeenCalledWith("a-1", 2);
    expect(w.text()).toContain("Subscription cancelled (version 3)");
  });

  it("says so when the customer has no adapter yet", async () => {
    const { w } = await page(CustomerPlan, "/dealer/customers/c-1/plan",
                             context({ ...ADAPTERS, adapters: [], total: { ...ADAPTERS.total, adapters: 0 } }));
    expect(w.find("[data-test=no-adapter]").text()).toBe("No adapter is activated for this customer yet.");
    expect(w.find("[data-test=price]").exists()).toBe(false);
    expect(w.find("[data-test=start-activate]").text()).toBe("Activate an adapter");
  });
});

// --- plan history ----------------------------------------------------------------------------------

describe("plan history", () => {
  it("lists every version, newest first, with what it changed", async () => {
    const v1 = plan({ id: "p-1", version: 1, lines: linesOf(null), cameras: 0, centsPeriod: 0, createdBy: null });
    const v2 = plan();
    const v3 = plan({ id: "p-3", version: 3, interval: "annual", centsPeriod: 24192 });
    const v4 = plan({ id: "p-4", version: 4, status: "cancelled", effectiveStatus: "cancelled", interval: "annual", centsPeriod: 0 });
    e.planHistory.mockReturnValue(ok({ adapter: { thingName: "adapter-01", name: "Hall", site: "Home" }, plans: [v4, v3, v2, v1] }));
    const { w } = await page(CustomerHistory, "/dealer/customers/c-1/history");
    expect(e.planHistory).toHaveBeenCalledWith("a-1");
    const changes = (v: number) => w.find(`[data-test=version-${v}] .changes`).text();
    expect(changes(4)).toBe("Trial → Cancelled");
    expect(changes(3)).toBe("payment: monthly → annual");
    expect(changes(2)).toBe("4MP motion: 0 → 4");
    expect(changes(1)).toBe("started");
    expect(w.find("[data-test=version-4]").text()).toContain("—");                   // nothing charged
    expect(w.find("[data-test=version-3]").text()).toContain(`${EUR(24192)} / year`);
    expect(w.find("[data-test=version-1]").text()).toContain("the system");
  });
});

// --- cameras ---------------------------------------------------------------------------------------

describe("the customer's cameras", () => {
  it("are listed by adapter with their class and recording, never a picture", async () => {
    const { w } = await page(CustomerCameras, "/dealer/customers/c-1/cameras");
    const first = w.find("[data-test=adapter-adapter-01]");
    expect(first.find("h3").text()).toBe("Home · Hall — adapter-01");
    expect(first.text()).toContain("4 in plan, 3 on this adapter (1 of unknown picture size)");
    const rows = first.findAll("tbody tr").map(r => r.findAll("td").map(t => t.text()));
    expect(rows[0].slice(0, 4)).toEqual(["cam-01", "H.264", "unknown", "continuous · manual"]);
    expect(rows[1].slice(0, 4)).toEqual(["Gate · cam-02", "H.265", "4MP · 2560x1440", "motion · motion"]);
    expect(w.find("[data-test=adapter-adapter-0302]").text()).toContain("No camera on this adapter yet.");
    expect(w.findAll("img")).toHaveLength(0);
  });

  it("say when the registry couldn't be read", async () => {
    const unread = { ...ADAPTERS, registryError: "the camera registry couldn't be read (AccessDeniedException)",
                     adapters: ADAPTERS.adapters.map(a => ({ ...a, cameras: null, actual: null })) };
    const { w } = await page(CustomerCameras, "/dealer/customers/c-1/cameras", context(unread));
    expect(w.find("[data-test=registry-error]").text()).toContain("AccessDeniedException");
    expect(w.findAll("tbody tr")).toHaveLength(0);
  });
});

// --- people, details -------------------------------------------------------------------------------

describe("the customer's people", () => {
  const PEOPLE = [
    { id: "u-1", email: "boss@acme.example", name: null, status: "active", lastSignInAt: null, role: "customer", manageable: true },
    { id: "u-2", email: "new@acme.example", name: null, status: "invited", lastSignInAt: null, role: "customer_user", manageable: true },
    { id: "u-3", email: "shared@acme.example", name: null, status: "active", lastSignInAt: null, role: "customer_user", manageable: false },
  ];

  it("lists them with the actions each allows", async () => {
    e.customerUsers.mockReturnValue(ok(PEOPLE));
    e.userAction.mockReturnValue(ok({ userId: "u-2", status: "invited" }));
    const { w } = await page(CustomerUsers, "/dealer/customers/c-1/users");
    const actions = (email: string) => w.find(`[data-test="person-${email}"]`).findAll("button").map(b => b.text());
    expect(w.find(`[data-test="person-boss@acme.example"]`).text()).toContain("Account holder");
    expect(actions("boss@acme.example")).toEqual(["Disable", "Reset password"]);
    expect(actions("new@acme.example")).toEqual(["Disable", "Resend invitation"]);
    expect(actions("shared@acme.example")).toEqual([]);
    expect(w.find("[data-test=invite-holder]").exists()).toBe(false);                 // it has a holder
    await w.find(`[data-test="person-new@acme.example"]`).findAll("button")[1].trigger("click");
    await flushPromises();
    expect(e.userAction).toHaveBeenCalledWith("u-2", "resend-invite");
  });

  it("invites the holder of a customer that has none", async () => {
    e.customerUsers.mockReturnValue(ok([]));
    e.inviteUser.mockReturnValue(ok({ userId: "u-9", invited: "jo@acme.example" }));
    const { w, ctx } = await page(CustomerUsers, "/dealer/customers/c-1/users", context(ADAPTERS, { ...DETAIL, holder: null }));
    await w.find("[data-test=holder-email]").setValue(" jo@acme.example ");
    await w.find("[data-test=invite-holder]").trigger("submit");
    await flushPromises();
    expect(e.inviteUser).toHaveBeenCalledWith({ email: "jo@acme.example", name: undefined, orgId: "c-1", role: "customer" });
    expect(ctx.reload).toHaveBeenCalled();
  });
});

describe("the customer's details", () => {
  it("sends only what changed, and suspends", async () => {
    e.updateCustomer.mockReturnValue(ok({ id: "c-1", changed: ["reference"] }));
    const { w } = await page(CustomerDetails, "/dealer/customers/c-1/details");
    await w.find("[data-test=reference]").setValue("A-8");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(e.updateCustomer).toHaveBeenCalledWith("c-1", { reference: "A-8" });
    await w.find("[data-test=suspend]").trigger("click");
    await flushPromises();
    expect(e.updateCustomer).toHaveBeenLastCalledWith("c-1", { status: "suspended" });
  });
});

// --- billing, the dealer's details -----------------------------------------------------------------

describe("billing", () => {
  it("is a demo: each customer's total and the sum, no payment", async () => {
    e.listCustomers.mockReturnValue(ok([ACME, BETA, { ...ACME, id: "c-5", name: "No adapter", adapters: 0, centsMonth: 0 }]));
    const router = await routed("/dealer/billing");
    const w = mount(DealerBilling, { global: { plugins: plugins(router) } });
    await flushPromises();
    expect(w.find("[data-test=demo-banner]").text()).toBe("Demo billing: no payment is taken.");
    expect(w.findAll("tbody tr")).toHaveLength(2);
    expect(w.find("[data-test=sum]").text()).toBe(EUR(3240));
    expect(w.findAll("input")).toHaveLength(0);                                      // no card or bank fields, not even fake (D9)
    expect(w.text()).not.toMatch(/card number|iban/i);
  });
});

describe("dealer details", () => {
  it("saves the fields that changed, the address as one", async () => {
    e.getDealer.mockReturnValue(ok({ id: "d-1", name: "North", email: null, phone: null, address: { city: "Bonn" },
                                     createdAt: "2026-11-01T10:00:00Z" }));
    e.updateOwnDealer.mockReturnValue(ok({ id: "d-1", changed: ["address", "phone"] }));
    const router = await routed("/dealer/details");
    const w = mount(DealerDetails, { global: { plugins: plugins(router) }, attachTo: document.body });
    await flushPromises();
    await w.find("[data-test=phone]").setValue("+49 30 1");
    await w.find("[data-test=address-line1]").setValue("Main St 1");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(e.updateOwnDealer).toHaveBeenCalledWith({ phone: "+49 30 1", address: { line1: "Main St 1", city: "Bonn" } });
    expect(w.text()).toContain("Saved.");
  });
});

// --- the plan vocabulary ---------------------------------------------------------------------------

describe("plans.ts", () => {
  it("reads a plan's cameras and lists changes field by field", () => {
    expect(camerasText(linesOf(null))).toBe("no cameras");
    expect(camerasText(plan().lines)).toBe("4 × 4MP motion");
    const before = plan(), after = plan({ retentionDays: 90, lines: linesOf({ lines: [
      { resolution: "4mp", recording: "motion", quantity: 2 }, { resolution: "2mp", recording: "continuous", quantity: 1 }] }) });
    expect(planChanges(before, after)).toEqual(["2MP continuous: 0 → 1", "4MP motion: 4 → 2", "storage: 30 → 90 days"]);
    expect(planChanges(before, plan())).toEqual(["saved again"]);
  });
});
