// What each role sees of the page (DEALER-PANEL-PLAN.md D6, D7). Convenience only: the API decides
// every call again from the database (vsz-directory), so hiding here is never the check.
import type { Role } from "../api/types";

/** Live, Events, Settings: whoever has cameras -- customers, and administrators for support. A token
 *  without vsz_role (the old pool, before the switch) is treated as before. */
export const CAMERA_ROLES: ReadonlyArray<Role | null> = ["customer", "system_admin", null];
export const DEALER_ROLES: ReadonlyArray<Role | null> = ["dealer"];
/** The dealer portal (D7): dealers, and owners and administrators across every dealer. */
export const PORTAL_ROLES: ReadonlyArray<Role | null> = ["dealer", "system_owner", "system_admin"];
export const SYSTEM_ROLES: ReadonlyArray<Role | null> = ["system_owner", "system_admin"];

/** Where a role lands after signing in: a dealer on the portal (D7), an owner on the System
 *  console, everyone with cameras on Live. */
export function homeFor(role: Role | null): string {
  if (role === "dealer") return "/dealer";
  if (role === "system_owner") return "/system";
  return "/live";
}

export const ROLE_LABELS: Record<string, string> = {
  system_admin: "System administrator", system_owner: "System owner", dealer: "Dealer",
  customer: "Account holder", customer_admin: "Super Admin", customer_user: "User",
};
