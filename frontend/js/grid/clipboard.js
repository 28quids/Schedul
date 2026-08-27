// Copying a block of cells out, and working out what pasting one back in does.
//
// The schedule-level paste — a whole block of new rows — is planned by the
// backend, because that is a decision about the record. This is the other kind:
// a rectangle of cells dropped onto a rectangle of existing ones, which is a
// grid interaction and stays here. It still refuses to be silent about it: the
// plan says how many filled cells would be overwritten before anything is sent.

/** Cells as tab-separated text, which is what a spreadsheet reads. */
export function toTsv(matrix) {
  return matrix
    .map((row) => row.map((cell) => formatCell(cell)).join('\t'))
    .join('\n');
}

function formatCell(value) {
  if (value === null || value === undefined) return '';
  const text = String(value);
  // A cell carrying a tab or a newline would silently become several cells.
  return /[\t\n\r]/.test(text) ? text.replace(/[\t\n\r]+/g, ' ') : text;
}

/** Tab-separated text back into a matrix of trimmed strings. */
export function parseTsv(text) {
  return String(text ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .split('\n')
    .filter((line, index, all) => line !== '' || index < all.length - 1)
    .map((line) => line.split('\t'));
}

/**
 * What pasting `matrix` at `top,left` would do to the rows already there.
 *
 * `columns` is the grid's full column list; only the editable ones can receive
 * a value, so a block landing across a calculated column skips it rather than
 * shifting everything along — which would put values in the wrong fields.
 *
 * Returns edits ready for the cells endpoint, plus the counts a confirmation
 * needs: how many filled cells would be overwritten, and how many rows the
 * block runs past the end of the schedule.
 */
export function planBlockPaste({ matrix, rows, columns, top, left }) {
  const edits = [];
  let overwritten = 0;
  let skipped = 0;
  let filled = 0;

  matrix.forEach((line, dr) => {
    const rowIndex = top + dr;
    const row = rows[rowIndex];
    if (!row) return;  // counted as overflow below

    const values = {};
    line.forEach((cell, dc) => {
      const column = columns[left + dc];
      if (!column) return;
      if (!column.editable) { skipped += 1; return; }
      const key = column.legacy_name;
      const existing = row.values ? row.values[key] : undefined;
      if (existing !== undefined && existing !== null && existing !== '') overwritten += 1;
      values[key] = cell.trim();
      filled += 1;
    });
    if (Object.keys(values).length) edits.push({ row_id: row.id, values });
  });

  const overflow = Math.max(0, top + matrix.length - rows.length);
  return {
    edits,
    overwritten,
    skipped,
    cells: filled,
    overflow,
    // The rows that run past the end, as row-shaped objects the append path can
    // send on to the schedule paste endpoint.
    overflowRows: matrix.slice(Math.max(0, rows.length - top)),
    width: matrix.reduce((n, line) => Math.max(n, line.length), 0),
    height: matrix.length,
  };
}

/** The values a selection covers, ready for `toTsv`. */
export function selectionMatrix({ rows, columns, bounds, display }) {
  const out = [];
  for (let r = bounds.top; r <= bounds.bottom; r += 1) {
    const row = rows[r];
    if (!row) continue;
    const line = [];
    for (let c = bounds.left; c <= bounds.right; c += 1) {
      const column = columns[c];
      if (!column) continue;
      line.push(display(row, column));
    }
    out.push(line);
  }
  return out;
}
