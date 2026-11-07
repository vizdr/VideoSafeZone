// The plan editor's vocabulary (DEALER-PANEL-PLAN.md §7.1): camera classes, how a plan reads, and
// what changed between two versions for Plan history.
import type { BillingInterval, Plan, PlanLine, RecordingClass, Resolution } from "../api/types";

export const RESOLUTIONS: Resolution[] = ["2mp", "4mp", "8mp"];
export const RECORDINGS: RecordingClass[] = ["motion", "continuous"];
export const RESOLUTION_LABELS: Record<Resolution, string> = { "2mp": "2MP (1080p)", "4mp": "4MP", "8mp": "8MP (4K)" };
export const RECORDING_LABELS: Record<RecordingClass, string> = { motion: "Motion triggered", continuous: "24/7 continuous" };
export const STATUS_LABELS: Record<string, string> = {
  trial: "Trial", active: "Active", cancelled: "Cancelled", ended: "Ended",
};

/** All six lines, at 0 unless the plan has them. */
export function linesOf(plan: Pick<Plan, "lines"> | null): PlanLine[] {
  return RESOLUTIONS.flatMap(resolution => RECORDINGS.map(recording => ({
    resolution, recording,
    quantity: plan?.lines.find(l => l.resolution === resolution && l.recording === recording)?.quantity ?? 0,
  })));
}

const short = (l: PlanLine) => `${l.quantity} × ${l.resolution.toUpperCase()} ${l.recording}`;

/** "4 × 4MP motion, 1 × 8MP continuous" -- or "no cameras". */
export function camerasText(lines: PlanLine[]): string {
  const on = lines.filter(l => l.quantity > 0);
  return on.length ? on.map(short).join(", ") : "no cameras";
}

export const intervalText = (i: BillingInterval) => (i === "annual" ? "annual" : "monthly");

/** What a version changed from the one before, in words; the first version "started". */
export function planChanges(before: Plan | null, after: Plan): string[] {
  if (!before) return ["started"];
  const out: string[] = [];
  if (before.status !== after.status) out.push(`${STATUS_LABELS[before.status] ?? before.status} → ${STATUS_LABELS[after.status] ?? after.status}`);
  for (const l of linesOf(after)) {
    const was = linesOf(before).find(b => b.resolution === l.resolution && b.recording === l.recording)!.quantity;
    if (was !== l.quantity) out.push(`${l.resolution.toUpperCase()} ${l.recording}: ${was} → ${l.quantity}`);
  }
  if (before.retentionDays !== after.retentionDays) out.push(`storage: ${before.retentionDays} → ${after.retentionDays} days`);
  if (before.interval !== after.interval) out.push(`payment: ${intervalText(before.interval)} → ${intervalText(after.interval)}`);
  return out.length ? out : ["saved again"];
}
