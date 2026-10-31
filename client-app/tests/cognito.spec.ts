import { describe, expect, it, vi } from "vitest";
import { AuthError, CognitoSession, jwtClaims } from "../src/auth/cognito";
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

/** A Cognito that answers with `replies` in order and records each request body. */
function cognito(...replies: Array<[number, object] | Error>) {
  const bodies: Array<Record<string, any>> = [];
  const fetch = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
    bodies.push(JSON.parse(String(init?.body)));
    const next = replies.shift() ?? [500, {}];
    if (next instanceof Error) throw next;
    return new Response(JSON.stringify(next[1]), { status: next[0] });
  });
  return { fetch: fetch as unknown as typeof globalThis.fetch, bodies, calls: fetch };
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

  it("explains a refused sign-in and a challenge this page can't answer", async () => {
    const wrong = session(cognito([400, { message: "Incorrect username or password." }])).s;
    await expect(wrong.signIn("u", "bad")).rejects.toThrow("Incorrect username or password.");
    const challenge = session(cognito([200, { ChallengeName: "NEW_PASSWORD_REQUIRED", Session: "x" }])).s;
    await expect(challenge.signIn("u", "p")).rejects.toThrow(/NEW_PASSWORD_REQUIRED/);
    expect(challenge.signedIn).toBe(false);
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

describe("jwtClaims", () => {
  it("decodes UTF-8 claims and never throws", () => {
    expect(jwtClaims(jwt({ name: "Zdravković" })).name).toBe("Zdravković");
    expect(jwtClaims("garbage")).toEqual({});
  });
});
