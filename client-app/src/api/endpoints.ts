// One function per API route the page uses, with the request bodies the Lambdas expect.
import type { ApiClient } from "./client";
import type {
  Camera, ClipsPage, CommandAction, HlsSession, IrMode, RecordingMode, StorageTier,
} from "./types";

const q = encodeURIComponent;

export function makeEndpoints(api: ApiClient) {
  return {
    listCameras: () => api.request<{ cameras: Camera[] }>("GET", "/cameras"),

    /** since: seconds since this page's Start, so a session holds only the new run (FoundAndFixed.md #66). */
    hls: (camera: string, view: "live" | "recorded", since?: string) =>
      api.request<HlsSession>("GET",
        `/hls?stream=${q(camera)}&view=${view}${since === undefined ? "" : `&since=${since}`}`),

    cmd: (camera: string, action: CommandAction, keepalive = false) =>
      api.request("POST", "/cmd", { action, camera }, { keepalive }),

    setIrMode: (camera: string, mode: IrMode) =>
      api.request("POST", "/cmd", { action: "ir", camera, mode }),

    setRecordingMode: (cameraId: string, recordingMode: RecordingMode) =>
      api.request("POST", "/cameras/mode", { cameraId, recordingMode }),

    setAudio: (cameraId: string, audioEnabled: boolean) =>
      api.request("POST", "/cameras/audio", { cameraId, audioEnabled }),

    setOutageBuffer: (cameraId: string, outageBufferSec: number) =>
      api.request("POST", "/cameras/outage-buffer", { cameraId, outageBufferSec }),

    listClips: (camera: string, limit: number, page: number | "last") =>
      api.request<ClipsPage>("GET", `/clips?camera=${q(camera)}&limit=${limit}&page=${page}`),

    recordClip: (stream: string, startTs: string, endTs: string) =>
      api.request<{ keys?: string[] }>("POST", "/clips/record", { stream, startTs, endTs }),

    setClipTier: (s3Key: string, tier: StorageTier) =>
      api.request("POST", "/clips/tier", { s3Key, tier }),

    deleteClip: (s3Key: string, startTs: string) =>
      api.request("POST", "/clips/delete", { s3Key, startTs }),

    playClip: (s3Key: string) =>
      api.request<{ status?: string; url?: string; eta_hours?: number }>("POST", "/clips/play", { s3Key }),
  };
}

export type Endpoints = ReturnType<typeof makeEndpoints>;
