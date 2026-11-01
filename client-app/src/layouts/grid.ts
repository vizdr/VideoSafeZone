// The Live grid's layouts (WEB-PANEL-PLAN.md D3), all on a 4×4 grid of 16:9 cells:
//
//   2 + 6 (default)          1 + 12                   16
//   ┌─────────┬─────────┐    ┌─────────┬────┬────┐    ┌────┬────┬────┬────┐
//   │    1    │    2    │    │    1    │ 2  │ 3  │    │ 1  │ 2  │ 3  │ 4  │
//   │         │         │    │         ├────┼────┤    ├────┼────┼────┼────┤
//   ├────┬────┼────┬────┤    │         │ 4  │ 5  │    │ ...               │
//   │ 3  │ 4  │ 5  │ 6  │    ├────┬────┼────┼────┤    │                   │
//   ├────┼────┼────┴────┤    │ 6  │ 7  │ 8  │ 9  │    │                   │
//   │ 7  │ 8  │  blank  │    │ 10 │ 11 │ 12 │ 13 │    │ 13 │ 14 │ 15 │ 16 │
//
// Pure data and functions: the view only places what these return.
import type { Camera } from "../api/types";

export type LayoutId = "2+6" | "1+12" | "16";

export interface Cell {
  row: number;          // 1-based grid line, as CSS grid-row
  col: number;
  span: 1 | 2;          // a large cell is 2×2
}

export interface Layout {
  id: LayoutId;
  label: string;
  cells: Cell[];        // in tile order: large cells first
}

export const GRID_SIZE = 4;
export const DEFAULT_LAYOUT: LayoutId = "2+6";

const small = (row: number, col: number): Cell => ({ row, col, span: 1 });
const large = (row: number, col: number): Cell => ({ row, col, span: 2 });

export const LAYOUTS: Record<LayoutId, Layout> = {
  "2+6": {
    id: "2+6", label: "2 + 6",
    cells: [large(1, 1), large(1, 3), small(3, 1), small(3, 2), small(3, 3), small(3, 4), small(4, 1), small(4, 2)],
  },
  "1+12": {
    id: "1+12", label: "1 + 12",
    cells: [large(1, 1), small(1, 3), small(1, 4), small(2, 3), small(2, 4),
            ...[1, 2, 3, 4].map(c => small(3, c)), ...[1, 2, 3, 4].map(c => small(4, c))],
  },
  "16": {
    id: "16", label: "16",
    cells: [1, 2, 3, 4].flatMap(r => [1, 2, 3, 4].map(c => small(r, c))),
  },
};

// Spelled out: Object.keys would put "16" first, as JavaScript orders integer-like keys first.
export const LAYOUT_IDS: LayoutId[] = ["2+6", "1+12", "16"];

export function isLayoutId(v: unknown): v is LayoutId {
  return typeof v === "string" && v in LAYOUTS;
}

/** How many of a layout's cells are large. */
export function largeCount(id: LayoutId): number {
  return LAYOUTS[id].cells.filter(c => c.span === 2).length;
}

/** The camera order the grid fills. Large cell i shows largeCells[i] when that camera exists;
 *  "" (or a camera that is gone, or one already placed) means the next camera in list order. Then
 *  every other camera, in list order. Picks for cameras that are gone are dropped here, by the page,
 *  which has the camera list (WEB-PANEL-PLAN.md §5.2). */
export function cameraOrder(cameras: Camera[], largeCells: string[], id: LayoutId): string[] {
  const ids = cameras.map(c => c.id);
  const used = new Set<string>();
  const slots = Array.from({ length: Math.min(largeCount(id), ids.length) }, (_, i) => {
    const pick = largeCells[i];
    if (pick && ids.includes(pick) && !used.has(pick)) { used.add(pick); return pick; }
    return null;
  });
  const rest = ids.filter(i => !used.has(i));
  const large = slots.map(s => s ?? rest.shift()!);
  return [...large, ...rest];
}

/** The cameras on one page of the grid, one per cell (null = no camera for that cell), and the
 *  number of pages. */
export function pageOf(order: string[], id: LayoutId, page: number): { tiles: (string | null)[]; pages: number } {
  const n = LAYOUTS[id].cells.length;
  const pages = Math.max(1, Math.ceil(order.length / n));
  const p = Math.min(Math.max(0, page), pages - 1);
  const slice = order.slice(p * n, p * n + n);
  return { tiles: [...slice, ...Array(n - slice.length).fill(null)], pages };
}

/** A click on a small tile brings its camera into the first large cell for the session; the camera
 *  that was there takes the clicked tile's place. Returns the new order (unchanged when the layout
 *  has no large cell, or the camera is already in one). */
export function bringToLarge(order: string[], id: LayoutId, page: number, cameraId: string): string[] {
  const n = LAYOUTS[id].cells.length;
  if (largeCount(id) === 0) return order;
  const first = page * n;                         // the page's first cell is its first large cell
  const at = order.indexOf(cameraId);
  if (at < 0 || (at >= first && at < first + largeCount(id))) return order;
  const next = [...order];
  [next[first], next[at]] = [next[at], next[first]];
  return next;
}
