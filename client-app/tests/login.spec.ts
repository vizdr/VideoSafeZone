// The one sign-in page (DEALER-PANEL-PLAN.md D17): log in, an invited person's own password, a
// forgotten password reset with the emailed code -- with the session faked.
import { flushPromises, mount } from "@vue/test-utils";
import OpenVue from "openvue/config";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthError, NewPasswordRequired } from "../src/auth/cognito";

const fake = vi.hoisted(() => ({
  session: {
    signedIn: false, username: null as string | null, claims: {}, onChange: () => () => {},
    signIn: vi.fn(), completeNewPassword: vi.fn(), forgotPassword: vi.fn(), confirmForgotPassword: vi.fn(),
  },
}));
vi.mock("../src/api", () => ({ session: fake.session, endpoints: {} }));

import { makeRouter } from "../src/router";
import LoginView from "../src/views/LoginView.vue";

let signedIn = false;                                 // what the router's guard sees

async function mountLogin(path = "/login") {
  const router = makeRouter(() => signedIn);
  await router.push(path);
  const w = mount(LoginView, { global: { plugins: [router, [OpenVue, { unstyled: true }]] }, attachTo: document.body });
  return { w, router };
}
const fill = async (w: Awaited<ReturnType<typeof mountLogin>>["w"], id: string, value: string) =>
  w.find(`#${id}`).setValue(value);
const submit = async (w: Awaited<ReturnType<typeof mountLogin>>["w"]) => {
  await w.find("form").trigger("submit");
  await flushPromises();
};

beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  signedIn = false;
  document.body.innerHTML = "";
});

describe("the sign-in page", () => {
  it("says VideoSafeZone and asks for an email address and a password", async () => {
    const { w } = await mountLogin();
    expect(w.find("h1").text()).toBe("VideoSafeZone");
    expect(w.find("label[for=email]").text()).toBe("Email address");
    expect(w.text()).toContain("Forgotten password?");
    expect(w.find("button[type=submit]").text()).toBe("Log in");
  });

  it("logs in and goes back where the person came from", async () => {
    fake.session.signIn.mockImplementation(async () => { signedIn = true; });
    const { w, router } = await mountLogin("/login?next=/events");
    await fill(w, "email", " anna@example.test ");
    await fill(w, "password", "secret");
    await submit(w);
    expect(fake.session.signIn).toHaveBeenCalledWith("anna@example.test", "secret");
    expect(router.currentRoute.value.path).toBe("/events");
  });

  it("shows why a sign-in was refused", async () => {
    fake.session.signIn.mockRejectedValue(new AuthError("This account can't sign in."));
    const { w } = await mountLogin();
    await fill(w, "email", "x@example.test");
    await fill(w, "password", "p");
    await submit(w);
    expect(w.find("[role=alert]").text()).toBe("This account can't sign in.");
  });

  it("lets an invited person choose their own password", async () => {
    fake.session.signIn.mockRejectedValue(new NewPasswordRequired());
    fake.session.completeNewPassword.mockImplementation(async () => { signedIn = true; });
    const { w, router } = await mountLogin();
    await fill(w, "email", "new@example.test");
    await fill(w, "password", "Temp1234");
    await submit(w);
    expect(w.text()).toContain("Choose your own password");
    await fill(w, "new-password", "MyOwnPass12");
    await fill(w, "repeat", "MyOwnPass13");
    await submit(w);
    expect(w.find("[role=alert]").text()).toContain("aren't the same");
    expect(fake.session.completeNewPassword).not.toHaveBeenCalled();
    await fill(w, "repeat", "MyOwnPass12");
    await submit(w);
    expect(fake.session.completeNewPassword).toHaveBeenCalledWith("MyOwnPass12");
    expect(router.currentRoute.value.path).toBe("/live");
  });

  it("resets a forgotten password with the code from the email", async () => {
    fake.session.forgotPassword.mockResolvedValue(undefined);
    fake.session.confirmForgotPassword.mockResolvedValue(undefined);
    const { w } = await mountLogin();
    await w.find("button.link").trigger("click");
    await fill(w, "forgot-email", "anna@example.test");
    await submit(w);
    expect(fake.session.forgotPassword).toHaveBeenCalledWith("anna@example.test");
    expect(w.text()).toContain("a code is on its way");
    await fill(w, "code", "123456");
    await fill(w, "reset-password", "NewPass1234");
    await fill(w, "reset-repeat", "NewPass1234");
    await submit(w);
    expect(fake.session.confirmForgotPassword).toHaveBeenCalledWith("anna@example.test", "123456", "NewPass1234");
    expect(w.text()).toContain("Your password is changed");
    expect(w.find("label[for=email]").exists()).toBe(true);           // back at log in
  });
});
