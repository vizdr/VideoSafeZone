import { describe, expect, it, vi } from "vitest";
import { ApiClient, errorText } from "../src/api/client";
import { makeEndpoints } from "../src/api/endpoints";

/** An API Gateway that answers with `replies` in order and records every request. */
function gateway(...replies: Array<[number, string]>) {
  const requests: Array<{ url: string; init: RequestInit }> = [];
  const fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    requests.push({ url: String(url), init: init ?? {} });
    const [status, body] = replies.shift() ?? [200, "{}"];
    return new Response(body, { status });
  });
  return { fetch: fetch as unknown as typeof globalThis.fetch, requests };
}

function client(g: ReturnType<typeof gateway>, tokens = ["T1", "T2"], refresh = vi.fn(async () => {})) {
  let i = 0;
  const api = new ApiClient({ base: "https://api", token: async () => tokens[Math.min(i, tokens.length - 1)], refresh: async () => { i += 1; await refresh(); }, fetch: g.fetch });
  return { api, refresh };
}

describe("ApiClient", () => {
  it("sends the ID token; a GET has no body and no content type", async () => {
    const g = gateway([200, '{"cameras":[]}']);
    const r = await client(g).api.request("GET", "/cameras");
    expect(r).toEqual({ ok: true, status: 200, data: { cameras: [] } });
    expect(g.requests[0].url).toBe("https://api/cameras");
    expect(g.requests[0].init.headers).toEqual({ Authorization: "T1" });
    expect(g.requests[0].init.body).toBeUndefined();
  });

  it("a POST carries JSON, and keepalive when asked", async () => {
    const g = gateway([200, "{}"]);
    await client(g).api.request("POST", "/cmd", { action: "live-stop", camera: "cam-01" }, { keepalive: true });
    const { init } = g.requests[0];
    expect(init.headers).toEqual({ Authorization: "T1", "Content-Type": "application/json" });
    expect(JSON.parse(String(init.body))).toEqual({ action: "live-stop", camera: "cam-01" });
    expect(init.keepalive).toBe(true);
  });

  it("a 401 renews the token and retries once, never more", async () => {
    const g = gateway([401, '{"message":"Unauthorized"}'], [200, '{"ok":1}']);
    const { api, refresh } = client(g);
    const r = await api.request("GET", "/cameras");
    expect(r.status).toBe(200);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect((g.requests[1].init.headers as Record<string, string>).Authorization).toBe("T2");

    const g2 = gateway([401, "{}"], [401, '{"message":"Unauthorized"}']);
    const again = await client(g2).api.request("GET", "/cameras");
    expect(again.status).toBe(401);
    expect(g2.requests).toHaveLength(2);
  });

  it("a body that isn't JSON reads as empty", async () => {
    const r = await client(gateway([502, "<html>Bad Gateway</html>"])).api.request("GET", "/x");
    expect(r).toEqual({ ok: false, status: 502, data: {} });
    expect(errorText(r)).toBe("502");
  });

  it("errorText prefers the Lambda's error, then API Gateway's message", () => {
    expect(errorText({ ok: false, status: 409, data: { error: "unregistered", message: "m" } })).toBe("unregistered");
    expect(errorText({ ok: false, status: 403, data: { message: "Forbidden" } })).toBe("Forbidden");
  });
});

describe("endpoints", () => {
  it("build the routes and bodies the Lambdas expect", async () => {
    const g = gateway();
    const e = makeEndpoints(client(g).api);
    await e.hls("cam-03", "live", "5.0");
    await e.hls("cam 3", "recorded");
    await e.cmd("cam-03", "start");
    await e.setIrMode("cam-03", "AUTO");
    await e.setOutageBuffer("cam-03", 120);
    await e.listClips("cam-03", 10, "last");
    expect(g.requests.map(r => r.url)).toEqual([
      "https://api/hls?stream=cam-03&view=live&since=5.0",
      "https://api/hls?stream=cam%203&view=recorded",
      "https://api/cmd",
      "https://api/cmd",
      "https://api/cameras/outage-buffer",
      "https://api/clips?camera=cam-03&limit=10&page=last",
    ]);
    expect(JSON.parse(String(g.requests[2].init.body))).toEqual({ action: "start", camera: "cam-03" });
    expect(JSON.parse(String(g.requests[3].init.body))).toEqual({ action: "ir", camera: "cam-03", mode: "AUTO" });
    expect(JSON.parse(String(g.requests[4].init.body))).toEqual({ cameraId: "cam-03", outageBufferSec: 120 });
  });
});
