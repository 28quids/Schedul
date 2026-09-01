// Copying a block out of the grid, and planning one back into it.

import test from 'node:test';
import assert from 'node:assert/strict';

import { parseTsv, planBlockPaste, selectionMatrix, toTsv } from '../js/grid/clipboard.js';

const columns = [
  { legacy_name: 'Unit Reference', editable: true, kind: 'input' },
  { legacy_name: 'Location', editable: true, kind: 'input' },
  { legacy_name: 'Total Airflow (l/s)', editable: false, kind: 'derived' },
];

const gridRows = () => [
  { id: 'r1', values: { 'Unit Reference': 'A' }, computed: { 'Total Airflow (l/s)': 900 } },
  { id: 'r2', values: {}, computed: {} },
];

test('cells go out as tab-separated text', () => {
  assert.equal(toTsv([['A', 'Roof'], ['B', '']]), 'A\tRoof\nB\t');
});

test('a cell carrying a tab cannot silently become two cells', () => {
  assert.equal(toTsv([['a\tb']]), 'a b');
  assert.equal(toTsv([['line\none']]), 'line one');
});

test('blank and missing values come out empty rather than as undefined', () => {
  assert.equal(toTsv([[null, undefined, 0]]), '\t\t0');
});

test('pasted text parses back into a matrix', () => {
  assert.deepEqual(parseTsv('A\tRoof\r\nB\tPlant\n'), [['A', 'Roof'], ['B', 'Plant']]);
});

test('a block lands on the rows under it, skipping calculated columns', () => {
  const plan = planBlockPaste({
    matrix: [['X', 'Roof', '999'], ['Y', 'Plant', '999']],
    rows: gridRows(), columns, top: 0, left: 0,
  });
  assert.deepEqual(plan.edits, [
    { row_id: 'r1', values: { 'Unit Reference': 'X', Location: 'Roof' } },
    { row_id: 'r2', values: { 'Unit Reference': 'Y', Location: 'Plant' } },
  ]);
  assert.equal(plan.skipped, 2, 'the calculated column is skipped, not shifted into');
  assert.equal(plan.cells, 4);
});

test('the plan counts what it would overwrite before anything is written', () => {
  const plan = planBlockPaste({
    matrix: [['X'], ['Y']], rows: gridRows(), columns, top: 0, left: 0,
  });
  assert.equal(plan.overwritten, 1, 'only the row that already had a value counts');
});

test('a block starting part-way across lands in the right columns', () => {
  const plan = planBlockPaste({
    matrix: [['Roof']], rows: gridRows(), columns, top: 1, left: 1,
  });
  assert.deepEqual(plan.edits, [{ row_id: 'r2', values: { Location: 'Roof' } }]);
});

test('rows past the end of the schedule are reported as overflow', () => {
  const plan = planBlockPaste({
    matrix: [['A'], ['B'], ['C'], ['D']], rows: gridRows(), columns, top: 0, left: 0,
  });
  assert.equal(plan.overflow, 2);
  assert.deepEqual(plan.overflowRows, [['C'], ['D']]);
  assert.equal(plan.edits.length, 2, 'only the rows that exist are edited');
});

test('a block entirely on calculated columns produces no edits at all', () => {
  const plan = planBlockPaste({
    matrix: [['999']], rows: gridRows(), columns, top: 0, left: 2,
  });
  assert.deepEqual(plan.edits, []);
  assert.equal(plan.skipped, 1);
});

test('a selection reads typed values and computed ones alike', () => {
  const matrix = selectionMatrix({
    rows: gridRows(),
    columns,
    bounds: { top: 0, bottom: 0, left: 0, right: 2 },
    display: (row, column) =>
      column.editable ? (row.values[column.legacy_name] ?? '') : row.computed[column.legacy_name],
  });
  assert.deepEqual(matrix, [['A', '', 900]]);
});

/* ------------------------------------------------ repeating across a range --- */

const row = (id) => ({ id, values: {}, computed: {} });
const editable = (name) => ({ legacy_name: name, editable: true, kind: 'input' });

test('one copied cell fills the whole selection, as a spreadsheet does', () => {
  const rows = [row('a'), row('b'), row('c'), row('d')];
  const plan = planBlockPaste({
    matrix: [['450']],
    rows,
    columns: [editable('Supply Airflow (l/s)')],
    top: 0,
    left: 0,
    selection: { top: 0, bottom: 3, left: 0, right: 0 },
  });
  assert.equal(plan.edits.length, 4, 'every selected cell takes the value');
  assert.equal(plan.cells, 4);
  assert.equal(plan.repeated, true);
  assert.equal(plan.overflow, 0, 'a repeat is bounded by the selection');
});

test('a block repeats across as well as down', () => {
  const rows = [row('a'), row('b')];
  const plan = planBlockPaste({
    matrix: [['x', 'y']],
    rows,
    columns: [editable('A'), editable('B'), editable('C'), editable('D')],
    top: 0,
    left: 0,
    selection: { top: 0, bottom: 1, left: 0, right: 3 },
  });
  assert.deepEqual(plan.edits[0].values, { A: 'x', B: 'y', C: 'x', D: 'y' });
  assert.equal(plan.cells, 8);
});

test('a block that does not divide the selection is pasted once', () => {
  // Excel refuses to guess here, and so does this: two rows into five is not a
  // repeat anybody asked for.
  const rows = [row('a'), row('b'), row('c'), row('d'), row('e')];
  const plan = planBlockPaste({
    matrix: [['1'], ['2']],
    rows,
    columns: [editable('A')],
    top: 0,
    left: 0,
    selection: { top: 0, bottom: 4, left: 0, right: 0 },
  });
  assert.equal(plan.edits.length, 2);
  assert.equal(plan.repeated, false);
});

test('a paste with no selection given behaves as it always did', () => {
  const rows = [row('a'), row('b')];
  const plan = planBlockPaste({
    matrix: [['1'], ['2']],
    rows,
    columns: [editable('A')],
    top: 0,
    left: 0,
  });
  assert.equal(plan.edits.length, 2);
  assert.equal(plan.repeated, false);
});
