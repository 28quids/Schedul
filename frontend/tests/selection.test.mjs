// The selection model. Run with `node --test frontend/tests`.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  bounds, cells, cellSelection, clampTo, columns, contains, isSingleCell, move,
  rows, selectRows, size, step,
} from '../js/grid/selection.js';

const grid = { rowCount: 4, columnCount: 3 };

test('a new selection is one cell', () => {
  const sel = cellSelection(1, 2);
  assert.equal(isSingleCell(sel), true);
  assert.deepEqual(size(sel), { rows: 1, columns: 1 });
  assert.deepEqual(bounds(sel), { top: 1, bottom: 1, left: 2, right: 2 });
});

test('an arrow moves the active cell and collapses the selection', () => {
  const sel = move(cellSelection(0, 0), 1, 0, grid);
  assert.deepEqual(sel.focus, { r: 1, c: 0 });
  assert.equal(isSingleCell(sel), true);
});

test('shift+arrow extends from the anchor without moving it', () => {
  let sel = cellSelection(1, 1);
  sel = move(sel, 1, 0, grid, { extend: true });
  sel = move(sel, 1, 0, grid, { extend: true });
  assert.deepEqual(sel.anchor, { r: 1, c: 1 });
  assert.deepEqual(bounds(sel), { top: 1, bottom: 3, left: 1, right: 1 });
});

test('extending back past the anchor shrinks rather than flipping', () => {
  let sel = cellSelection(2, 0);
  sel = move(sel, -1, 0, grid, { extend: true });
  sel = move(sel, -1, 0, grid, { extend: true });
  assert.deepEqual(bounds(sel), { top: 0, bottom: 2, left: 0, right: 0 });
  assert.deepEqual(sel.focus, { r: 0, c: 0 });
});

test('moving off the edge of the grid returns nothing to do', () => {
  assert.equal(move(cellSelection(0, 0), -1, 0, grid), null);
  assert.equal(move(cellSelection(3, 2), 0, 1, grid), null);
  assert.equal(move(cellSelection(0, 0), 1, 0, { rowCount: 0, columnCount: 0 }), null);
});

test('tab wraps to the next row at the last column', () => {
  const sel = step(cellSelection(0, 2), 1, grid);
  assert.deepEqual(sel.focus, { r: 1, c: 0 });
});

test('shift+tab wraps back to the end of the previous row', () => {
  const sel = step(cellSelection(1, 0), -1, grid);
  assert.deepEqual(sel.focus, { r: 0, c: 2 });
});

test('tab off the last cell of the last row goes nowhere', () => {
  assert.equal(step(cellSelection(3, 2), 1, grid), null);
  assert.equal(step(cellSelection(0, 0), -1, grid), null);
});

test('a rectangle lists its cells in reading order', () => {
  const sel = { anchor: { r: 0, c: 0 }, focus: { r: 1, c: 1 } };
  assert.deepEqual(cells(sel), [
    { r: 0, c: 0 }, { r: 0, c: 1 }, { r: 1, c: 0 }, { r: 1, c: 1 },
  ]);
  assert.deepEqual(rows(sel), [0, 1]);
  assert.deepEqual(columns(sel), [0, 1]);
});

test('contains answers for cells inside and outside the rectangle', () => {
  const sel = { anchor: { r: 1, c: 1 }, focus: { r: 2, c: 2 } };
  assert.equal(contains(sel, 1, 1), true);
  assert.equal(contains(sel, 2, 2), true);
  assert.equal(contains(sel, 0, 1), false);
  assert.equal(contains(sel, 1, 3), false);
});

test('selecting a row covers every column', () => {
  const sel = selectRows(2, 2, 3);
  assert.deepEqual(bounds(sel), { top: 2, bottom: 2, left: 0, right: 2 });
});

test('a selection survives the grid shrinking under it', () => {
  const sel = { anchor: { r: 3, c: 2 }, focus: { r: 3, c: 2 } };
  const clamped = clampTo(sel, { rowCount: 2, columnCount: 2 });
  assert.deepEqual(clamped.focus, { r: 1, c: 1 });
  assert.equal(clampTo(sel, { rowCount: 0, columnCount: 0 }), null);
  assert.equal(clampTo(null, grid), null);
});
