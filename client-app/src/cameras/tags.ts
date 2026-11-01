// Tags group the camera list (WEB-PANEL-PLAN.md §4.1). The rules are set_camera_settings.py's.
export const MAX_TAGS = 10;
const TAG = /^[A-Za-z0-9 _-]{1,24}$/;

/** What was typed, tidied: no space at the ends, single spaces inside. */
export function normalizeTag(s: string): string {
  return s.trim().replace(/\s+/g, " ");
}

/** Why `tag` can't be added to `tags`, or null when it can. */
export function tagProblem(tag: string, tags: string[]): string | null {
  if (!tag) return "Type a tag first.";
  if (!TAG.test(tag)) return "A tag is 1–24 letters, digits, spaces, - or _.";
  if (tags.some(t => t.toLowerCase() === tag.toLowerCase())) return `“${tag}” is already there.`;
  if (tags.length >= MAX_TAGS) return `At most ${MAX_TAGS} tags.`;
  return null;
}
