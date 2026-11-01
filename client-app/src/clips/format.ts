// The evidence-clip list's wording and paging, ported from client/index.html.
import type { Clip, StorageTier } from "../api/types";

/** Clips are listed a page at a time, newest first. */
export const CLIPS_PAGE_SIZE = 10;

export const TIER_LABEL: Record<StorageTier, string> = {
  STANDARD: "Standard", STANDARD_IA: "Standard-IA", DEEP_ARCHIVE: "Deep Archive",
  GLACIER: "Glacier", UNKNOWN: "Unknown",
};

// set_clip_tier.py VALID_TIERS (tests/test_value_lists.py compares them).
export const TIER_CHOICES: { value: StorageTier; label: string }[] = [
  { value: "STANDARD", label: "Standard" },
  { value: "STANDARD_IA", label: "Standard-IA" },
  { value: "DEEP_ARCHIVE", label: "Deep Archive" },
];

/** The choice to preselect for a clip: Glacier shows as Deep Archive; unknown as Standard. */
export function tierChoice(tier: StorageTier | undefined): StorageTier {
  if (tier === "GLACIER") return "DEEP_ARCHIVE";
  return tier && TIER_CHOICES.some(t => t.value === tier) ? tier : "STANDARD";
}

// Evidence clips are 45 s and read fine as "45 sec.". Outage-buffer chunks run to 2 hours, which
// rendered as "7200 sec." -- technically correct and unreadable.
export function formatDuration(sec: number | null | undefined): string {
  if (sec == null) return "duration unknown";
  const s = Number(sec);
  if (s < 90) return `${s} sec.`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  if (!h) return `${m} min.`;
  return m ? `${h} h ${m} min.` : `${h} h`;
}

/** The page numbers to offer: all of them up to 9 pages, else the first, the last and two either
 *  side of the current one, with null marking each gap ("…"). */
export function clipPageNumbers(page: number, pages: number): (number | null)[] {
  if (pages <= 9) return Array.from({ length: pages }, (_, i) => i + 1);
  const shown = [...new Set([1, page - 2, page - 1, page, page + 1, page + 2, pages])]
    .filter(n => n >= 1 && n <= pages).sort((a, b) => a - b);
  const out: (number | null)[] = [];
  shown.forEach((n, i) => { if (i && n - shown[i - 1] > 1) out.push(null); out.push(n); });
  return out;
}

export type PageRequest = "first" | "last" | "older" | "newer" | "current" | number;

/** The page to ask list-clips for. "last" is resolved by the server: a page past the end comes
 *  back as the last page, so the numbers follow deletes and new clips (list_clips.py). */
export function targetPage(req: PageRequest, current: number): number | "last" {
  if (req === "first") return 1;
  if (req === "last") return "last";
  if (req === "older") return current + 1;
  if (req === "newer") return Math.max(1, current - 1);
  if (req === "current") return current;
  return req;
}

/** The camera's sound and codec in a clip line, e.g. " · H.265". */
export function clipCodecName(clip: Clip): string | null {
  return clip.videoCodec === "h265" ? "H.265" : clip.videoCodec === "h264" ? "H.264" : null;
}
