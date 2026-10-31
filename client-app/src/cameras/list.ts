// The camera list's search and grouping (WEB-PANEL-PLAN.md §4.1): search on name, ID and tag;
// grouped by first tag, untagged last. Until tags exist (Phase 4) every camera is untagged, and
// the list shows a single group with no heading.
import type { Camera } from "../api/types";

export interface CameraGroup {
  tag: string | null;        // null: the untagged cameras
  cameras: Camera[];
}

/** The name to show: the display name set in the admin GUI, else the ID. */
export function cameraName(c: Camera): string {
  return c.displayName || c.id;
}

/** Every word of the query must appear in the camera's name, ID or tags (case ignored). */
export function matchesQuery(c: Camera, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [c.id, c.displayName ?? "", ...(c.tags ?? [])].join(" ").toLowerCase();
  return words.every(w => hay.includes(w));
}

/** Groups in tag order, untagged last; cameras keep the order list-cameras gave them (by ID). */
export function groupCameras(cameras: Camera[]): CameraGroup[] {
  const groups = new Map<string | null, Camera[]>();
  for (const c of cameras) {
    const tag = c.tags?.[0] || null;
    if (!groups.has(tag)) groups.set(tag, []);
    groups.get(tag)!.push(c);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : a.localeCompare(b)))
    .map(([tag, cams]) => ({ tag, cameras: cams }));
}

const CODEC_NAMES: Record<string, string> = { h264: "H.264", h265: "H.265" };

/** "video H.265 (camera encoder)": display only -- the codec is set in the admin GUI (guide §21.3). */
export function codecLabel(c: Camera): string {
  const name = CODEC_NAMES[c.videoCodec] || "H.264";
  const where = c.videoEncode === "camera" ? "camera encoder"
    : c.videoEncode === "pi-hw" ? "adapter hardware encoder" : null;
  return `video ${name}${where ? ` (${where})` : ""}`;
}

/** "USB, transcoded" or "ONVIF, passthrough". */
export function modeLabel(c: Camera): string {
  return c.mode === "passthrough" ? "ONVIF, passthrough" : "USB, transcoded";
}
