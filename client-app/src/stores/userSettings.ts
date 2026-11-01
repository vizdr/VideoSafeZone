import { defineStore } from "pinia";
import { ref } from "vue";
import { endpoints } from "../api";
import { errorText, routeMissing } from "../api/client";
import type { UserSettings } from "../api/types";
import type { SettingStatus } from "../composables/useSavedSetting";
import { DEFAULT_LAYOUT, isLayoutId, type LayoutId } from "../layouts/grid";

// Before cloud/provision.sh has created /me/settings, API Gateway knows no such route
// (routeMissing). The page then works with the defaults and keeps a choice for as long as it stays
// open, rather than calling every change a failure.
const notSetUp = routeMissing;
const NOT_SET_UP = "Saving to your account isn't set up yet: this applies in this browser until you leave.";

/** The signed-in user's own settings (WEB-PANEL-PLAN.md D4): the Live layout and its large-cell
 *  cameras, kept with the account so they follow it from browser to browser. */
export const useUserSettingsStore = defineStore("userSettings", () => {
  const liveLayout = ref<LayoutId>(DEFAULT_LAYOUT);
  const largeCells = ref<string[]>([]);
  const loaded = ref(false);
  /** Why the account's settings aren't in use, or null. */
  const problem = ref<string | null>(null);

  function apply(s: UserSettings): void {
    if (isLayoutId(s.liveLayout)) liveLayout.value = s.liveLayout;
    if (Array.isArray(s.largeCells)) largeCells.value = s.largeCells.filter(x => typeof x === "string");
  }

  async function load(): Promise<void> {
    try {
      const r = await endpoints.getMySettings();
      if (r.ok) { apply(r.data.settings ?? {}); problem.value = null; }
      else problem.value = notSetUp(r) ? NOT_SET_UP
        : `Your saved settings couldn't be loaded (${errorText(r)}): the defaults are shown.`;
    } catch (e) {
      problem.value = `Your saved settings couldn't be loaded (${(e as Error).message}): the defaults are shown.`;
    } finally {
      loaded.value = true;
    }
  }

  /** Applies a change at once and saves it to the account. A refusal puts the old value back; a
   *  route that isn't set up yet keeps the change for this visit. */
  async function save(patch: UserSettings): Promise<SettingStatus> {
    const before: UserSettings = { liveLayout: liveLayout.value, largeCells: [...largeCells.value] };
    apply(patch);
    try {
      const r = await endpoints.putMySettings(patch);
      if (r.ok) { problem.value = null; return { text: "Saved to your account.", tone: "ok" }; }
      if (notSetUp(r)) { problem.value = NOT_SET_UP; return { text: NOT_SET_UP, tone: "info" }; }
      apply(before);
      return { text: "Not saved: " + errorText(r), tone: "error" };
    } catch (e) {
      apply(before);
      return { text: "Not saved: " + (e as Error).message, tone: "error" };
    }
  }

  function clear(): void {
    liveLayout.value = DEFAULT_LAYOUT;
    largeCells.value = [];
    loaded.value = false;
    problem.value = null;
  }

  return { liveLayout, largeCells, loaded, problem, load, save, clear };
});
