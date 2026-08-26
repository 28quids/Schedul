// What a keystroke in the grid means.

import test from 'node:test';
import assert from 'node:assert/strict';

import { decide } from '../js/grid/keys.js';

const typing = { editing: true, atStart: false, atEnd: false };
const readOnly = { editing: false };

test('arrows move the active cell', () => {
  assert.deepEqual(decide({ key: 'ArrowDown' }, typing), { type: 'move', dr: 1, dc: 0, extend: false });
  assert.deepEqual(decide({ key: 'ArrowUp' }, typing), { type: 'move', dr: -1, dc: 0, extend: false });
});

test('shift+arrow extends the selection', () => {
  assert.equal(decide({ key: 'ArrowDown', shiftKey: true }, typing).extend, true);
  assert.equal(decide({ key: 'ArrowRight', shiftKey: true }, typing).extend, true);
  assert.equal(decide({ key: 'ArrowLeft', shiftKey: true }, typing).extend, true);
});

test('left and right move the caret before they move the cell', () => {
  assert.equal(decide({ key: 'ArrowRight' }, typing).type, 'none');
  assert.equal(decide({ key: 'ArrowLeft' }, typing).type, 'none');

  assert.deepEqual(
    decide({ key: 'ArrowRight' }, { ...typing, atEnd: true }),
    { type: 'move', dr: 0, dc: 1, extend: false }
  );
  assert.deepEqual(
    decide({ key: 'ArrowLeft' }, { ...typing, atStart: true }),
    { type: 'move', dr: 0, dc: -1, extend: false }
  );
});

test('a read-only cell has no caret to move through', () => {
  assert.equal(decide({ key: 'ArrowRight' }, readOnly).type, 'move');
  assert.equal(decide({ key: 'ArrowLeft' }, readOnly).type, 'move');
});

test('tab steps sideways and shift+tab steps back', () => {
  assert.deepEqual(decide({ key: 'Tab' }, typing), { type: 'step', dc: 1 });
  assert.deepEqual(decide({ key: 'Tab', shiftKey: true }, typing), { type: 'step', dc: -1 });
});

test('enter is its own action, because it returns to where tabbing began', () => {
  assert.deepEqual(decide({ key: 'Enter' }, typing), { type: 'enter' });
});

test('ctrl+d fills down by copying, never by counting up', () => {
  assert.deepEqual(decide({ key: 'd', ctrlKey: true }, typing), { type: 'fill-down', mode: 'copy' });
  assert.deepEqual(decide({ key: 'D', metaKey: true }, typing), { type: 'fill-down', mode: 'copy' });
});

test('undo and redo are bound the way every editor binds them', () => {
  assert.equal(decide({ key: 'z', ctrlKey: true }, typing).type, 'undo');
  assert.equal(decide({ key: 'z', ctrlKey: true, shiftKey: true }, typing).type, 'redo');
  assert.equal(decide({ key: 'y', ctrlKey: true }, typing).type, 'redo');
});

test('copy, paste and select-all are recognised', () => {
  assert.equal(decide({ key: 'c', ctrlKey: true }, typing).type, 'copy');
  assert.equal(decide({ key: 'v', ctrlKey: true }, typing).type, 'paste');
  assert.equal(decide({ key: 'a', ctrlKey: true }, typing).type, 'select-all');
});

test('delete inside a cell being edited stays the browser’s job', () => {
  assert.equal(decide({ key: 'Delete' }, { ...typing, rangeSelected: false }).type, 'none');
  assert.equal(decide({ key: 'Backspace' }, { ...typing, rangeSelected: false }).type, 'none');
});

test('delete over a range clears the range', () => {
  assert.equal(decide({ key: 'Delete' }, { ...typing, rangeSelected: true }).type, 'clear');
  assert.equal(decide({ key: 'Delete' }, readOnly).type, 'clear');
});

test('an ordinary character is left alone so typing works', () => {
  assert.equal(decide({ key: 'a' }, typing).type, 'none');
  assert.equal(decide({ key: '4' }, typing).type, 'none');
});

test('alt combinations are not stolen from the operating system', () => {
  assert.equal(decide({ key: 'd', ctrlKey: true, altKey: true }, typing).type, 'none');
});
