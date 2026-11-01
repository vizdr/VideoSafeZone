import { describe, expect, it } from "vitest";
import type { Camera } from "../src/api/types";
import {
  GRID_SIZE, LAYOUTS, LAYOUT_IDS, bringToLarge, cameraOrder, isLayoutId, largeCount, pageOf,
  type LayoutId,
} from "../src/layouts/grid";

/** Which tile covers each of the 16 cells (-1 = blank); throws on an overlap or a cell off the grid. */
function coverage(id: LayoutId): number[][] {
  const grid = Array.from({ length: GRID_SIZE }, () => Array(GRID_SIZE).fill(-1));
  LAYOUTS[id].cells.forEach((cell, tile) => {
    for (let r = cell.row; r < cell.row + cell.span; r++) {
      for (let c = cell.col; c < cell.col + cell.span; c++) {
        if (r < 1 || r > GRID_SIZE || c < 1 || c > GRID_SIZE) throw new Error(`${id}: tile ${tile} off the grid`);
        if (grid[r - 1][c - 1] !== -1) throw new Error(`${id}: tiles ${grid[r - 1][c - 1]} and ${tile} overlap`);
        grid[r - 1][c - 1] = tile;
      }
    }
  });
  return grid;
}

const cams = (...ids: string[]) => ids.map(id => ({ id }) as Camera);

describe("layouts (D3)", () => {
  it("place every tile on the 4×4 grid without overlaps", () => {
    for (const id of LAYOUT_IDS) expect(() => coverage(id)).not.toThrow();
  });

  it("hold 8, 13 and 16 tiles, with 2, 1 and 0 large cells listed first", () => {
    expect(LAYOUT_IDS.map(id => LAYOUTS[id].cells.length)).toEqual([8, 13, 16]);
    expect(LAYOUT_IDS.map(largeCount)).toEqual([2, 1, 0]);
    for (const id of LAYOUT_IDS) {
      const spans = LAYOUTS[id].cells.map(c => c.span);
      expect(spans).toEqual([...spans].sort().reverse());
    }
  });

  it("leave blank only 2 + 6's two bottom-right cells", () => {
    expect(coverage("2+6")).toEqual([
      [0, 0, 1, 1],
      [0, 0, 1, 1],
      [2, 3, 4, 5],
      [6, 7, -1, -1],
    ]);
    expect(coverage("1+12").flat()).not.toContain(-1);
    expect(coverage("16").flat()).not.toContain(-1);
  });

  it("recognise only their own ids", () => {
    expect(["2+6", "1+12", "16"].every(isLayoutId)).toBe(true);
    expect(isLayoutId("4x4")).toBe(false);
    expect(isLayoutId(undefined)).toBe(false);
  });
});

describe("which camera goes where", () => {
  const list = cams("cam-01", "cam-02", "cam-03", "cam-04");

  it("puts the large-cell picks first, then the rest in list order", () => {
    expect(cameraOrder(list, ["cam-03", "cam-01"], "2+6")).toEqual(["cam-03", "cam-01", "cam-02", "cam-04"]);
    expect(cameraOrder(list, [], "2+6")).toEqual(["cam-01", "cam-02", "cam-03", "cam-04"]);
  });

  it("drops picks for cameras that are gone, repeats, and picks beyond the large cells", () => {
    expect(cameraOrder(list, ["cam-09", "cam-04"], "2+6")).toEqual(["cam-01", "cam-04", "cam-02", "cam-03"]);
    expect(cameraOrder(list, ["cam-02", "cam-02"], "2+6")).toEqual(["cam-02", "cam-01", "cam-03", "cam-04"]);
    expect(cameraOrder(list, ["cam-03", "cam-04"], "1+12")).toEqual(["cam-03", "cam-01", "cam-02", "cam-04"]);
    expect(cameraOrder(list, ["cam-03"], "16")).toEqual(["cam-01", "cam-02", "cam-03", "cam-04"]);
  });

  it("keeps each pick in its own large cell; \"\" means the next camera in the list", () => {
    expect(cameraOrder(list, ["", "cam-03"], "2+6")).toEqual(["cam-01", "cam-03", "cam-02", "cam-04"]);
    expect(cameraOrder(cams("cam-01"), ["", "cam-01"], "2+6")).toEqual(["cam-01"]);
  });

  it("fills a page and pads it with empty cells", () => {
    expect(pageOf(["a", "b", "c"], "2+6", 0)).toEqual({ tiles: ["a", "b", "c", null, null, null, null, null], pages: 1 });
  });

  it("pages when there are more cameras than tiles, and clamps a page that no longer exists", () => {
    const ten = Array.from({ length: 10 }, (_, i) => `c${i}`);
    expect(pageOf(ten, "2+6", 0)).toMatchObject({ pages: 2 });
    expect(pageOf(ten, "2+6", 1).tiles).toEqual(["c8", "c9", null, null, null, null, null, null]);
    expect(pageOf(ten, "16", 3)).toMatchObject({ pages: 1 });
    expect(pageOf([], "16", 0)).toEqual({ tiles: Array(16).fill(null), pages: 1 });
  });

  it("brings a clicked small tile into the first large cell, swapping places", () => {
    const order = ["a", "b", "c", "d"];
    expect(bringToLarge(order, "2+6", 0, "d")).toEqual(["d", "b", "c", "a"]);
    expect(bringToLarge(order, "2+6", 0, "b")).toBe(order);       // already large
    expect(bringToLarge(order, "16", 0, "d")).toBe(order);        // no large cell
    expect(bringToLarge(order, "1+12", 0, "c")).toEqual(["c", "b", "a", "d"]);
  });
});
