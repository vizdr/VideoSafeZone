// One camera's player, live view and Start/Stop, ported from client/index.html without changing
// what it does: load(), watchStalls(), liveToggle(), cmd(), checkArchive(),
// scheduleSessionRefresh() and the Start window. Each carries a fix for something that went wrong
// (FoundAndFixed.md #43, #61, #66, #83), cited where it applies.
//
// Framework-free on purpose: the components only call its methods and show its state. State goes
// out as a fresh snapshot object on every change (subscribe()), so a Vue shallowRef follows it,
// while the hls.js instance and the timers stay private, outside any reactive proxy.
//
// hls.js is loaded with the first player that starts, not with the page: Settings and the archive
// check never need it, and it is most of the bundle.
import type Hls from "hls.js";
import type { ApiResult } from "../api/client";
import type { Camera, CommandAction, HlsSession } from "../api/types";
import { hevcSupport } from "./hevc";

// After Start or Live: how long "not live yet" (503) is expected rather than an error. 45 s covers
// a producer that dies on its first try and is restarted by systemd (RestartSec=5) plus a 3 s GOP.
export const START_WINDOW_MS = 45_000;
export const FIRST_LOAD_DELAY_MS = 5_000;
export const WAIT_RETRY_MS = 3_000;
export const STALL_SEC = 20;
export const STALL_CHECK_MS = 5_000;
export const LIVE_RESEND_MS = 120_000;
export const LIVE_DEADLINE_MS = 300_000;          // agent.py LIVE_DEADLINE_SEC
export const ARCHIVE_POLL_MS = 30_000;
export const ARCHIVE_POLL_FAST_MS = 5_000;
export const ARCHIVE_START_WINDOW_MS = 45_000;    // the Start window
export const ARCHIVE_STOP_GRACE_MS = 35_000;      // KVS's 30 s "live" window, plus slack

export const INITIAL_OVERLAY = "Press Live to watch, or Start to record to the cloud";

export type View = "live" | "recorded" | null;

export interface PlayerState {
  /** What the player shows: "live" (Live), "recorded" (after Start) or nothing. */
  view: View;
  /** Text over the video; null = hidden. */
  overlay: string | null;
  status: string;
  /** The stream being played carries sound, so "Play sound here" can be offered. */
  listenAvailable: boolean;
  /** The viewer's standing choice to hear it; kept across reloads and session refreshes. */
  listen: boolean;
  /** Footage is reaching the cloud archive, so Record clip can work (#83). */
  archiving: boolean;
  /** This page's Start was accepted and its first footage hasn't reached the archive yet. */
  archivePending: boolean;
  /** The last Start's window closed with no footage in the archive: the Pi didn't answer. */
  archiveFailed: boolean;
  /** This browser refused the camera's H.265: the page-level note should show. */
  hevcHelp: boolean;
}

export interface PlayerApi {
  hls(camera: string, view: "live" | "recorded", since?: string): Promise<ApiResult<HlsSession>>;
  cmd(camera: string, action: CommandAction, keepalive?: boolean): Promise<ApiResult>;
}

export interface ControllerDeps {
  api: PlayerApi;
  /** The hls.js class, already loaded (tests); otherwise it is imported on first use. */
  hls?: typeof Hls;
  loadHls?: () => Promise<typeof Hls>;
  isVisible?: () => boolean;
  now?: () => number;
  perfNow?: () => number;
}

type Timer = ReturnType<typeof setTimeout>;
type MediaWithTracks = HTMLVideoElement & {
  audioTracks?: { length: number };
  webkitAudioDecodedByteCount?: number;
};

export class CameraController {
  private s: PlayerState = {
    view: null, overlay: INITIAL_OVERLAY, status: "", listenAvailable: false, listen: false,
    archiving: false, archivePending: false, archiveFailed: false, hevcHelp: false,
  };
  private listeners = new Set<(s: PlayerState) => void>();

  private readonly api: PlayerApi;
  private H: typeof Hls | null;
  private readonly loadHls: () => Promise<typeof Hls>;
  private readonly isVisible: () => boolean;
  private readonly now: () => number;
  private readonly perfNow: () => number;

  private video: MediaWithTracks | null = null;
  private hls: Hls | null = null;
  private userStopped = true;
  private refreshTimer: Timer | null = null;
  private stallTimer: Timer | null = null;
  private liveTimer: Timer | null = null;
  private netRetries = 0;
  private stallReloads = 0;
  private liveSentAt = 0;
  private startedAt: number | null = null;
  private liveWaitUntil = 0;
  private archiveActive = false;
  private archiveTimer: Timer | null = null;
  private archiveSeq = 0;
  private archiveStartedAt = 0;
  private archiveWaitUntil = 0;
  private archiveStoppedAt = 0;

  constructor(private camera: Camera, deps: ControllerDeps) {
    this.api = deps.api;
    this.H = deps.hls ?? null;
    this.loadHls = deps.loadHls ?? (() => import("hls.js").then(m => m.default));
    this.isVisible = deps.isVisible ?? (() => document.visibilityState === "visible");
    this.now = deps.now ?? Date.now;
    this.perfNow = deps.perfNow ?? (() => performance.now());
  }

  get id(): string { return this.camera.id; }
  get state(): PlayerState { return this.s; }

  subscribe(fn: (s: PlayerState) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** A newer registry row for the same camera (codec, live view). */
  setCamera(camera: Camera): void { this.camera = camera; }

  attach(video: HTMLVideoElement): void { this.video = video; }

  detach(): void {
    this.teardownPlayer();
    this.video = null;
  }

  /** Clears every timer and the player; a live view still running is ended (the Pi's deadline is the guarantee). */
  dispose(): void {
    if (this.s.view === "live") this.api.cmd(this.id, "live-stop").catch(() => {});
    this.clearLiveTimer();
    this.stopArchive();
    this.detach();
    this.listeners.clear();
  }

  // --- the player ---------------------------------------------------------------------------

  async load(): Promise<void> {
    const v = this.video;
    if (!v) return;
    this.setStatus("loading stream...");
    this.showOverlay("Loading stream…");
    try {
      // view: Live plays the camera's live view, its own KVS stream "<camera>-live" that the Pi
      // pushes while this page asks for it (toggleLive); after Start the player shows the recorded
      // stream. Within the start window, since=<seconds since the button> asks for footage from
      // the new run only: a plain live session right after Stop -> Start is built from the old
      // run's last fragment and then waits for the new one (FoundAndFixed.md #66). Measured on this
      // page's own clock.
      const view = this.s.view === "live" ? "live" : "recorded";
      const since = this.startedAt !== null && this.now() < this.liveWaitUntil
        ? Math.max(1, (this.perfNow() - this.startedAt) / 1000).toFixed(1) : undefined;
      const r = await this.api.hls(this.id, view, since);
      const data = r.data;
      // Right after Start, "not live" (503) is expected, not an error: the producer needs a few
      // seconds to connect, and KVS has nothing until the camera's first keyframe *closes* a
      // fragment. cam-02 sends one every 3 s, so its first fragment lands ~10 s after Start -- a
      // single look at 8 s missed it every time (FoundAndFixed.md #43). Keep asking until the
      // Start window runs out; Stop clears the window.
      if (r.status === 503 && this.now() < this.liveWaitUntil) {
        this.setStatus("waiting for first video...");
        this.showOverlay((view === "live" ? "Starting live view…" : "Starting stream…")
                         + "\nWaiting for the first video from the camera.");
        this.clearRefresh();
        this.refreshTimer = setTimeout(() => void this.load(), WAIT_RETRY_MS);
        return;
      }
      const waited = !!this.liveWaitUntil;
      this.liveWaitUntil = 0;
      this.startedAt = null;
      if (r.status === 503 && waited) {
        // The whole start window passed without one fragment. The likeliest cause is the command
        // never reaching the Pi -- its MQTT session to AWS was down (FoundAndFixed.md #61) -- so
        // say that rather than a bare "not available".
        this.setStatus("no video arrived within 45 s");
        this.showOverlay("No video arrived within 45 s.\n\nThe Pi didn't answer: it may be offline " +
                         "from AWS, or the camera unreachable. Try again in a minute.");
        return;
      }
      if (!r.ok || !data.url) {
        this.setStatus("error: " + (data.error || r.status));
        this.showOverlay((data.error || "Could not load stream") + "\n\nPress Start to begin.");
        return;
      }
      if (this.video !== v) return;          // detached while the request was out
      const H = this.H ??= await this.loadHls();
      if (this.video !== v) return;
      this.startPlayback(H, v, data.url, data.expires_in, view);
    } catch (e) {
      this.setStatus("error: " + (e as Error).message);
      this.showOverlay("Error loading stream.\n\nPress Reload player to retry.");
    }
  }

  /** The Reload player button. */
  reload(): Promise<void> {
    this.stallReloads = 0;
    return this.load();
  }

  private startPlayback(H: typeof Hls, v: MediaWithTracks, url: string, expiresIn: number | undefined,
                        view: "live" | "recorded") {
    if (this.hls) { this.hls.destroy(); this.hls = null; }
    this.clearRefresh();
    this.clearStallTimer();
    this.userStopped = false;
    this.netRetries = 0;

    // Every reload starts muted and unavailable; MANIFEST_PARSED below re-enables it if the stream
    // really does carry sound. Assuming from the registry flag instead would be wrong -- the flag
    // can have been changed since this producer was started.
    v.muted = true;
    this.setListenAvailable(false);

    if (H.isSupported()) {
      const hls = new H();
      this.hls = hls;
      hls.loadSource(url);
      hls.attachMedia(v);
      hls.on(H.Events.MANIFEST_PARSED, (_e, d) => {
        this.setListenAvailable(!!(d.audioTracks && d.audioTracks.length));
      });
      hls.on(H.Events.FRAG_BUFFERED, () => { this.hideOverlay(); this.netRetries = 0; });
      hls.on(H.Events.ERROR, (_e, d) => {
        if (!d.fatal || this.userStopped) return;
        // The browser refuses the stream's codec outright -- what Firefox and Edge report for
        // H.265 without the HEVC extension. recoverMediaError() cannot supply a missing decoder,
        // so explain it instead of retrying.
        if (d.details === H.ErrorDetails.MANIFEST_INCOMPATIBLE_CODECS_ERROR) {
          const hevc = /hvc1|hev1/.test(String(d.reason || "")) || !hevcSupport(true).live;
          this.setStatus("this browser can't decode the stream's video codec");
          this.showOverlay(hevc
            ? "This browser can't play this camera's H.265 video.\n\nSee the note at the top of the page."
            : "This browser can't decode this camera's video format.");
          if (hevc) this.set({ hevcHelp: true });
          return;
        }
        this.setStatus("player error: " + d.details);
        switch (d.type) {
          case H.ErrorTypes.NETWORK_ERROR:
            // Usually the KVS session URL having expired, which startLoad() can never fix -- it
            // just re-requests the same dead URL. Allow a couple of retries for genuinely
            // transient blips, then go get a whole new session. A failed playlist load is
            // different: startLoad() resumes fragment loading and has no playlist to resume, so
            // the player would sit there spinning, never erroring again (FoundAndFixed.md #66).
            // Those go straight to a new session.
            if (!/^(manifest|level)/.test(d.details) && ++this.netRetries <= 2) {
              hls.startLoad();
            } else {
              this.netRetries = 0;
              this.showOverlay("Reconnecting…");
              this.clearRefresh();
              this.refreshTimer = setTimeout(() => { if (!this.userStopped) void this.load(); }, 1000);
            }
            break;
          case H.ErrorTypes.MEDIA_ERROR:
            hls.recoverMediaError();
            break;
          default:
            this.showOverlay("Playback error (" + d.details + ")\n\nPress Reload player to retry.");
        }
      });
    } else {
      // Native HLS (Safari). There is no manifest event here, so ask the element once it has
      // metadata. audioTracks is the standard answer; webkitAudioDecodedByteCount is the fallback
      // for the browsers that never implemented it.
      v.src = url;
      v.addEventListener("loadedmetadata", () => {
        const n = v.audioTracks ? v.audioTracks.length
                                : ((v.webkitAudioDecodedByteCount ?? 0) > 0 ? 1 : 0);
        this.setListenAvailable(n > 0);
      }, { once: true });
      this.hideOverlay();
    }
    // The <video> has native controls, so the viewer can mute from there too. Mirror it back, or
    // "Play sound here" starts lying about what they can hear.
    v.onvolumechange = () => {
      if (this.s.listenAvailable) this.set({ listen: !v.muted });
    };
    this.scheduleSessionRefresh(expiresIn);
    this.watchStalls();
    this.setStatus(view === "live"
      ? `live view${this.camera.liveView === "sub" ? " · sub stream" : ""} · not recorded`
      : "recorded stream loaded");
  }

  private teardownPlayer(): void {
    this.clearRefresh();
    this.clearStallTimer();
    if (this.hls) { this.hls.destroy(); this.hls = null; }
    const v = this.video;
    if (v) {
      v.onvolumechange = null;        // or it fires during load() and rewrites listen
      v.removeAttribute("src");
      v.load();
    }
    // Stopping leaves nothing to listen to. `listen` is deliberately NOT cleared -- it is the
    // viewer's standing preference and is reapplied when a stream with sound comes back.
    this.setListenAvailable(false);
  }

  // A KVS HLS session URL dies at `expires_in` seconds and cannot be revived -- every later request
  // against it 403s. Refresh at 80% of that so a fresh session is playing before the old URL lapses.
  // Without this the player ends up retrying a permanently dead URL while the browser shows its own
  // spinner forever as the last buffered seconds play out.
  private scheduleSessionRefresh(expiresIn?: number): void {
    this.clearRefresh();
    const ms = Math.max(30, Math.floor((expiresIn || 300) * 0.8)) * 1000;
    this.refreshTimer = setTimeout(() => { if (!this.userStopped) void this.load(); }, ms);
  }

  // A player that stops advancing must never spin forever (FoundAndFixed.md #66). If the picture
  // hasn't moved for STALL_SEC while the page is visible, get a new session: the stream may have
  // restarted, or the session may be stuck on an old run. load() then either plays or says the
  // stream isn't live. Three times in a row with no progress at all means the browser gets the
  // video but doesn't show it -- say so instead of reloading forever.
  private watchStalls(): void {
    const v = this.video;
    if (!v) return;
    this.clearStallTimer();
    let lastT = -1;
    let lastMove = this.now();
    this.stallTimer = setInterval(() => {
      if (this.userStopped || !this.hls) return;
      if (v.currentTime !== lastT) {
        if (lastT >= 0) this.stallReloads = 0;
        lastT = v.currentTime;
        lastMove = this.now();
        return;
      }
      // Hidden tabs may pause video on purpose; a paused player is the viewer's choice.
      if (!this.isVisible() || v.paused) { lastMove = this.now(); return; }
      if (this.now() - lastMove < STALL_SEC * 1000) return;
      this.clearStallTimer();
      this.stallReloads += 1;
      if (this.stallReloads > 2) {
        const codec = this.s.view === "live" ? this.camera.liveVideoCodec : this.camera.videoCodec;
        this.setStatus("video arrives but doesn't play");
        this.showOverlay("The video reaches this browser but doesn't play.\n\n" +
          (codec === "h265" ? "This browser may not decode this camera's H.265 at this size. " : "") +
          "Press Reload player to try again.");
        return;
      }
      this.setStatus(`no new video for ${STALL_SEC} s -- reconnecting`);
      this.showOverlay("Reconnecting…");
      void this.load();
    }, STALL_CHECK_MS);
  }

  // --- sound ---------------------------------------------------------------------------------

  /** "Play sound here": this browser only, instant, affects nobody else. */
  setListen(on: boolean): void {
    const v = this.video;
    if (!v) return;
    // Browsers block unmuted autoplay, which is why the player starts muted. Unmuting from inside
    // a click handler is fine: the click is the user gesture that permits it.
    v.muted = !on;
    if (on && v.paused) v.play().catch(() => {});
    this.set({ listen: on });
  }

  private setListenAvailable(available: boolean): void {
    this.set({ listenAvailable: available });
    // Restore the choice across a session refresh: load() re-attaches the player roughly hourly,
    // and silently re-muting each time would look like the audio had cut out.
    if (available && this.s.listen) this.setListen(true);
  }

  // --- Live: watch without recording (VMS-PORT-PLAN.md D2/D3) ---------------------------------
  // `live` asks the Pi to push the camera's live view (its sub stream when it records main) to the
  // KVS stream "<camera>-live" until a deadline five minutes out; every `live` moves the deadline.
  // So the page re-sends it every LIVE_RESEND_MS while it is visible, sends `live-stop` when the
  // viewer ends Live or leaves, and the deadline is what guarantees the stream stops even when the
  // laptop just closes. Nothing here records: clips come from Start.

  async toggleLive(): Promise<void> {
    this.stallReloads = 0;
    this.clearLiveTimer();
    if (this.s.view === "live") {                       // End live
      this.set({ view: null });
      this.userStopped = true;
      this.liveWaitUntil = 0;
      this.teardownPlayer();
      this.showOverlay("Live view ended.\n\nPress Live to watch again, or Start to record to the cloud.");
      this.api.cmd(this.id, "live-stop").catch(() => {});   // best effort: the Pi's deadline is the guarantee
      this.setStatus("live view ended");
      return;
    }
    this.set({ view: "live" });
    this.userStopped = false;
    this.teardownPlayer();
    this.setStatus("sending live...");
    this.showOverlay("Starting live view…\nThe Pi starts sending this camera's picture; this takes 10–20 s.");
    try {
      const { ok, data } = await this.api.cmd(this.id, "live");
      if (!ok) {
        this.set({ view: null });
        this.setStatus("error: " + (data.error || "live refused"));
        this.showOverlay("Could not start the live view: " + (data.error || "refused"));
        return;
      }
    } catch (e) {
      this.set({ view: null });
      this.setStatus("error: " + (e as Error).message);
      this.showOverlay("Network error starting the live view.\n\nPress Live to try again.");
      return;
    }
    this.liveSentAt = this.now();
    this.startedAt = this.perfNow();
    this.liveWaitUntil = this.now() + START_WINDOW_MS;
    this.liveTimer = setInterval(() => {
      if (this.s.view === "live" && this.isVisible()) {
        this.api.cmd(this.id, "live").then(() => { this.liveSentAt = this.now(); }).catch(() => {});
      }
    }, LIVE_RESEND_MS);
    this.clearRefresh();
    this.refreshTimer = setTimeout(() => void this.load(), FIRST_LOAD_DELAY_MS);
  }

  /** The tab became visible again: renew Live at once, and ask the archive. */
  onVisible(): void {
    // After more than the deadline away, the Pi has stopped the live view, so start it over; the
    // player would only stall on the old session.
    if (this.s.view === "live") {
      if (this.now() - this.liveSentAt > LIVE_DEADLINE_MS - 15_000) {
        this.set({ view: null });                     // toggleLive starts it again from "off"
        void this.toggleLive();
      } else {
        this.api.cmd(this.id, "live").then(() => { this.liveSentAt = this.now(); }).catch(() => {});
      }
    }
    if (this.archiveActive) void this.checkArchive();
  }

  /** Leaving the page: tell the Pi, best effort (keepalive lets the request outlive the page). */
  onPageHide(): void {
    if (this.s.view === "live") this.api.cmd(this.id, "live-stop", true).catch(() => {});
  }

  // --- Start / Stop: record to the cloud archive ------------------------------------------------

  /** Resolves true when the API accepted the command (not proof the Pi acted: see archivePending). */
  async command(action: "start" | "stop"): Promise<boolean> {
    this.setStatus(`sending ${action}...`);
    this.stallReloads = 0;
    const watchingLive = this.s.view === "live";     // Start/Stop record; they leave a Live view alone
    if (action === "stop" && !watchingLive) {
      this.userStopped = true;
      this.liveWaitUntil = 0;
      this.set({ view: null });
      this.teardownPlayer();
      this.showOverlay("Recording to the cloud stopped.\n\nPress Live to watch, or Start to record again.");
    } else if (!watchingLive) {
      this.showOverlay("Starting stream…\nThis can take a few seconds.");
    }
    try {
      const r = await this.api.cmd(this.id, action);
      this.setStatus(r.ok ? `${action} sent` : "error: " + (r.data.error || r.status));
      if (!r.ok) { this.showOverlay("Could not send " + action + ": " + (r.data.error || r.status)); return false; }
      if (action === "start") {                      // watching Live or not: Start records either way
        this.archiveStoppedAt = 0;
        this.archiveStartedAt = this.perfNow();
        this.archiveWaitUntil = this.now() + ARCHIVE_START_WINDOW_MS;
        this.set({ archivePending: true, archiveFailed: false });
        void this.checkArchive();
      } else {
        this.archiveStoppedAt = this.now();
        this.archiveWaitUntil = 0;
        this.set({ archivePending: false, archiveFailed: false });
        this.setArchiving(false);
      }
      if (action === "start" && !watchingLive) {
        this.set({ view: "recorded" });
        this.startedAt = this.perfNow();
        this.liveWaitUntil = this.now() + START_WINDOW_MS;
        this.clearRefresh();
        this.refreshTimer = setTimeout(() => void this.load(), FIRST_LOAD_DELAY_MS);
      }
      return true;
    } catch (e) {
      this.setStatus("error: " + (e as Error).message);
      this.showOverlay("Network error sending " + action + ".\n\nPress Start to try again.");
      return false;
    }
  }

  // --- Is footage reaching the cloud archive? (FoundAndFixed.md #83) ----------------------------
  // Record clip sends no video of its own: it asks the cloud to cut a time span out of the camera's
  // archive, which has footage only while Start runs. KVS itself says whether it does: the recorded
  // stream's /hls answers 200 while it holds a fragment from the last 30 s, 503 when it doesn't --
  // the answer the player gets too. Asked every ARCHIVE_POLL_MS while the page is visible (a Start
  // or Stop from the admin GUI or another browser shows up that way), on coming back to the tab,
  // and every ARCHIVE_POLL_FAST_MS through the start window after this page's Start -- with since=,
  // so the previous run's last fragment doesn't count (#66). After this page's own Stop, KVS still
  // calls the stream live for up to 30 s, so a 200 is ignored for ARCHIVE_STOP_GRACE_MS.

  startArchive(): void {
    this.archiveActive = true;
    void this.checkArchive();
  }

  stopArchive(): void {
    this.archiveActive = false;
    this.archiveSeq += 1;                            // drops an answer still on its way
    if (this.archiveTimer) clearTimeout(this.archiveTimer);
    this.archiveTimer = null;
  }

  // One loop per camera: each call takes over the timer, and an answer that a later call has
  // overtaken is dropped (archiveSeq), so a slow probe from before Start can't switch it back off.
  async checkArchive(): Promise<void> {
    this.archiveActive = true;
    if (this.archiveTimer) clearTimeout(this.archiveTimer);
    const seq = ++this.archiveSeq;
    if (this.isVisible()) {
      const since = this.now() < this.archiveWaitUntil
        ? Math.max(1, (this.perfNow() - this.archiveStartedAt) / 1000).toFixed(1) : undefined;
      try {
        const r = await this.api.hls(this.id, "recorded", since);
        if (seq !== this.archiveSeq) return;
        // Anything else (an expired sign-in, a Lambda error) says nothing about the stream.
        if (r.status === 200 || r.status === 503) {
          this.setArchiving(r.ok && this.now() - this.archiveStoppedAt > ARCHIVE_STOP_GRACE_MS);
          // A Start is settled by the first answer that finds footage, or by the first answer
          // after its window: then nothing arrived in 45 s, the case load() reports as "the Pi
          // didn't answer" (FoundAndFixed.md #61). Only an answer settles it -- a hidden tab waits.
          if (this.s.archivePending && (this.s.archiving || this.now() >= this.archiveWaitUntil)) {
            this.set({ archivePending: false, archiveFailed: !this.s.archiving });
          }
        }
      } catch {
        if (seq !== this.archiveSeq) return;         // transient; the next tick asks again
      }
    }
    this.archiveTimer = setTimeout(() => void this.checkArchive(),
      this.now() < this.archiveWaitUntil ? ARCHIVE_POLL_FAST_MS : ARCHIVE_POLL_MS);
  }

  private setArchiving(on: boolean): void { this.set({ archiving: on }); }

  // --- small helpers ----------------------------------------------------------------------------

  private set(patch: Partial<PlayerState>): void {
    this.s = { ...this.s, ...patch };
    for (const fn of this.listeners) fn(this.s);
  }
  private setStatus(msg: string): void { this.set({ status: msg }); }
  private showOverlay(msg: string): void { this.set({ overlay: msg }); }
  private hideOverlay(): void { this.set({ overlay: null }); }

  private clearRefresh(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = null;
  }
  private clearStallTimer(): void {
    if (this.stallTimer) clearInterval(this.stallTimer);
    this.stallTimer = null;
  }
  private clearLiveTimer(): void {
    if (this.liveTimer) clearInterval(this.liveTimer);
    this.liveTimer = null;
  }
}
