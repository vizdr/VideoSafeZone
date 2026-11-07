// A plan's price (DEALER-PANEL-PLAN.md D9, D19), for the plan editor's preview. The server computes
// the same when the plan is saved (price_plan in cloud/lambda/vsz_directory.py), and its figure is
// the one stored; tests/fixtures/pricing-cases.json keeps the two the same.
import type { BillingInterval, PlanLine, Price } from "./api/types";

/** The price for the plan's period, in cents; null when the price list has no price for a camera on it. */
export function pricePlan(prices: Pick<Price, "resolution" | "recording" | "retentionDays" | "centsMonth">[],
                          annualDiscountPercent: number, lines: PlanLine[], retentionDays: number,
                          interval: BillingInterval): number | null {
  let monthly = 0;
  for (const line of lines) {
    if (!line.quantity) continue;
    const price = prices.find(p => p.resolution === line.resolution && p.recording === line.recording
                                   && p.retentionDays === retentionDays);
    if (!price) return null;
    monthly += line.quantity * price.centsMonth;
  }
  if (interval === "monthly") return monthly;
  // Whole cents, half up, as the server's integer arithmetic.
  return Math.floor((monthly * 12 * (100 - annualDiscountPercent) + 50) / 100);
}

/** "€23.56" -- cents in the currency, as the viewer's browser writes money. */
export function money(cents: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}
