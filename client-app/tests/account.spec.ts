// The Account page's Account details (DEALER-PANEL-PLAN.md D18), with the session faked; and the
// rail's Account entry, which shows the page without the camera list.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthError } from "../src/auth/cognito";

const fake = vi.hoisted(() => ({
  session: {
    signedIn: true, username: "anna@example.test" as string | null, claims: { vsz_role: "customer" },
    onChange: () => () => {}, changePassword: vi.fn(), changeEmail: vi.fn(), confirmEmail: vi.fn(),
  },
}));
vi.mock("../src/api", () => ({ session: fake.session, endpoints: {} }));

import AppRail from "../src/components/AppRail.vue";
import { makeRouter } from "../src/router";
import { useAuthStore } from "../src/stores/auth";
import AccountView from "../src/views/AccountView.vue";

async function mountAccount() {
  const router = makeRouter(() => true);
  await router.push("/account");
  return mount(AccountView, { global: { plugins: [router, [OpenVue, { unstyled: true }]] }, attachTo: document.body });
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
