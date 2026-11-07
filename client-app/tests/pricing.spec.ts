// The page's plan price (src/pricing.ts) against the vectors the server's is tested with
// (tests/fixtures/pricing-cases.json in the repository, read by tests/cloud/test_pricing.py): the
// price a dealer sees while editing a plan is the price the server stores (DEALER-PANEL-PLAN.md D9).
import { describe, expect, it } from "vitest";
import type { BillingInterval, PlanLine, Price } from "../src/api/types";
import { money, pricePlan } from "../src/pricing";
import vectors from "../../tests/fixtures/pricing-cases.json";

type Case = { name: string; lines: PlanLine[]; retentionDays: number; interval: BillingInterval;
              annualDiscountPercent?: number; cents: number | null };

describe("pricePlan, against the server's vectors", () => {
  it.each((vectors.cases as Case[]).map(c => [c.name, c] as const))("%s", (_, c) => {
    const discount = c.annualDiscountPercent ?? vectors.annualDiscountPercent;
    expect(pricePlan(vectors.prices as Price[], discount, c.lines, c.retentionDays, c.interval)).toBe(c.cents);
  });
});

describe("money", () => {
  it("writes cents in the currency, and copes with one the browser doesn't know", () => {
    expect(money(2356, "EUR")).toMatch(/23[.,]56/);
    expect(money(2356, "EUR")).toMatch(/€|EUR/);
    expect(money(100, "XX1")).toBe("1.00 XX1");
  });
});
