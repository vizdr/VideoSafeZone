import { ref } from "vue";
import { errorText, type ApiResult } from "../api/client";

export interface SettingStatus {
  text: string;
  tone: "info" | "ok" | "error";
}

/** Saves one setting the moment the viewer changes it: shows the change at once, sends it,
 *  and puts the old value back if the server refuses -- the page never shows a setting the
 *  server didn't accept. The control is busy (disabled) while a save is out, so saves never
 *  overlap. Call save() from the control's change event only, never from a watch on loaded
 *  data: that would send every setting back on every sign-in. */
export function useSavedSetting<T>(opts: {
  read: () => T;
  apply: (v: T) => void;
  send: (v: T) => Promise<ApiResult<unknown>>;
  saved?: (v: T) => string;
}) {
  const status = ref<SettingStatus | null>(null);
  const busy = ref(false);

  async function save(v: T): Promise<void> {
    if (busy.value) return;
    const previous = opts.read();
    busy.value = true;
    opts.apply(v);
    status.value = { text: "saving…", tone: "info" };
    try {
      const r = await opts.send(v);
      if (r.ok) {
        status.value = { text: opts.saved ? opts.saved(v) : "Saved.", tone: "ok" };
      } else {
        opts.apply(previous);
        status.value = { text: "Not saved: " + errorText(r), tone: "error" };
      }
    } catch (e) {
      opts.apply(previous);
      status.value = { text: "Not saved: " + (e as Error).message, tone: "error" };
    } finally {
      busy.value = false;
    }
  }

  return { status, busy, save };
}
