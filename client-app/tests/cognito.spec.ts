import { describe, expect, it, vi } from "vitest";
import { AuthError, CognitoSession, NewPasswordRequired, jwtClaims } from "../src/auth/cognito";
import { COGNITO_CLIENT_ID } from "../src/config";

function b64url(s: string): string {
  const bytes = new TextEncoder().encode(s);
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
const jwt = (claims: object) => `${b64url('{"alg":"none"}')}.${b64url(JSON.stringify(claims))}.sig`;

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, String(v)),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    get length() { return m.size; },
  };
}

const result = (id: string, refresh?: string) => ({
  AuthenticationResult: { IdToken: id, AccessToken: "access", ExpiresIn: 3600, ...(refresh ? { RefreshToken: refresh } : {}) },
});

/** A Cognito that answers with `replies` in order and records each request's body and action. */
function cognito(...replies: Array<[number, object] | Error>) {
  const bodies: Array<Record<string, any>> = [];
  const targets: string[] = [];
  const fetch = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
    bodies.push(JSON.parse(String(init?.body)));
    targets.push(String((init?.headers as Record<string, string>)["X-Amz-Target"]).split(".")[1]);
    const next = replies.shift() ?? [500, {}];
    if (next instanceof Error) throw next;
    return new Response(JSON.stringify(next[1]), { status: next[0] });
  });
  return { fetch: fetch as unknown as typeof globalThis.fetch, bodies, targets, calls: fetch };
}

function session(c: ReturnType<typeof cognito>, storage: Storage | null = memoryStorage(), clock = { t: 0 }) {
  return { s: new CognitoSession({ fetch: c.fetch, storage: () => storage, now: () => clock.t }), storage, clock };
}

describe("CognitoSession", () => {
  it("signs in with USER_PASSWORD_AUTH and keeps only the refresh token in storage", async () => {
    const c = cognito([200, result(jwt({ "cognito:username": "vlad" }), "R1")]);
    const { s, storage } = session(c);
    await s.signIn("vlad", "pw");
    expect(c.bodies[0]).toEqual({
      AuthFlow: "USER_PASSWORD_AUTH", ClientId: COGNITO_CLIENT_ID,
      AuthParameters: { USERNAME: "vlad", PASSWORD: "pw" },
    });
    expect(s.signedIn).toBe(true);
    expect(s.username).toBe("vlad");
    expect(storage!.getItem("vsz.refreshToken")).toBe("R1");
    expect(await s.idToken()).toContain(".");
    expect(c.calls).toHaveBeenCalledTimes(1);           // a fresh token needs no refresh
  });

  it("renews the ID token two minutes before it expires, keeping the refresh token", async () => {
    const c = cognito([200, result("ID1", "R1")], [200, result("ID2")]);
    const { s, clock, storage } = session(c);
    await s.signIn("u", "p");
    clock.t = 3600_000 - 120_001;
    expect(await s.idToken()).toBe("ID1");
    clock.t = 3600_000 - 119_000;
    expect(await s.idToken()).toBe("ID2");
    expect(c.bodies[1]).toEqual({
      AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: COGNITO_CLIENT_ID, AuthParameters: { REFRESH_TOKEN: "R1" },
    });
    expect(storage!.getItem("vsz.refreshToken")).toBe("R1");
  });

  it("shares one refresh between concurrent callers", async () => {
    const c = cognito([200, result("ID1", "R1")], [200, result("ID2")]);
    const { s, clock } = session(c);
    await s.signIn("u", "p");
    clock.t = 3600_000;
    expect(await Promise.all([s.idToken(), s.idToken(), s.idToken()])).toEqual(["ID2", "ID2", "ID2"]);
    expect(c.calls).toHaveBeenCalledTimes(2);
  });

  it("a refused refresh signs out and tells the listeners", async () => {
    const c = cognito([200, result("ID1", "R1")], [400, { message: "Refresh Token has expired" }]);
    const { s, clock, storage } = session(c);
    await s.signIn("u", "p");
    const changed = vi.fn();
    s.onChange(changed);
    clock.t = 3600_000;
    await expect(s.idToken()).rejects.toThrow(AuthError);
    expect(s.signedIn).toBe(false);
    expect(storage!.getItem("vsz.refreshToken")).toBeNull();
    expect(changed).toHaveBeenCalled();
  });

  it("a network failure during a refresh doesn't sign out", async () => {
    const c = cognito([200, result("ID1", "R1")], new TypeError("Failed to fetch"));
    const { s, clock } = session(c);
    await s.signIn("u", "p");
    clock.t = 3600_000;
    await expect(s.idToken()).rejects.toThrow(TypeError);
    expect(s.signedIn).toBe(true);
  });

  it("restores a reloaded tab from the stored refresh token", async () => {
    const storage = memoryStorage();
    storage.setItem("vsz.refreshToken", "R9");
    const c = cognito([200, result("ID9")]);
    const { s } = session(c, storage);
    expect(await s.restore()).toBe(true);
    expect(c.bodies[0].AuthParameters).toEqual({ REFRESH_TOKEN: "R9" });
    expect(await s.idToken()).toBe("ID9");
  });

  it("restore without a stored token asks nothing; with a dead one it clears it", async () => {
    const none = cognito();
    expect(await session(none).s.restore()).toBe(false);
    expect(none.calls).not.toHaveBeenCalled();

    const storage = memoryStorage();
    storage.setItem("vsz.refreshToken", "dead");
    const dead = cognito([400, { message: "Invalid Refresh Token" }]);
    const { s } = session(dead, storage);
    expect(await s.restore()).toBe(false);
    expect(s.signedIn).toBe(false);
    expect(storage.getItem("vsz.refreshToken")).toBeNull();
  });

  it("explains a refused sign-in, in the trigger's own words, and a challenge it can't answer", async () => {
    const wrong = session(cognito([400, { message: "Incorrect username or password." }])).s;
    await expect(wrong.signIn("u", "bad")).rejects.toThrow("Incorrect username or password.");
    const refused = session(cognito([400, { __type: "UserLambdaValidationException",
      message: "PreTokenGeneration failed with error This account can't sign in. If it should, ask whoever manages your account." }])).s;
    await expect(refused.signIn("u", "p")).rejects.toThrow(/^This account can't sign in\./);
    const mfa = session(cognito([200, { ChallengeName: "SOFTWARE_TOKEN_MFA", Session: "x" }])).s;
    await expect(mfa.signIn("u", "p")).rejects.toThrow(/SOFTWARE_TOKEN_MFA/);
    expect(mfa.signedIn).toBe(false);
  });

  it("works when storage is blocked (private browsing)", async () => {
    const blocked = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); },
                      removeItem() { throw new Error("denied"); } } as unknown as Storage;
    const { s } = session(cognito([200, result("ID1", "R1")]), blocked);
    await s.signIn("u", "p");
    expect(await s.idToken()).toBe("ID1");
    expect(await s.restore()).toBe(false);
  });
});

describe("invited people and their own account (DEALER-PANEL-PLAN.md D17, D18)", () => {
  it("asks an invited person for their own password, answering with Cognito's user name", async () => {
    const c = cognito(
      [200, { ChallengeName: "NEW_PASSWORD_REQUIRED", Session: "S1", ChallengeParameters: { USER_ID_FOR_SRP: "uuid-1" } }],
      [200, result(jwt({ email: "a@example.test", vsz_role: "customer" }), "R1")]);
    const { s } = session(c);
    await expect(s.signIn("a@example.test", "Temp1234")).rejects.toBeInstanceOf(NewPasswordRequired);
    expect(s.awaitingNewPassword).toBe(true);
    expect(s.signedIn).toBe(false);
    await s.completeNewPassword("MyOwnPass12");
    expect(c.targets).toEqual(["InitiateAuth", "RespondToAuthChallenge"]);
    expect(c.bodies[1]).toEqual({
      ChallengeName: "NEW_PASSWORD_REQUIRED", ClientId: COGNITO_CLIENT_ID, Session: "S1",
      ChallengeResponses: { USERNAME: "uuid-1", NEW_PASSWORD: "MyOwnPass12" },
    });
    expect(s.signedIn).toBe(true);
    expect(s.username).toBe("a@example.test");
    expect(s.claims.vsz_role).toBe("customer");
    expect(s.awaitingNewPassword).toBe(false);
  });

  it("refuses a new password with no challenge waiting", async () => {
    const c = cognito();
    const { s } = session(c);
    await expect(s.completeNewPassword("x")).rejects.toThrow(/Sign in again/);
    expect(c.calls).not.toHaveBeenCalled();
  });

  it("resets a forgotten password with the emailed code", async () => {
    const c = cognito([200, {}], [200, {}]);
    const { s } = session(c);
    await s.forgotPassword("a@example.test");
    await s.confirmForgotPassword("a@example.test", "123456", "NewPass1234");
    expect(c.targets).toEqual(["ForgotPassword", "ConfirmForgotPassword"]);
    expect(c.bodies).toEqual([
      { ClientId: COGNITO_CLIENT_ID, Username: "a@example.test" },
      { ClientId: COGNITO_CLIENT_ID, Username: "a@example.test", ConfirmationCode: "123456", Password: "NewPass1234" },
    ]);
  });

  it("changes the password and the address with the access token, renewing once the address is confirmed", async () => {
    const c = cognito([200, result(jwt({ email: "old@example.test" }), "R1")], [200, {}], [200, {}], [200, {}],
                      [200, result(jwt({ email: "new@example.test" }))]);
    const { s } = session(c);
    await s.signIn("old@example.test", "p");
    await s.changePassword("p", "NewPass1234");
    await s.changeEmail("new@example.test");
    expect(s.username).toBe("old@example.test");            // until it is confirmed
    await s.confirmEmail("654321");
    expect(c.targets).toEqual(["InitiateAuth", "ChangePassword", "UpdateUserAttributes", "VerifyUserAttribute", "InitiateAuth"]);
    expect(c.bodies[1]).toEqual({ AccessToken: "access", PreviousPassword: "p", ProposedPassword: "NewPass1234" });
    expect(c.bodies[2]).toEqual({ AccessToken: "access", UserAttributes: [{ Name: "email", Value: "new@example.test" }] });
    expect(c.bodies[3]).toEqual({ AccessToken: "access", AttributeName: "email", Code: "654321" });
    expect(c.bodies[4].AuthFlow).toBe("REFRESH_TOKEN_AUTH");
    expect(s.username).toBe("new@example.test");
  });

  it("renews an expiring access token before using it", async () => {
    const c = cognito([200, result("ID1", "R1")],
                      [200, { AuthenticationResult: { IdToken: "ID2", AccessToken: "access2", ExpiresIn: 300 } }], [200, {}]);
    const { s, clock } = session(c);
    await s.signIn("u", "p");
    clock.t = 3600_000;
    await s.changePassword("p", "q");
    expect(c.targets).toEqual(["InitiateAuth", "InitiateAuth", "ChangePassword"]);
    expect(c.bodies[2].AccessToken).toBe("access2");
  });

  it("forgets a waiting challenge on sign-out", async () => {
    const c = cognito([200, { ChallengeName: "NEW_PASSWORD_REQUIRED", Session: "S1" }]);
    const { s } = session(c);
    await expect(s.signIn("a@example.test", "t")).rejects.toBeInstanceOf(AuthError);
    s.signOut();
    expect(s.awaitingNewPassword).toBe(false);
  });
});

describe("jwtClaims", () => {
  it("decodes UTF-8 claims and never throws", () => {
    expect(jwtClaims(jwt({ name: "Zdravković" })).name).toBe("Zdravković");
    expect(jwtClaims("garbage")).toEqual({});
  });
});
