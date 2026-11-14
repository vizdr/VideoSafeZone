import { defineStore } from "pinia";
import { ref } from "vue";
import { endpoints } from "../api";
import { errorText } from "../api/client";
import type { Me } from "../api/types";

/** The signed-in person as the directory knows them (GET /directory/me): their role in each account and
 *  each account's sites and adapters -- for the Account page's tabs and the camera list's groups
 *  (DEALER-PANEL-PLAN.md D18). Asked once; load(true) asks again, after a change of access. */
export const useDirectoryStore = defineStore("directory", () => {
  const me = ref<Me | null>(null);
  const error = ref("");
  let asked: Promise<void> | null = null;

  function load(again = false): Promise<void> {
    if (asked && !again) return asked;
    asked = (async () => {
      try {
        const r = await endpoints.me();
        if (r.ok) {
          me.value = r.data;
          error.value = "";
        } else {
          error.value = errorText(r);
        }
      } catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
      }
    })();
    return asked;
  }

  function clear(): void {
    me.value = null;
    error.value = "";
    asked = null;
  }

  return { me, error, load, clear };
});
