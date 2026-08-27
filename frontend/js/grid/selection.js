// A spreadsheet selection: one active cell, and a rectangle around it.
//
// Kept as plain data with no DOM in sight, because this is the part that has to
// be right. "Shift+Down twice then Left once" has an exact answer, and working
// it out against a live grid means the only way to check it is by hand.
//
// A selection is an anchor and a focus. The anchor is where the selection
// started and does not move while it is being extended; the focus is the active
// cell and is what the keyboard moves. The rectangle is whatever lies between
// them, which is why extending back past the anchor shrinks the selection
// rather than growing it the other way.

/** A single-cell selection at `r, c`. */
export function cellSelection(r, c) {
  return { anchor: { r, c }, focus: { r, c } };
}

/** The rectangle a selection covers, as inclusive bounds. */
export function bounds(selection) {
  const { anchor, focus } = selection;
  return {
    top: Math.min(anchor.r, focus.r),
    bottom: Math.max(anchor.r, focus.r),
    left: Math.min(anchor.c, focus.c),
    right: Math.max(anchor.c, focus.c),
  };
}

export function isSingleCell(selection) {
  const b = bounds(selection);
  return b.top === b.bottom && b.left === b.right;
}

export function size(selection) {
  const b = bounds(selection);
  return { rows: b.bottom - b.top + 1, columns: b.right - b.left + 1 };
}

export function contains(selection, r, c) {
  const b = bounds(selection);
  return r >= b.top && r <= b.bottom && c >= b.left && c <= b.right;
}

/** Every cell in the selection, in reading order. */
export function cells(selection) {
  const b = bounds(selection);
  const out = [];
  for (let r = b.top; r <= b.bottom; r += 1) {
    for (let c = b.left; c <= b.right; c += 1) out.push({ r, c });
  }
  return out;
}

/** The row indices the selection touches. */
export function rows(selection) {
  const b = bounds(selection);
  const out = [];
  for (let r = b.top; r <= b.bottom; r += 1) out.push(r);
  return out;
}

/** The column indices the selection touches. */
export function columns(selection) {
  const b = bounds(selection);
  const out = [];
  for (let c = b.left; c <= b.right; c += 1) out.push(c);
  return out;
}

const clamp = (value, max) => Math.max(0, Math.min(value, max));

/**
 * Move the focus, optionally extending the selection instead of collapsing it.
 *
 * Returns null when the move would leave the grid, so the caller can decide
 * what running off the bottom means — on the last row that is "start a new row",
 * which is a decision about schedules rather than about selections.
 */
export function move(selection, dr, dc, extent, { extend = false } = {}) {
  const { rowCount, columnCount } = extent;
  if (!rowCount || !columnCount) return null;

  const r = selection.focus.r + dr;
  const c = selection.focus.c + dc;
  if (r < 0 || r >= rowCount || c < 0 || c >= columnCount) return null;

  const focus = { r, c };
  return extend ? { anchor: { ...selection.anchor }, focus } : cellSelection(r, c);
}

/**
 * Move one cell along, wrapping to the next or previous row at the edges.
 *
 * This is Tab, and the wrap is what makes tabbing along a row of a schedule
 * continue onto the next one rather than stopping dead in the last column.
 */
export function step(selection, dc, extent) {
  const { rowCount, columnCount } = extent;
  if (!rowCount || !columnCount) return null;

  let r = selection.focus.r;
  let c = selection.focus.c + dc;
  if (c >= columnCount) { c = 0; r += 1; }
  if (c < 0) { c = columnCount - 1; r -= 1; }
  if (r < 0 || r >= rowCount) return null;
  return cellSelection(r, c);
}

/** Grow the selection to whole rows, which is what clicking a row number means. */
export function selectRows(top, bottom, columnCount) {
  return {
    anchor: { r: top, c: 0 },
    focus: { r: bottom, c: Math.max(0, columnCount - 1) },
  };
}

/** Keep a selection valid after the row or column count has changed. */
export function clampTo(selection, extent) {
  const { rowCount, columnCount } = extent;
  if (!selection || !rowCount || !columnCount) return null;
  const fix = (cell) => ({
    r: clamp(cell.r, rowCount - 1),
    c: clamp(cell.c, columnCount - 1),
  });
  return { anchor: fix(selection.anchor), focus: fix(selection.focus) };
}
