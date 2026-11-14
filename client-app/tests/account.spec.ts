// The Account page (DEALER-PANEL-PLAN.md D18, Phase 9), with the session and the directory API faked:
// its tabs role by role; Account details; Plan & Billing (the holder's, read-only); Additional users
// (Add user, Users, Pending invites, handing the account over); and the rail's Account entry. The API
// decides every call again (tests/cloud/test_directory_api.py, tests_db/test_accounts.py).
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import ToggleSwitch from "openvue/toggleswitch";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountPlans, AccountUsers, Me, MemberRole, Plan } from "../src/api/types";
import { AuthError } from "../src/auth/cognito";

const fake = vi.hoisted(() => {
  const fn = () => vi.fn();
  return {
    session: {
      signedIn: true, username: "anna@example.test" as string | null, claims: { vsz_role: "customer" } as Record<string, unknown>,
      onChange: () => () => {}, changePassword: vi.fn(), changeEmail: vi.fn(), confirmEmail: vi.fn(),
    },
    endpoints: {
      me: fn(), accountPlans: fn(), accountUsers: fn(), addAccountUser: fn(), updateAccountUser: fn(),
      removeAccountUser: fn(), accountInvite: fn(), handOverAccount: fn(),
    },
  };
});
vi.mock("../src/api", () => ({ session: fake.session, endpoints: fake.endpoints }));

import AppRail from "../src/components/AppRail.vue";
import { linesOf } from "../src/dealer/plans";
import { money } from "../src/pricing";
import { makeRouter } from "../src/router";
import { useAuthStore } from "../src/stores/auth";
import AccountView from "../src/views/AccountView.vue";

const ok = <T>(data: T) => Promise.resolve({ ok: true, status: 200, data });
const refused = (status: number, error: string) => Promise.resolve({ ok: false, status, data: { error } });
const e = fake.endpoints;
const EUR = (cents: number) => money(cents, "EUR");

const account = (id: string, name: string, role: MemberRole) => ({
  id, name, role, dealer: "North", sites: [{ id: "s-1", name: "Home", timezone: "Europe/Berlin" }],
  adapters: [{ thingName: "adapter-01", name: "Hall", siteId: "s-1" }],
});
const me = (...accounts: Me["accounts"]): Me => ({ email: "anna@example.test", role: "customer", org: null, accounts });

async function mountAccount(path = "/account", role = "customer") {
  fake.session.claims = { vsz_role: role };
  const router = makeRouter(() => true);
  await router.push(path);
  const w = mount(AccountView, { global: { plugins: [router, [OpenVue, { unstyled: true }]] }, attachTo: document.body });
  await flushPromises();
  return w;
}
const button = (w: Awaited<ReturnType<typeof mountAccount>>, label: string) =>
  w.findAll("button").find(b => b.text() === label)!;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  document.body.innerHTML = "";
});

describe("Account details", () => {
  it("shows the email address and a hidden password, each with Edit", async () => {
    const w = await mountAccount();
    expect(w.find(".tab.active").text()).toBe("Account details");
    expect(w.find("[data-test=email]").text()).toBe("anna@example.test");
    expect(w.text()).toContain("Password: ********");
    expect(w.findAll("button.link").map(b => b.text())).toEqual(["Edit", "Edit"]);
    expect(useAuthStore().role).toBe("customer");
  });

  it("changes the password when both new ones agree", async () => {
    fake.session.changePassword.mockResolvedValue(undefined);
    const w = await mountAccount();
    await w.findAll("button.link")[1].trigger("click");
    await w.find("#current-password").setValue("OldPass1234");
    await w.find("#next-password").setValue("NewPass1234");
    await w.find("#repeat-password").setValue("NewPass9999");
    await button(w, "Change password").trigger("click");
    await flushPromises();
    expect(w.find("[role=alert]").text()).toContain("aren't the same");
    expect(fake.session.changePassword).not.toHaveBeenCalled();
    await w.find("#repeat-password").setValue("NewPass1234");
    await button(w, "Change password").trigger("click");
    await flushPromises();
    expect(fake.session.changePassword).toHaveBeenCalledWith("OldPass1234", "NewPass1234");
    expect(w.text()).toContain("Your password is changed.");
  });

  it("changes the email address in two steps: a code to the new one, then confirming it", async () => {
    fake.session.changeEmail.mockResolvedValue(undefined);
    fake.session.confirmEmail.mockResolvedValue(undefined);
    const w = await mountAccount();
    await w.findAll("button.link")[0].trigger("click");
    await w.find("#new-email").setValue("anna@new.example");
    await button(w, "Send code").trigger("click");
    await flushPromises();
    expect(fake.session.changeEmail).toHaveBeenCalledWith("anna@new.example");
    expect(w.text()).toContain("you keep using your current address");
    await w.find("#email-code").setValue(" 123456 ");
    await button(w, "Confirm").trigger("click");
    await flushPromises();
    expect(fake.session.confirmEmail).toHaveBeenCalledWith("123456");
    expect(w.text()).toContain("Your email address is changed");
  });

  it("shows Cognito's refusal", async () => {
    fake.session.changeEmail.mockRejectedValue(new AuthError("An account with the given email already exists."));
    const w = await mountAccount();
    await w.findAll("button.link")[0].trigger("click");
    await w.find("#new-email").setValue("taken@example.test");
    await button(w, "Send code").trigger("click");
    await flushPromises();
    expect(w.find("[role=alert]").text()).toBe("An account with the given email already exists.");
  });
});

describe("the rail", () => {
  it("has Account above Exit", async () => {
    const router = makeRouter(() => true);
    await router.push("/account");
    const w = mount(AppRail, { global: { plugins: [router] } });
    const labels = w.findAll(".rail-item span").map(s => s.text());
    expect(labels.slice(-2)).toEqual(["Account", "Exit"]);
    expect(w.find("a.active").text()).toBe("Account");
  });
});

// --- the tabs, role by role ---------------------------------------------------------------------------

describe("the tabs", () => {
  const tabs = (w: Awaited<ReturnType<typeof mountAccount>>) => w.findAll(".tab").map(t => t.text());

  it("a holder gets Plan & Billing and Additional users", async () => {
    e.me.mockReturnValue(ok(me(account("a-1", "Acme", "customer"))));
    expect(tabs(await mountAccount())).toEqual(["Account details", "Plan & Billing", "Additional users"]);
  });

  it("a Super Admin gets Additional users, and a user Account details alone", async () => {
    e.me.mockReturnValue(ok(me(account("a-1", "Acme", "customer_admin"))));
    expect(tabs(await mountAccount())).toEqual(["Account details", "Additional users"]);
    setActivePinia(createPinia());
    e.me.mockReturnValue(ok(me(account("a-1", "Acme", "customer_user"))));
    expect(tabs(await mountAccount())).toEqual(["Account details"]);
  });

  it("dealers, owners and administrators get Account details only, without asking the directory", async () => {
    for (const role of ["dealer", "system_owner", "system_admin"]) {
      setActivePinia(createPinia());
      expect(tabs(await mountAccount("/account", role))).toEqual(["Account details"]);
    }
    expect(e.me).not.toHaveBeenCalled();
  });

  it("a tab the person may not open shows Account details", async () => {
    e.me.mockReturnValue(ok(me(account("a-1", "Acme", "customer_user"))));
    const w = await mountAccount("/account/plans");
    expect(w.find(".tab.active").text()).toBe("Account details");
    expect(e.accountPlans).not.toHaveBeenCalled();
  });
});

// --- Plan & Billing -----------------------------------------------------------------------------------

const plan = (over: Partial<Plan> = {}): Plan => ({
  id: "p-2", version: 2, status: "active", effectiveStatus: "active", retentionDays: 30, interval: "monthly",
  centsPeriod: 2240, centsMonth: 2240, currency: "EUR", trialEndsAt: null, createdAt: "2026-11-07T10:00:00Z",
  createdBy: "dealer@north.example", lines: linesOf({ lines: [{ resolution: "4mp", recording: "motion", quantity: 4 }] }),
  cameras: 4, ...over,
});
const PLANS: AccountPlans = {
  account: { id: "a-1", name: "Acme" }, dealer: { name: "North", email: "sales@north.example", phone: null },
  adapters: [{ id: "ad-1", thingName: "adapter-01", name: "Hall", activatedAt: "2026-11-01T10:00:00Z", site: "Home",
               plan: plan(), history: [plan(), plan({ id: "p-1", version: 1, status: "trial", centsPeriod: 0, centsMonth: 0,
                                                      lines: linesOf(null), cameras: 0, createdBy: null })] }],
  total: { adapters: 1, centsMonth: 2240, cameras: 4, currency: "EUR" },
  next: { period: "2026-11", periodStart: "2026-11-01", periodEnd: "2026-12-01", billedAt: "2026-11-14T10:00:00Z",
          lines: [], cents: 0, currency: "EUR", invoiceId: null },
  invoices: [{ id: "i-1", periodStart: "2026-10-01", periodEnd: "2026-11-01", cents: 2240, currency: "EUR", status: "issued",
               provider: "fake", createdAt: "2026-11-01T00:00:00Z", customer: { id: "a-1", name: "Acme" },
               dealer: { id: "d-1", name: "North" }, lines: [] }],
  truncated: false,
};

describe("Plan & Billing", () => {
  it("shows each adapter's plan read-only with its history, the total and the invoices, as a demo", async () => {
    e.me.mockReturnValue(ok(me(account("a-1", "Acme", "customer"))));
    e.accountPlans.mockReturnValue(ok(PLANS));
    const w = await mountAccount("/account/plans");
    expect(e.accountPlans).toHaveBeenCalledWith("a-1");
    expect(w.find("[data-test=demo-banner]").text()).toBe("Demo billing: no payment is taken.");
    expect(w.find("[data-test=adapter-plan] h3").text()).toBe("Home · Hall — adapter-01");
    expect(w.find("[data-test=plan-state]").text()).toBe(`Active · ${EUR(2240)} a month`);
    expect(w.find(".plan").text()).toContain("4 × 4MP motion");
    expect(w.find("[data-test=version-2]").text()).toContain("4MP motion: 0 → 4");
    expect(w.find("[data-test=version-1]").text()).toContain("started");
    expect(w.find("[data-test=total]").text()).toContain(`1 adapter, ${EUR(2240)} a month`);
    expect(w.find("[data-test=dealer]").text()).toContain("North (sales@north.example)");
    expect(w.find("[data-test=nothing-to-pay]").exists()).toBe(true);
    expect(w.findAll("[data-test=invoice]")).toHaveLength(1);
    expect(w.findAll("input, select")).toHaveLength(0);                   // read-only: no plan control, no card field
  });

  it("asks for the account picked when the person holds several", async () => {
    e.me.mockReturnValue(ok(me(account("a-1", "Acme", "customer"), account("a-2", "Beta", "customer"))));
    e.accountPlans.mockReturnValue(ok(PLANS));
    const w = await mountAccount("/account/plans");
    await w.find("[data-test=account-picker]").setValue("a-2");
    await flushPromises();
    expect(e.accountPlans.mock.calls.map(c => c[0])).toEqual(["a-1", "a-2"]);
  });

  it("shows the directory's refusal", async () => {
    e.me.mockReturnValue(ok(me(account("a-1", "Acme", "customer"))));
    e.accountPlans.mockReturnValue(refused(404, "no such account"));
    const w = await mountAccount("/account/plans");
    expect(w.find("[data-test=plans-error]").text()).toBe("no such account");
  });
});

// --- Additional users ---------------------------------------------------------------------------------

const person = (id: string, email: string, role: AccountUsers["people"][number]["role"], over = {}) => ({
  id, email, name: null, status: "active" as const, lastSignInAt: "2026-11-02T10:00:00Z", role, addedAt: "2026-11-01T10:00:00Z",
  cameras: [] as string[], you: false, ...over,
});
const USERS = (viewer: AccountUsers["role"] = "customer"): AccountUsers => ({
  account: { id: "a-1", name: "Acme" }, role: viewer, things: ["adapter-01"],
  people: [
    person("u-0", "anna@example.test", "customer", { you: viewer === "customer" }),
    person("u-1", "sam@example.test", "customer_admin", { you: viewer === "customer_admin" }),
    person("u-2", "ben@example.test", "customer_user", { cameras: ["cam-03"] }),
    person("u-3", "carl@example.test", "customer_user", { status: "invited", lastSignInAt: null }),
  ],
  cameras: [{ id: "cam-03", name: "Hall", thing: "adapter-01" }, { id: "cam-04", name: null, thing: "adapter-01" }],
});

async function mountUsers(viewer: AccountUsers["role"] = "customer", users = USERS(viewer)) {
  e.me.mockReturnValue(ok(me(account("a-1", "Acme", viewer === "customer_admin" ? "customer_admin" : "customer"))));
  e.accountUsers.mockReturnValue(ok(users));
  return mountAccount("/account/users");
}

describe("Additional users", () => {
  it("lists the users with what each may see, the pending invites and the holder", async () => {
    const w = await mountUsers();
    expect(w.findAll("[data-test=user]").map(r => r.findAll("td").slice(0, 2).map(td => td.text()))).toEqual([
      ["sam@example.test", "Super Admin"], ["ben@example.test", "1 camera shared: Hall"]]);
    expect(w.findAll("[data-test=pending]").map(r => r.find("td").text())).toEqual(["carl@example.test"]);
    expect(w.find("[data-test=holder]").text()).toContain("anna@example.test (you)");
  });

  it("offers no change of your own access", async () => {
    const w = await mountUsers("customer_admin");
    const sam = w.findAll("[data-test=user]").find(r => r.text().includes("sam@example.test"))!;
    expect(sam.text()).toContain("(you)");
    expect(sam.findAll("button")).toHaveLength(0);
    expect(w.find("[data-test=holder]").findAll("button")).toHaveLength(0);   // only the holder hands the account over
  });

  it("says so when nobody has been added and nobody is invited", async () => {
    const w = await mountUsers("customer", { ...USERS(), people: [USERS().people[0]] });
    expect(w.find("[data-test=no-users]").text()).toBe("You haven't shared cameras with anyone yet.");
    expect(w.find("[data-test=no-pending]").text()).toBe("No pending invites.");
  });

  it("adds someone with the cameras ticked; an address with an account has access at once", async () => {
    e.addAccountUser.mockReturnValue(ok({ userId: "u-9", email: "dora@example.test", status: "active", new: false }));
    const w = await mountUsers();
    await w.find("input[data-test=new-email], [data-test=new-email] input").setValue(" dora@example.test ");
    await w.find('[data-test=new-cameras] input[value="cam-04"]').setValue(true);
    await w.find("form.add").trigger("submit");
    await flushPromises();
    expect(e.addAccountUser).toHaveBeenCalledWith("a-1", { email: "dora@example.test", superAdmin: false, cameras: ["cam-04"] });
    expect(w.find("[data-test=message]").text()).toContain("dora@example.test has access now");
    expect(e.accountUsers).toHaveBeenCalledTimes(2);                       // the list again
    expect(e.me).toHaveBeenCalledTimes(2);                                 // and the viewer's own access
  });

  it("a Super Admin shares no cameras, and a new address is invited", async () => {
    e.addAccountUser.mockReturnValue(ok({ userId: "u-9", email: "eve@example.test", status: "invited", new: true }));
    const w = await mountUsers();
    await w.find("input[data-test=new-email], [data-test=new-email] input").setValue("eve@example.test");
    await w.find('[data-test=new-cameras] input[value="cam-03"]').setValue(true);
    w.findAllComponents(ToggleSwitch)[0].vm.$emit("update:modelValue", true);
    await flushPromises();
    expect(w.find("[data-test=new-cameras]").exists()).toBe(false);
    await w.find("form.add").trigger("submit");
    await flushPromises();
    expect(e.addAccountUser).toHaveBeenCalledWith("a-1", { email: "eve@example.test", superAdmin: true, cameras: [] });
    expect(w.find("[data-test=message]").text()).toContain("An invitation is on its way to eve@example.test");
  });

  it("shows a refusal, such as an address that can't be added", async () => {
    e.addAccountUser.mockReturnValue(refused(409, "can't add this address"));
    const w = await mountUsers();
    await w.find("input[data-test=new-email], [data-test=new-email] input").setValue("seller@north.example");
    await w.find("form.add").trigger("submit");
    await flushPromises();
    expect(w.find("[data-test=users-error]").text()).toBe("can't add this address");
  });

  it("edits a user into a Super Admin, which shares no cameras", async () => {
    e.updateAccountUser.mockReturnValue(ok({ userId: "u-2", role: "customer_admin", cameras: [] }));
    const w = await mountUsers();
    const ben = w.findAll("[data-test=user]").find(r => r.text().includes("ben@example.test"))!;
    await ben.findAll("button").find(b => b.text() === "Edit")!.trigger("click");
    expect((w.find('[data-test=editing] input[value="cam-03"]').element as HTMLInputElement).checked).toBe(true);
    w.find("[data-test=editing]").findComponent(ToggleSwitch).vm.$emit("update:modelValue", true);
    await flushPromises();
    await w.find("[data-test=editing]").findAll("button").find(b => b.text() === "Save")!.trigger("click");
    await flushPromises();
    expect(e.updateAccountUser).toHaveBeenCalledWith("a-1", "u-2", { superAdmin: true, cameras: [] });
  });

  it("removes someone after asking, and says when their sign-in couldn't be deleted", async () => {
    e.removeAccountUser.mockReturnValue(ok({ userId: "u-2", deleted: true, cognitoError: "their sign-in wasn't deleted (X)" }));
    const w = await mountUsers();
    const ben = () => w.findAll("[data-test=user]").find(r => r.text().includes("ben@example.test"))!;
    await ben().findAll("button").find(b => b.text() === "Remove")!.trigger("click");
    expect(e.removeAccountUser).not.toHaveBeenCalled();
    expect(ben().text()).toContain("Remove ben@example.test?");
    await ben().findAll("button").find(b => b.text() === "Remove")!.trigger("click");
    await flushPromises();
    expect(e.removeAccountUser).toHaveBeenCalledWith("a-1", "u-2");
    expect(w.find("[data-test=warning]").text()).toContain("their sign-in wasn't deleted");
  });

  it("resends an invitation, and cancels one after asking", async () => {
    e.accountInvite.mockReturnValue(ok({ userId: "u-3" }));
    const w = await mountUsers();
    const carl = () => w.find("[data-test=pending]");
    await carl().findAll("button").find(b => b.text() === "Resend")!.trigger("click");
    await flushPromises();
    expect(e.accountInvite).toHaveBeenLastCalledWith("a-1", "u-3", "resend");
    await carl().findAll("button").find(b => b.text() === "Cancel")!.trigger("click");
    await carl().findAll("button").find(b => b.text() === "Cancel invitation")!.trigger("click");
    await flushPromises();
    expect(e.accountInvite).toHaveBeenLastCalledWith("a-1", "u-3", "cancel");
  });

  it("the holder hands the account over to someone who has signed in", async () => {
    e.handOverAccount.mockReturnValue(ok({ accountId: "a-1", holder: { id: "u-2", email: "ben@example.test" } }));
    const w = await mountUsers();
    await button(w, "Hand the account over…").trigger("click");
    const options = w.findAll("[data-test=heir] option").map(o => o.text());
    expect(options).toEqual(["Choose someone", "sam@example.test", "ben@example.test"]);    // carl hasn't signed in
    await w.find("[data-test=heir]").setValue("u-2");
    await button(w, "Hand over").trigger("click");
    await flushPromises();
    expect(e.handOverAccount).toHaveBeenCalledWith("a-1", "u-2");
    expect(w.find("[data-test=message]").text()).toContain("ben@example.test holds this account now");
    expect(e.me).toHaveBeenCalledTimes(2);                                 // the tabs follow: no Plan & Billing now
  });
});
