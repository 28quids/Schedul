// The selection model. Run with `node --test frontend/tests`.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  activeCell, bounds, cells, cellSelection, clampTo, columns, contains, isSingleCell, move, nextInRange, rows, selectRows, size, step, withActive,
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

/* ------------------------------------------------- walking a selected block --- */

test('Enter walks a block across the row, then down to the next', () => {
  // A2:C4, starting at the top-left. Excel's own order for a chosen rectangle:
  // every cell in it, without steering.
  const block = { anchor: { r: 0, c: 0 }, focus: { r: 2, c: 2 } };
  let selection = withActive(block, { r: 0, c: 0 });

  const walked = [];
  for (let i = 0; i < 9; i += 1) {
    const next = nextInRange(selection, 1);
    walked.push(`${next.r},${next.c}`);
    selection = withActive(block, next);
    assert.deepEqual(bounds(selection), bounds(block), 'the block never shrinks');
  }
  assert.deepEqual(walked, [
    '0,1', '0,2',
    '1,0', '1,1', '1,2',
    '2,0', '2,1', '2,2',
    '0,0',
  ], 'it wraps back to the top-left rather than falling out of the block');
});

test('Shift+Tab walks a block backwards', () => {
  const block = { anchor: { r: 0, c: 0 }, focus: { r: 2, c: 1 } };
  const back = nextInRange(withActive(block, { r: 1, c: 0 }), -1);
  assert.deepEqual(back, { r: 0, c: 1 }, 'back into the end of the row above');
});

test('the caret and the block are different things', () => {
  const block = { anchor: { r: 0, c: 0 }, focus: { r: 2, c: 2 } };
  assert.deepEqual(activeCell(block), block.focus, 'without a caret, the focus is it');
  const typing = withActive(block, { r: 1, c: 1 });
  assert.deepEqual(activeCell(typing), { r: 1, c: 1 });
  assert.deepEqual(bounds(typing), bounds(block));
});

test('a plain arrow leaves from the caret, not the corner of the block', () => {
  const block = withActive(
    { anchor: { r: 0, c: 0 }, focus: { r: 4, c: 4 } }, { r: 1, c: 1 }
  );
  const moved = move(block, 1, 0, { rowCount: 9, columnCount: 9 });
  assert.deepEqual(moved.focus, { r: 2, c: 1 });
  const extended = move(block, 1, 0, { rowCount: 9, columnCount: 9 }, { extend: true });
  assert.deepEqual(extended.focus, { r: 5, c: 4 }, 'extending still moves the drag end');
});

test('a single cell is not a block, so Enter keeps its old meaning', () => {
  assert.equal(nextInRange(cellSelection(3, 2), 1), null);
  assert.equal(nextInRange(null, 1), null);
});

test('walking a block ignores where the anchor happens to be', () => {
  // Dragging bottom-right to top-left puts the anchor after the focus. The
  // block is the same rectangle either way.
  const dragged = { anchor: { r: 2, c: 2 }, focus: { r: 1, c: 1 } };
  assert.deepEqual(nextInRange(dragged, 1), { r: 1, c: 2 });
});

test('extending leaves the caret where the typing was', () => {
  // Shift+Down from A1 selects the column and leaves you in A1, as a
  // spreadsheet does — so Enter then walks the block from its start rather
  // than from whichever corner the drag ended on.
  const extended = move(cellSelection(0, 0), 1, 0, { rowCount: 5, columnCount: 5 }, { extend: true });
  assert.deepEqual(activeCell(extended), { r: 0, c: 0 });
  assert.deepEqual(extended.focus, { r: 1, c: 0 });
  assert.deepEqual(nextInRange(extended, 1), { r: 1, c: 0 }, 'and Enter goes down into it');
});
