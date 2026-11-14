// Billing on the page (DEALER-PANEL-PLAN.md Phase 8, D9): a billing period is a calendar month in UTC,
// as the directory counts it; and how an invoice line reads.
import type { InvoiceLine } from "../api/types";
import { money } from "../pricing";
import { adapterLabel } from "./customer";

/** "2026-11": the month under way, in UTC. */
export function currentPeriod(now = new Date()): string {
  return now.toISOString().slice(0, 7);
}

/** "November 2026", for a period ("2026-11") or its first day ("2026-11-01"). */
export function monthLabel(period: string): string {
  return new Date(`${period.slice(0, 7)}-01T00:00:00Z`)
    .toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
}

/** Where the line's adapter is: "Home · Garage — adapter-0002". */
export function lineAdapter(line: InvoiceLine): string {
  return adapterLabel({ site: line.site, name: line.name, thingName: line.thingName });
}

/** What the line bills: its plan's version, cameras and storage; an annual plan's line is a twelfth of its year. */
export function lineText(line: InvoiceLine): string {
  const cameras = `${line.cameras} camera${line.cameras === 1 ? "" : "s"}`;
  const year = line.interval === "annual" ? ` · a twelfth of ${money(line.centsPeriod, line.currency)} a year` : "";
  return `plan version ${line.version} · ${cameras} · ${line.retentionDays} days${year}`;
}
