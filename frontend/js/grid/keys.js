// What a keystroke in the grid means.
//
// Separated from the handler that carries it out for the same reason the
// selection model is: the rules are fiddly — Left moves the caret until it
// reaches the edge of the text and only then moves cell — and a rule that is
// only exercised by hand is a rule that quietly stops working.
//
// `decide` takes a plain description of the event and of where the caret is,
// and returns what should happen. It touches nothing.

/**
 * @param event  { key, shiftKey, ctrlKey, metaKey, altKey }
 * @param caret  { atStart, atEnd, editing } — editing is false for a read-only cell
 * @returns { type, ...args } where type is one of:
 *   'move'        dr, dc, extend
 *   'step'        dc            (Tab: moves one cell, wrapping at the edges)
 *   'enter'       (down one row, back to where a run of tabbing began)
 *   'fill-down' | 'copy' | 'paste' | 'clear' | 'undo' | 'redo' |
 *   'select-all' | 'escape' | 'none'
 */
export function decide(event, caret = {}) {
  const { key } = event;
  const mod = event.ctrlKey || event.metaKey;
  const shift = Boolean(event.shiftKey);
  const editing = caret.editing !== false;
  const atStart = Boolean(caret.atStart);
  const atEnd = Boolean(caret.atEnd);

  if (mod && !event.altKey) {
    const lower = String(key).toLowerCase();
    // Ctrl+D copies down, as Excel does. Excel's incrementing lives on the fill
    // handle, and binding it here would silently turn a column of 'Level 02'
    // into 03, 04, 05.
    if (lower === 'd') return { type: 'fill-down', mode: 'copy' };
    if (lower === 'c') return { type: 'copy' };
    if (lower === 'v') return { type: 'paste' };
    if (lower === 'a') return { type: 'select-all' };
    if (lower === 'z') return { type: shift ? 'redo' : 'undo' };
    if (lower === 'y') return { type: 'redo' };
    return { type: 'none' };
  }

  if (key === 'Tab') return { type: 'step', dc: shift ? -1 : 1 };
  if (key === 'Enter') return { type: 'enter' };
  if (key === 'Escape') return { type: 'escape' };

  if (key === 'ArrowDown') return { type: 'move', dr: 1, dc: 0, extend: shift };
  if (key === 'ArrowUp') return { type: 'move', dr: -1, dc: 0, extend: shift };

  // Left and right move the caret through the text first and only step out of
  // the cell at the edge. Extending a selection is the exception: Shift+Arrow
  // is unambiguously about cells, so it never has to reach the edge first.
  if (key === 'ArrowRight') {
    if (shift) return { type: 'move', dr: 0, dc: 1, extend: true };
    if (!editing || atEnd) return { type: 'move', dr: 0, dc: 1, extend: false };
    return { type: 'none' };
  }
  if (key === 'ArrowLeft') {
    if (shift) return { type: 'move', dr: 0, dc: -1, extend: true };
    if (!editing || atStart) return { type: 'move', dr: 0, dc: -1, extend: false };
    return { type: 'none' };
  }

  // Clearing a range is a range operation; inside a cell being edited, Delete
  // is just Delete and the browser handles it.
  if (key === 'Delete' || key === 'Backspace') {
    return editing && !caret.rangeSelected ? { type: 'none' } : { type: 'clear' };
  }

  return { type: 'none' };
}

/** Whether an action needs the browser's default behaviour suppressed. */
export function preventsDefault(action) {
  return action.type !== 'none';
}
