// What `el()` puts where, on the two elements that disagree about it.
//
// This exists because of a bug that emptied every notes box in the app. A
// textarea has no `value` attribute in HTML — its value is its text content —
// so `setAttribute('value', ...)` is written, ignored, and the box comes up
// blank. Worse than blank: the Save button beside it then wrote the emptiness
// back over the notes that were really there.
//
// `ui.js` touches the DOM, so it cannot be imported the way the grid modules
// are. The stand-in below is the smallest thing that tells a property from an
// attribute, which is the whole of what is being asserted.

import test from 'node:test';
import assert from 'node:assert/strict';

class FakeNode {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.dataset = {};
    this.listeners = {};
    if (this.tagName === 'INPUT' || this.tagName === 'TEXTAREA') this.value = '';
  }

  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key]; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  appendChild(child) { this.children.push(child); return child; }
}

globalThis.document = { createElement: (tag) => new FakeNode(tag) };

const { el, textarea, input } = await import('../js/ui.js');

test('a textarea takes its value as a property, not an attribute', () => {
  const box = el('textarea', { rows: 4, value: 'one\ntwo' });
  assert.equal(box.value, 'one\ntwo');
  assert.equal(
    box.getAttribute('value'), undefined,
    'HTML has no value attribute on a textarea; writing one is writing nothing'
  );
});

test('an input still takes its value as an attribute', () => {
  // Inputs do have the attribute, and it is what makes the value visible in the
  // DOM. Fixing the textarea must not quietly change this one.
  assert.equal(input('RAD-001').getAttribute('value'), 'RAD-001');
});

test('the textarea helper carries the text and the attributes', () => {
  const box = textarea('a note', { rows: 2, placeholder: 'One per line' });
  assert.equal(box.value, 'a note');
  assert.equal(box.getAttribute('rows'), '2');
  assert.equal(box.getAttribute('placeholder'), 'One per line');
});

test('an empty value is empty rather than the string "undefined"', () => {
  assert.equal(textarea(undefined).value, '');
  assert.equal(textarea(null).value, '');
  assert.equal(el('textarea', { value: '' }).value, '');
});

test('a value that is only a newline survives', () => {
  // The notes boxes join a list with newlines, so a single empty note is one
  // newline and nothing else. It must not be mistaken for "no value at all".
  assert.equal(textarea('\n').value, '\n');
});
