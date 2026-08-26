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
