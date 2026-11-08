// Unregister and Re-register from the web page (DEALER-PANEL-PLAN.md D16; the user, 2026-11-08): the
// page asks the camera's adapter, which acts in a moment -- seconds for the USB camera, up to a
// minute for a network camera, which must answer with its stored login. The page then reads the
// camera list until it shows the change.
import { endpoints } from "../api";
import { errorText } from "../api/client";
import type { Camera } from "../api/types";
import { useCamerasStore } from "../stores/cameras";
import { pollUntil } from "./poll";

export const WAIT_MS = 90_000, EVERY_MS = 3_000;

/** Whoever controls a camera on an account may unregister and re-register it: the holder or a Super
 *  Admin, and an administrator; it must be an adapter's, the one that is asked. */
export const canManage = (camera: Camera, role: string | null) =>
  (role === "customer" || role === "system_admin") && camera.access !== "view" && !!camera.adapterId;

/** Ask the adapter, then wait until the camera list shows it. "" when it did, else what to say. */
export async function changeRegistration(camera: Camera, register: boolean, stopped?: () => boolean): Promise<string> {
  const res = register ? await endpoints.reregisterCamera(camera.adapterId!, camera.id)
    : await endpoints.removeCamera(camera.adapterId!, camera.id);
  if (!res.ok) return errorText(res);
  const store = useCamerasStore();
  const out = await pollUntil(async () => {
    await store.load();
    return store.byId(camera.id) ?? null;
  }, c => !c.unregisteredAt === register, { every: EVERY_MS, within: WAIT_MS, stopped });
  if (out === "timeout") {
    return register ? "The adapter hasn't brought it back yet. A network camera must answer with its stored login: "
                      + "look again in a while, or add it again from Cameras with a new login."
      : "The adapter hasn't unregistered it yet: is it online? Look again in a while.";
  }
  return "";
}
