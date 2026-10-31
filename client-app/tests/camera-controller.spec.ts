// The player logic ported from client/index.html must behave as it did there. Each test names
// the FoundAndFixed.md entry whose fix it keeps.
import Hls from "hls.js";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiResult } from "../src/api/client";
import type { Camera, HlsSession } from "../src/api/types";
import { CameraController } from "../src/player/camera-controller";

class FakeHls {
  static isSupported = () => true;
  static Events = Hls.Events;
  static ErrorTypes = Hls.ErrorTypes;
  static ErrorDetails = Hls.ErrorDetails;
  static all: FakeHls[] = [];
  static get last(): FakeHls { return FakeHls.all[FakeHls.all.length - 1]; }
  handlers: Record<string, (e: string, d: unknown) => void> = {};
  loadSource = vi.fn();
  attachMedia = vi.fn();
  destroy = vi.fn();
  startLoad = vi.fn();
  recoverMediaError = vi.fn();
  constructor() { FakeHls.all.push(this); }
  on(ev: string, fn: (e: string, d: unknown) => void) { this.handlers[ev] = fn; }
  emit(ev: string, data: object = {}) { this.handlers[ev]?.(ev, data); }
}

const camera = (over: Partial<Camera> = {}): Camera => ({
  id: "cam-03", mode: "passthrough", hasIrControl: false, recordingMode: "manual",
  supportsDetection: true, audioCapable: true, audioEnabled: false, outageBufferSec: 0,
  videoCodec: "h264", liveView: "sub", liveVideoCodec: "h264", ...over,
});

type Reply = ApiResult<HlsSession>;
const playable = (expires_in = 3600): Reply =>
  ({ ok: true, status: 200, data: { url: "https://kvs.example/session.m3u8", expires_in } });
const notLive: Reply = { ok: false, status: 503, data: { error: "No fragments found" } };

function fakeVideo() {
  const v = document.createElement("video");
  let t = 0;
  let paused = false;
  v.load = vi.fn();
  v.play = vi.fn(() => Promise.resolve());
  Object.defineProperty(v, "currentTime", { get: () => t, set: (x: number) => { t = x; }, configurable: true });
  Object.defineProperty(v, "paused", { get: () => paused, configurable: true });
  return Object.assign(v, { setPaused: (p: boolean) => { paused = p; } });
}

function setup(over: Partial<Camera> = {}) {
  vi.useFakeTimers();
  vi.setSystemTime(1_000_000);
  const visible = { value: true };
  const api = {
    hls: vi.fn(async (_c: string, _v: "live" | "recorded", _s?: string): Promise<Reply> => notLive),
    cmd: vi.fn(async (): Promise<ApiResult> => ({ ok: true, status: 200, data: {} })),
  };
  const c = new CameraController(camera(over), {
    api, hls: FakeHls as unknown as typeof Hls, isVisible: () => visible.value,
    now: () => Date.now(), perfNow: () => Date.now(),
  });
  const v = fakeVideo();
  c.attach(v);
  return { c, api, v, visible };
}

const tick = (ms = 0) => vi.advanceTimersByTimeAsync(ms);
const cmdCalls = (api: ReturnType<typeof setup>["api"], action: string) =>
  api.cmd.mock.calls.filter((a: unknown[]) => a[1] === action);

afterEach(() => {
  vi.useRealTimers();
  FakeHls.all = [];
});

describe("Start and the start window", () => {
  it("waits through 503s after Start, asking with since=, then plays (#43, #66)", async () => {
    const { c, api, v } = setup();
    await c.command("start");
    expect(api.cmd).toHaveBeenCalledWith("cam-03", "start");
    expect(c.state.view).toBe("recorded");
    expect(c.state.overlay).toBe("Starting stream…\nThis can take a few seconds.");

    await tick(5000);
    expect(api.hls).toHaveBeenCalledWith("cam-03", "recorded", "5.0");
    expect(c.state.status).toBe("waiting for first video...");
    expect(c.state.overlay).toBe("Starting stream…\nWaiting for the first video from the camera.");

    await tick(3000);
    expect(api.hls).toHaveBeenCalledWith("cam-03", "recorded", "8.0");

    api.hls.mockResolvedValue(playable());
    await tick(3000);
    expect(FakeHls.all).toHaveLength(1);
    expect(FakeHls.last.loadSource).toHaveBeenCalledWith("https://kvs.example/session.m3u8");
    expect(FakeHls.last.attachMedia).toHaveBeenCalledWith(v);
    expect(c.state.status).toBe("recorded stream loaded");
    FakeHls.last.emit(Hls.Events.FRAG_BUFFERED);
    expect(c.state.overlay).toBeNull();
  });

  it("says the Pi didn't answer when the whole window passes without video (#61)", async () => {
    const { c } = setup();
    await c.command("start");
    await tick(44_000);
    expect(c.state.status).toBe("waiting for first video...");
    await tick(4_000);
    expect(c.state.status).toBe("no video arrived within 45 s");
    expect(c.state.overlay).toMatch(/^No video arrived within 45 s\.\n\nThe Pi didn't answer/);
  });

  it("outside the start window an error is shown with Press Start", async () => {
    const { c, api } = setup();
    api.hls.mockResolvedValue({ ok: false, status: 502, data: { error: "boom" } });
    await c.load();
    expect(api.hls).toHaveBeenCalledWith("cam-03", "recorded", undefined);
    expect(c.state.status).toBe("error: boom");
    expect(c.state.overlay).toBe("boom\n\nPress Start to begin.");
  });

  it("Stop tears the player down; Start while watching Live leaves the live view alone", async () => {
    const { c, api, v } = setup();
    await c.command("stop");
    expect(api.cmd).toHaveBeenCalledWith("cam-03", "stop");
    expect(v.load).toHaveBeenCalled();
    expect(c.state.view).toBeNull();
    expect(c.state.overlay).toMatch(/^Recording to the cloud stopped\./);

    await c.toggleLive();
    await c.command("start");
    expect(c.state.view).toBe("live");
    expect(c.state.overlay).toMatch(/^Starting live view…/);
  });
});

describe("the playing session", () => {
  it("offers sound only when the stream has it, and keeps the choice across a session refresh", async () => {
    const { c, api, v } = setup();
    v.setPaused(true);                                  // a paused player is never a stall
    api.hls.mockResolvedValue(playable(300));
    await c.load();
    expect(v.muted).toBe(true);
    expect(c.state.listenAvailable).toBe(false);
    FakeHls.last.emit(Hls.Events.MANIFEST_PARSED, { audioTracks: [{}] });
    expect(c.state.listenAvailable).toBe(true);
    c.setListen(true);
    expect(v.muted).toBe(false);

    // A KVS session URL dies at expires_in: a new session at 80% of it.
    await tick(239_999);
    expect(api.hls).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(api.hls).toHaveBeenCalledTimes(2);
    expect(v.muted).toBe(true);                         // every reload starts muted...
    expect(c.state.listen).toBe(true);
    FakeHls.last.emit(Hls.Events.MANIFEST_PARSED, { audioTracks: [{}] });
    expect(v.muted).toBe(false);                        // ...and the viewer's choice comes back
  });

  it("a short session is still refreshed no sooner than 30 s", async () => {
    const { c, api, v } = setup();
    v.setPaused(true);
    api.hls.mockResolvedValue(playable(10));
    await c.load();
    await tick(29_999);
    expect(api.hls).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(api.hls).toHaveBeenCalledTimes(2);
  });

  it("retries a fragment twice, then gets a new session; a playlist error goes straight to one (#66)", async () => {
    const { c, api } = setup();
    api.hls.mockResolvedValue(playable());
    await c.load();
    const hls = FakeHls.last;
    const frag = { fatal: true, type: Hls.ErrorTypes.NETWORK_ERROR, details: Hls.ErrorDetails.FRAG_LOAD_ERROR };
    hls.emit(Hls.Events.ERROR, { ...frag, fatal: false });
    hls.emit(Hls.Events.ERROR, frag);
    hls.emit(Hls.Events.ERROR, frag);
    expect(hls.startLoad).toHaveBeenCalledTimes(2);
    hls.emit(Hls.Events.ERROR, frag);
    expect(hls.startLoad).toHaveBeenCalledTimes(2);
    expect(c.state.overlay).toBe("Reconnecting…");
    await tick(1000);
    expect(api.hls).toHaveBeenCalledTimes(2);

    FakeHls.last.emit(Hls.Events.ERROR,
      { fatal: true, type: Hls.ErrorTypes.NETWORK_ERROR, details: Hls.ErrorDetails.LEVEL_LOAD_ERROR });
    expect(FakeHls.last.startLoad).not.toHaveBeenCalled();
    await tick(1000);
    expect(api.hls).toHaveBeenCalledTimes(3);

    FakeHls.last.emit(Hls.Events.ERROR,
      { fatal: true, type: Hls.ErrorTypes.MEDIA_ERROR, details: Hls.ErrorDetails.BUFFER_STALLED_ERROR });
    expect(FakeHls.last.recoverMediaError).toHaveBeenCalled();
  });

  it("explains a codec the browser refuses instead of retrying, and raises the H.265 note", async () => {
    const { c, api } = setup({ videoCodec: "h265" });
    api.hls.mockResolvedValue(playable());
    await c.load();
    FakeHls.last.emit(Hls.Events.ERROR, {
      fatal: true, type: Hls.ErrorTypes.MEDIA_ERROR,
      details: Hls.ErrorDetails.MANIFEST_INCOMPATIBLE_CODECS_ERROR, reason: "no decoder for hvc1.1.6.L90.0",
    });
    expect(c.state.overlay).toBe("This browser can't play this camera's H.265 video.\n\nSee the note at the top of the page.");
    expect(c.state.hevcHelp).toBe(true);
    expect(FakeHls.last.recoverMediaError).not.toHaveBeenCalled();
  });
});

describe("stalls (#66)", () => {
  it("reloads a picture frozen for 20 s; the third stall in a row is reported, not reloaded", async () => {
    const { c, api } = setup({ videoCodec: "h265" });
    api.hls.mockResolvedValue(playable());
    await c.load();
    await tick(24_999);
    expect(api.hls).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(api.hls).toHaveBeenCalledTimes(2);
    await tick(25_000);
    expect(api.hls).toHaveBeenCalledTimes(3);
    await tick(25_000);
    expect(api.hls).toHaveBeenCalledTimes(3);
    expect(c.state.status).toBe("video arrives but doesn't play");
    expect(c.state.overlay).toContain("This browser may not decode this camera's H.265 at this size.");
  });

  it("a hidden tab or a paused player is not a stall; moving video resets the count", async () => {
    const { c, api, v, visible } = setup();
    api.hls.mockResolvedValue(playable());
    await c.load();
    visible.value = false;
    await tick(60_000);
    visible.value = true;
    v.setPaused(true);
    await tick(60_000);
    v.setPaused(false);
    for (let i = 0; i < 12; i++) { v.currentTime += 5; await tick(5000); }
    expect(api.hls).toHaveBeenCalledTimes(1);
  });
});

describe("Live", () => {
  it("sends live, keeps it alive every 2 min while visible, and End live sends live-stop", async () => {
    const { c, api, visible } = setup();
    await c.toggleLive();
    expect(cmdCalls(api, "live")).toHaveLength(1);
    expect(c.state.view).toBe("live");
    expect(c.state.overlay).toMatch(/^Starting live view…/);
    await tick(5000);
    expect(api.hls).toHaveBeenCalledWith("cam-03", "live", "5.0");

    await tick(115_000);
    expect(cmdCalls(api, "live")).toHaveLength(2);
    visible.value = false;
    await tick(120_000);
    expect(cmdCalls(api, "live")).toHaveLength(2);
    visible.value = true;
    await tick(120_000);
    expect(cmdCalls(api, "live")).toHaveLength(3);

    await c.toggleLive();
    expect(cmdCalls(api, "live-stop")).toHaveLength(1);
    expect(c.state.view).toBeNull();
    expect(c.state.overlay).toMatch(/^Live view ended\./);
    await tick(600_000);
    expect(cmdCalls(api, "live")).toHaveLength(3);
  });

  it("a refused live says why and leaves Live off", async () => {
    const { c, api } = setup();
    api.cmd.mockResolvedValueOnce({ ok: false, status: 409, data: { error: "camera unregistered" } });
    await c.toggleLive();
    expect(c.state.view).toBeNull();
    expect(c.state.overlay).toBe("Could not start the live view: camera unregistered");
  });

  it("back on the tab: renews Live, or starts it over once the Pi's deadline has passed", async () => {
    const { c, api, visible } = setup();
    await c.toggleLive();
    await tick(1000);
    c.onVisible();
    await tick(0);
    expect(cmdCalls(api, "live")).toHaveLength(2);

    visible.value = false;
    await tick(300_000);
    visible.value = true;
    c.onVisible();
    await tick(0);
    expect(cmdCalls(api, "live")).toHaveLength(3);
    expect(cmdCalls(api, "live-stop")).toHaveLength(0);
    expect(c.state.view).toBe("live");
    expect(c.state.overlay).toMatch(/^Starting live view…/);
  });

  it("leaving the page sends live-stop with keepalive; dispose ends Live and every timer", async () => {
    const { c, api } = setup();
    await c.toggleLive();
    c.onPageHide();
    expect(api.cmd).toHaveBeenLastCalledWith("cam-03", "live-stop", true);
    c.dispose();
    expect(cmdCalls(api, "live-stop")).toHaveLength(2);
    const before = api.cmd.mock.calls.length + api.hls.mock.calls.length;
    await tick(600_000);
    expect(api.cmd.mock.calls.length + api.hls.mock.calls.length).toBe(before);
  });
});

describe("the archive check (#83)", () => {
  it("follows KVS: 200 = footage arriving, 503 = none, anything else changes nothing", async () => {
    const { c, api } = setup();
    api.hls.mockResolvedValueOnce(playable());
    c.startArchive();
    await tick(0);
    expect(c.state.archiving).toBe(true);
    api.hls.mockResolvedValueOnce({ ok: false, status: 500, data: {} });
    await tick(30_000);
    expect(c.state.archiving).toBe(true);
    api.hls.mockResolvedValueOnce(notLive);
    await tick(30_000);
    expect(c.state.archiving).toBe(false);
  });

  it("after this page's Stop, KVS's lingering 'live' is ignored for 35 s", async () => {
    const { c, api } = setup();
    api.hls.mockResolvedValue(playable());
    c.startArchive();
    await tick(1000);
    await c.command("stop");
    expect(c.state.archiving).toBe(false);
    await tick(29_000);                                 // the poll at 30 s: 29 s after Stop
    expect(c.state.archiving).toBe(false);
    await tick(30_000);                                 // 59 s after Stop
    expect(c.state.archiving).toBe(true);
  });

  it("polls every 5 s with since= through the start window, then every 30 s", async () => {
    const { c, api } = setup();
    c.detach();                                         // no player: only the archive asks /hls
    await c.command("start");
    expect(api.hls.mock.calls).toEqual([["cam-03", "recorded", "1.0"]]);
    await tick(5000);
    expect(api.hls.mock.lastCall).toEqual(["cam-03", "recorded", "5.0"]);
    await tick(40_000);                                 // 45 s: the window has closed
    expect(api.hls.mock.calls).toHaveLength(10);        // at 0, 5, ..., 45 s
    expect(api.hls.mock.lastCall?.[2]).toBeUndefined();
    await tick(29_999);
    expect(api.hls.mock.calls).toHaveLength(10);
    await tick(1);
    expect(api.hls.mock.calls).toHaveLength(11);
  });

  it("drops an answer a later check has overtaken; asks nothing while hidden", async () => {
    const { c, api, visible } = setup();
    let answerSlow!: (r: Reply) => void;
    api.hls.mockImplementationOnce(() => new Promise<Reply>(res => { answerSlow = res; }));
    api.hls.mockResolvedValueOnce(notLive);
    void c.checkArchive();
    await c.checkArchive();
    answerSlow(playable());
    await tick(0);
    expect(c.state.archiving).toBe(false);

    visible.value = false;
    const n = api.hls.mock.calls.length;
    await tick(90_000);
    expect(api.hls.mock.calls.length).toBe(n);
  });
});
