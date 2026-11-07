// The System console and the role-shaped page (DEALER-PANEL-PLAN.md Phase 4, D6, D7): where each
// role lands, what the rail offers it, and the console's tabs against a faked directory API. The
// API decides every call again (tests/cloud/test_directory_api.py, tests_db/test_directory_ops.py);
// what is pinned here is that the page asks for the right thing and shows what comes back.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Component } from "vue";
import type { Role } from "../src/api/types";

const fake = vi.hoisted(() => {
  const fn = () => vi.fn();
  return {
    session: { signedIn: true, username: "me@example.test" as string | null, claims: { vsz_role: "system_admin" } as Record<string, unknown>,
               onChange: () => () => {} },
    endpoints: {
      me: fn(), overview: fn(), listDealers: fn(), createDealer: fn(), updateDealer: fn(), listCustomers: fn(),
      listAdapters: fn(), listUsers: fn(), inviteUser: fn(), userAction: fn(), getPricing: fn(), putPricing: fn(),
    },
  };
});
vi.mock("../src/api", () => ({ session: fake.session, endpoints: fake.endpoints }));

import AppRail from "../src/components/AppRail.vue";
import { makeRouter } from "../src/router";
import SystemCustomers from "../src/views/system/SystemCustomers.vue";
import SystemDealers from "../src/views/system/SystemDealers.vue";
import SystemOverview from "../src/views/system/SystemOverview.vue";
import SystemPricing from "../src/views/system/SystemPricing.vue";
import SystemUsers from "../src/views/system/SystemUsers.vue";

const ok = <T>(data: T) => Promise.resolve({ ok: true, status: 200, data });
const refused = (status: number, error: string) => Promise.resolve({ ok: false, status, data: { error } });
const e = fake.endpoints;

const PLATFORM = { id: "p-1", kind: "platform" as const, name: "VideoSafeZone" };
const NORTH = { id: "d-1", name: "North", email: "sales@north.example", phone: null, status: "active" as const,
                createdAt: "2026-11-01T10:00:00Z", customers: 2, users: 1, adapters: 3 };
const SOUTH = { ...NORTH, id: "d-2", name: "South", email: null, status: "suspended" as const, customers: 0 };
const ACME = { id: "c-1", name: "Acme", reference: "A-7", status: "active" as const, createdAt: "2026-11-02T10:00:00Z",
               dealer: { id: "d-1", name: "North" }, adapters: 1, users: 2 };

function signIn(role: Role, email = "me@example.test") {
  fake.session.claims = { vsz_role: role };
  fake.session.username = email;
  e.me.mockReturnValue(ok({ email, role, org: role === "dealer" ? { id: "d-1", kind: "dealer", name: "North" } : PLATFORM, accounts: [] }));
}

async function mountTab(view: Component, path: string, role: Role = "system_admin", email?: string) {
  signIn(role, email);
  const router = makeRouter(() => true, () => role);
  await router.push(path);
  const w = mount(view, { global: { plugins: [router, [OpenVue, { unstyled: true }]] }, attachTo: document.body });
  await flushPromises();
  return w;
}
const button = (w: { findAll: (s: string) => Array<{ text: () => string }> }, label: string) =>
  w.findAll("button").find(b => b.text() === label) as never as { trigger: (ev: string) => Promise<void> };

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  document.body.innerHTML = "";
});

describe("where each role lands", () => {
  it.each([
    ["dealer", "dealer"], ["system_owner", "system-overview"], ["system_admin", "live"], ["customer", "live"],
  ] as const)("%s starts on %s", async (role, name) => {
    const router = makeRouter(() => true, () => role);
    await router.push("/");
    expect(router.currentRoute.value.name).toBe(name);
    await router.push("/login");                          // already signed in: home again
    expect(router.currentRoute.value.name).toBe(name);
  });

  it.each([
    ["dealer", "/live/cam-01", "dealer"], ["dealer", "/system/users", "dealer"], ["dealer", "/settings", "dealer"],
    ["system_owner", "/live", "system-overview"], ["system_owner", "/dealer", "system-overview"],
    ["customer", "/system/pricing", "live"], ["customer", "/dealer", "live"],
  ] as const)("%s asking for %s is sent home", async (role, path, name) => {
    const router = makeRouter(() => true, () => role);
    await router.push(path);
    expect(router.currentRoute.value.name).toBe(name);
  });

  it("an administrator reaches both the cameras and the System console", async () => {
    const router = makeRouter(() => true, () => "system_admin");
    for (const [path, name] of [["/system/pricing", "system-pricing"], ["/settings/cam-01", "settings"], ["/system", "system-overview"]]) {
      await router.push(path);
      expect(router.currentRoute.value.name).toBe(name);
    }
  });

  it("a token without a role (the old pool) keeps the camera pages only", async () => {
    const router = makeRouter(() => true);
    await router.push("/system");
    expect(router.currentRoute.value.name).toBe("live");
  });
});

describe("the rail, role by role", () => {
  it.each([
    ["customer", ["Live", "Events", "Settings", "Account", "Exit"]],
    ["dealer", ["Dealer portal", "Account", "Exit"]],
    ["system_owner", ["System", "Account", "Exit"]],
    ["system_admin", ["Live", "Events", "Settings", "System", "Account", "Exit"]],
  ] as const)("%s sees %j", async (role, labels) => {
    signIn(role);
    const router = makeRouter(() => true, () => role);
    await router.push("/account");
    const w = mount(AppRail, { global: { plugins: [router] } });
    expect(w.findAll(".rail-item span").map(s => s.text())).toEqual(labels);
  });

  it("the System entry is active on every System tab", async () => {
    signIn("system_owner");
    const router = makeRouter(() => true, () => "system_owner");
    await router.push("/system/users");
    const w = mount(AppRail, { global: { plugins: [router] } });
    expect(w.find("a.active").text()).toBe("System");
  });
});

describe("Overview", () => {
  it("shows the counts and the memberships by role", async () => {
    e.overview.mockReturnValue(ok({
      dealers: { active: 2, suspended: 1 }, customers: { active: 5 }, users: { dealer: 2, customer: 5, customer_user: 3 },
      invited: 4, adapters: { allocated: 2, activated: 3 }, plans: { trial: 1, active: 3, cancelled: 0, monthlyCents: 4497, currency: "EUR" },
    }));
    const w = await mountTab(SystemOverview, "/system/overview");
    const text = w.text();
    expect(text).toContain("2 dealers");
    expect(text).toContain("1 suspended");
    expect(text).toContain("10 memberships");
    expect(text).toContain("4 invited");
    expect(text).toContain("5 adapters");
    expect(text).toContain("44.97 EUR a month");
    expect(w.findAll(".roles tbody tr").map(r => r.text())).toEqual(["Dealer2", "Account holder5", "User3"]);
  });

  it("shows the API's refusal", async () => {
    e.overview.mockReturnValue(refused(403, "not allowed for your role"));
    const w = await mountTab(SystemOverview, "/system/overview");
    expect(w.find("[role=alert]").text()).toBe("not allowed for your role");
  });
});

describe("Dealers", () => {
  beforeEach(() => e.listDealers.mockReturnValue(ok([NORTH, SOUTH])));

  it("lists them with their counts and status", async () => {
    const w = await mountTab(SystemDealers, "/system/dealers");
    const rows = w.findAll(".console-table tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("North");
    expect(rows[0].text()).toContain("sales@north.example");
    expect(rows[0].find(".status").classes()).toContain("active");
    expect(rows[1].find(".status").text()).toBe("suspended");
  });

  it("creates one, sending blank fields as absent, and reloads the list", async () => {
    e.createDealer.mockReturnValue(ok({ id: "d-3" }));
    const w = await mountTab(SystemDealers, "/system/dealers");
    await w.find("[data-test=dealer-name]").setValue("  East ");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(e.createDealer).toHaveBeenCalledWith({ name: "East", email: undefined, phone: undefined });
    expect(e.listDealers).toHaveBeenCalledTimes(2);
    expect(w.text()).toContain("Dealer East created.");
  });

  it("shows a refusal, such as a name already taken", async () => {
    e.createDealer.mockReturnValue(refused(409, "a dealer with that name exists"));
    const w = await mountTab(SystemDealers, "/system/dealers");
    await w.find("[data-test=dealer-name]").setValue("North");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(w.find("[role=alert]").text()).toBe("a dealer with that name exists");
  });

  it("suspends an active dealer and reactivates a suspended one", async () => {
    e.updateDealer.mockReturnValue(ok({ id: "d-1", changed: ["status"] }));
    const w = await mountTab(SystemDealers, "/system/dealers");
    await button(w, "Suspend").trigger("click");
    await button(w, "Activate").trigger("click");
    await flushPromises();
    expect(e.updateDealer.mock.calls).toEqual([["d-1", { status: "suspended" }], ["d-2", { status: "active" }]]);
  });

  it("invites a dealer's first user as a dealer of that organisation", async () => {
    e.inviteUser.mockReturnValue(ok({ userId: "u-9", invited: "anna@north.example" }));
    const w = await mountTab(SystemDealers, "/system/dealers");
    await w.findAll("button").filter(b => b.text() === "Invite user")[0].trigger("click");
    await w.find("[data-test=invite-email]").setValue(" anna@north.example ");
    await w.findAll("form")[1].trigger("submit");
    await flushPromises();
    expect(e.inviteUser).toHaveBeenCalledWith({ email: "anna@north.example", name: undefined, orgId: "d-1", role: "dealer" });
    expect(w.text()).toContain("Invitation sent to anna@north.example");
    expect(w.find("[data-test=invite-email]").exists()).toBe(false);
  });
});

describe("Customers", () => {
  it("lists every customer and filters by dealer", async () => {
    e.listDealers.mockReturnValue(ok([NORTH, SOUTH]));
    e.listCustomers.mockReturnValue(ok([ACME]));
    const w = await mountTab(SystemCustomers, "/system/customers");
    expect(e.listCustomers).toHaveBeenCalledWith(undefined);
    expect(w.find(".console-table tbody tr").text()).toContain("AcmeA-7North");
    await w.find("[data-test=dealer-filter]").setValue("d-2");
    await flushPromises();
    expect(e.listCustomers).toHaveBeenLastCalledWith("d-2");
  });
});

describe("Users", () => {
  const USERS = [
    { id: "u-1", email: "me@example.test", name: null, status: "active", lastSignInAt: "2026-11-07T08:00:00Z", createdAt: "",
      memberships: [{ orgId: "p-1", org: "VideoSafeZone", kind: "platform", role: "system_admin" }] },
    { id: "u-2", email: "anna@north.example", name: "Anna", status: "invited", lastSignInAt: null, createdAt: "",
      memberships: [{ orgId: "d-1", org: "North", kind: "dealer", role: "dealer" }] },
    { id: "u-3", email: "bob@acme.example", name: null, status: "active", lastSignInAt: "2026-11-06T08:00:00Z", createdAt: "",
      memberships: [{ orgId: "c-1", org: "Acme", kind: "customer", role: "customer_admin" }] },
    { id: "u-4", email: "eve@acme.example", name: null, status: "disabled", lastSignInAt: null, createdAt: "",
      memberships: [{ orgId: "c-1", org: "Acme", kind: "customer", role: "customer_user" }] },
  ];
  beforeEach(() => {
    e.listUsers.mockReturnValue(ok(USERS));
    e.listDealers.mockReturnValue(ok([NORTH]));
    e.listCustomers.mockReturnValue(ok([ACME]));
  });
  const row = (w: Awaited<ReturnType<typeof mountTab>>, email: string) => w.find(`[data-test="user-${email}"]`);
  const actions = (w: Awaited<ReturnType<typeof mountTab>>, email: string) =>
    row(w, email).findAll("button").map(b => b.text());

  it("lists people with where they belong, as what, and the actions their status allows", async () => {
    const w = await mountTab(SystemUsers, "/system/users");
    expect(row(w, "bob@acme.example").text()).toContain("Super Admin · Acme");
    expect(actions(w, "me@example.test")).toEqual([]);                 // not yourself
    expect(row(w, "me@example.test").text()).toContain("you");
    expect(actions(w, "anna@north.example")).toEqual(["Disable", "Resend invitation"]);
    expect(actions(w, "bob@acme.example")).toEqual(["Disable", "Reset password"]);
    expect(actions(w, "eve@acme.example")).toEqual(["Enable"]);
  });

  it("searches every word across email, organisation and role", async () => {
    const w = await mountTab(SystemUsers, "/system/users");
    await w.find('input[placeholder="email, organisation, role"]').setValue("acme USER");
    expect(w.findAll("tbody tr").map(r => r.attributes("data-test"))).toEqual(["user-eve@acme.example"]);
  });

  it("disables someone and reloads", async () => {
    e.userAction.mockReturnValue(ok({ userId: "u-3", status: "disabled" }));
    const w = await mountTab(SystemUsers, "/system/users");
    await row(w, "bob@acme.example").findAll("button")[0].trigger("click");
    await flushPromises();
    expect(e.userAction).toHaveBeenCalledWith("u-3", "disable");
    expect(w.text()).toContain("bob@acme.example: disabled -- signed out everywhere.");
    expect(e.listUsers).toHaveBeenCalledTimes(2);
  });

  it("offers the roles that fit the organisation, and an administrator every one", async () => {
    const w = await mountTab(SystemUsers, "/system/users");
    const roleOptions = () => w.findAll("[data-test=user-role] option:not([disabled])").map(o => o.text());
    expect(w.findAll("[data-test=user-org] option:not([disabled])").map(o => o.text())).toEqual(
      ["VideoSafeZone (platform)", "North (dealer)", "Acme (customer of North)"]);
    await w.find("[data-test=user-org]").setValue("p-1");
    expect(roleOptions()).toEqual(["System owner", "System administrator"]);
    await w.find("[data-test=user-org]").setValue("c-1");
    expect(roleOptions()).toEqual(["Account holder", "Super Admin", "User"]);
  });

  it("an owner is offered a dealer's staff and a customer's holder only, and leaves the system's staff alone", async () => {
    const w = await mountTab(SystemUsers, "/system/users", "system_owner", "owner@example.test");
    const roleOptions = () => w.findAll("[data-test=user-role] option:not([disabled])").map(o => o.text());
    await w.find("[data-test=user-org]").setValue("p-1");
    expect(roleOptions()).toEqual([]);
    await w.find("[data-test=user-org]").setValue("d-1");
    expect(roleOptions()).toEqual(["Dealer"]);
    await w.find("[data-test=user-org]").setValue("c-1");
    expect(roleOptions()).toEqual(["Account holder"]);                 // Super Admins and users: the holder's (D18)
    expect(actions(w, "me@example.test")).toEqual([]);                 // an administrator, seen by an owner
    expect(row(w, "me@example.test").text()).not.toContain("you");
    expect(actions(w, "bob@acme.example")).toEqual(["Disable", "Reset password"]);
  });

  it("invites someone into a customer account", async () => {
    e.inviteUser.mockReturnValue(ok({ userId: "u-5", invited: "carl@acme.example" }));
    const w = await mountTab(SystemUsers, "/system/users");
    await w.find("[data-test=user-email]").setValue("carl@acme.example");
    await w.find("[data-test=user-org]").setValue("c-1");
    await w.find("[data-test=user-role]").setValue("customer_user");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(e.inviteUser).toHaveBeenCalledWith({ email: "carl@acme.example", name: undefined, orgId: "c-1", role: "customer_user" });
    expect(w.text()).toContain("Invitation sent to carl@acme.example");
  });

  it("shows the API's refusal of an invitation", async () => {
    e.inviteUser.mockReturnValue(refused(409, "can't add this address"));
    const w = await mountTab(SystemUsers, "/system/users");
    await w.find("[data-test=user-email]").setValue("taken@example.test");
    await w.find("[data-test=user-org]").setValue("d-1");
    await w.find("[data-test=user-role]").setValue("dealer");
    await w.find("form").trigger("submit");
    await flushPromises();
    expect(w.find("[role=alert]").text()).toBe("can't add this address");
  });
});

describe("Pricing", () => {
  const PRICING = {
    prices: [
      ...(["2mp", "4mp", "8mp"] as const).flatMap((r, i) => (["motion", "continuous"] as const).flatMap((c, j) =>
        [7, 30].map(d => ({ resolution: r, recording: c, retentionDays: d, centsMonth: 100 * (i + 1) + 50 * j + d })))),
    ],
    settings: { retention_days: [7, 30], annual_discount_percent: 15, trial_days: 30, currency: "EUR", placeholder_prices: true },
  };
  beforeEach(() => e.getPricing.mockReturnValue(ok(PRICING)));
  const cell = (w: Awaited<ReturnType<typeof mountTab>>, k: string) => w.find(`[data-test="price-${k}"]`);
  // OpenVue's Message gives the placeholder warning role=alert too.
  const alerts = (w: Awaited<ReturnType<typeof mountTab>>) => w.findAll("[role=alert]").map(a => a.text());

  it("shows the matrix in currency units, and says the prices are placeholders", async () => {
    const w = await mountTab(SystemPricing, "/system/pricing");
    expect(w.text()).toContain("placeholder prices");
    expect(w.findAll(".pricing thead th").map(t => t.text())).toEqual(["Camera", "Recording", "7 days", "30 days"]);
    expect(w.findAll(".pricing tbody tr")).toHaveLength(6);
    expect((cell(w, "4mp|continuous|30").element as HTMLInputElement).value).toBe("2.80");
    await w.find("[data-test=days]").setValue("30 7");                // typed in any order: shown in order
    expect(w.findAll(".pricing thead th").map(t => t.text()).slice(2)).toEqual(["7 days", "30 days"]);
  });

  it("saves the whole list in cents, with the settings", async () => {
    e.putPricing.mockReturnValue(ok({ prices: 12 }));
    const w = await mountTab(SystemPricing, "/system/pricing");
    await cell(w, "2mp|motion|7").setValue("4,5");
    await button(w, "Save the price list").trigger("click");
    await flushPromises();
    const [prices, settings] = e.putPricing.mock.calls[0];
    expect(prices).toHaveLength(12);
    expect(prices[0]).toEqual({ resolution: "2mp", recording: "motion", retentionDays: 7, centsMonth: 450 });
    expect(prices).toContainEqual({ resolution: "8mp", recording: "continuous", retentionDays: 30, centsMonth: 380 });
    expect(settings).toEqual({ retention_days: [7, 30], annual_discount_percent: 15, trial_days: 30 });
    expect(w.text()).toContain("Saved: 12 prices.");
  });

  it("a new storage duration needs its prices before anything is sent", async () => {
    const w = await mountTab(SystemPricing, "/system/pricing");
    await w.find("[data-test=days]").setValue("7, 14, 30");
    expect(w.findAll(".pricing thead th").map(t => t.text())).toContain("14 days");
    await button(w, "Save the price list").trigger("click");
    await flushPromises();
    expect(e.putPricing).not.toHaveBeenCalled();
    expect(alerts(w)).toContain("Give a price for 2MP motion, 14 days.");
  });

  it.each([["7, 7"], ["0"], ["3651"], ["seven"], [""]])("refuses storage durations %j", async days => {
    const w = await mountTab(SystemPricing, "/system/pricing");
    await w.find("[data-test=days]").setValue(days);
    await button(w, "Save the price list").trigger("click");
    expect(e.putPricing).not.toHaveBeenCalled();
    expect(w.find(".pricing").exists()).toBe(false);
  });

  it("refuses a discount over 50 % and a price that isn't money", async () => {
    const w = await mountTab(SystemPricing, "/system/pricing");
    await w.findAll("input")[1].setValue("60");
    await button(w, "Save the price list").trigger("click");
    expect(alerts(w)).toContain("Annual discount: a whole percent from 0 to 50.");
    await w.findAll("input")[1].setValue("10");
    await cell(w, "2mp|motion|7").setValue("1.234");
    await button(w, "Save the price list").trigger("click");
    expect(alerts(w)).toContain("Give a price for 2MP motion, 7 days.");
    expect(e.putPricing).not.toHaveBeenCalled();
  });
});
