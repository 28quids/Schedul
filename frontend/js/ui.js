// Small DOM helpers. No framework: the app is a handful of screens over a REST
// API, and a build step would be one more thing to install on a Windows laptop.

/** Create an element. `attrs.class`, `attrs.on` (event map) and `attrs.html` are special. */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'on') {
      for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
    } else if (key === 'dataset') {
      Object.assign(node.dataset, value);
    } else if (node.tagName === 'TEXTAREA' && key === 'value') {
      // A textarea's value is its text content, not an attribute. HTML has no
      // `value` attribute on one at all, so `setAttribute('value', ...)` writes
      // something the browser silently ignores and the box comes up empty. That
      // is what emptied every notes box in the app, and worse, a Save then wrote
      // the emptiness back over what was really there.
      node.value = value ?? '';
    } else if (key in node && key !== 'list' && typeof value !== 'string') {
      node[key] = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(typeof child === 'object' ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function mount(node) {
  const main = document.getElementById('main');
  clear(main);
  main.appendChild(node);
  main.scrollTop = 0;
  return node;
}

/** A labelled form control. */
export function field(label, control, help) {
  return el('div', { class: 'field' }, [
    el('label', { text: label }),
    control,
    help ? el('span', { class: 'help', text: help }) : null,
  ]);
}

export function input(value, attrs = {}) {
  return el('input', { type: 'text', value: value ?? '', ...attrs });
}

/**
 * A multi-line box, with its content set the way a textarea actually takes it.
 *
 * Exists so nobody has to remember that one: `input()` has a counterpart, and
 * every notes box in the app goes through it.
 */
export function textarea(value, attrs = {}) {
  const node = el('textarea', { rows: 6, ...attrs });
  node.value = value ?? '';
  return node;
}

export function select(options, value, attrs = {}) {
  const node = el('select', attrs);
  for (const option of options) {
    const [val, label] = Array.isArray(option) ? option : [option, option];
    node.appendChild(el('option', { value: val, text: label, selected: val === value }));
  }
  node.value = value ?? '';
  return node;
}

export function button(label, attrs = {}) {
  return el('button', { class: 'btn', type: 'button', ...attrs }, [label]);
}

export function pill(text, tone = 'quiet') {
  return el('span', { class: `pill pill-${tone}`, text });
}

export function notice(text, tone = 'info', items = []) {
  return el('div', { class: `notice notice-${tone}` }, [
    el('div', { text }),
    items.length ? el('ul', {}, items.map((i) => el('li', { text: i }))) : null,
  ]);
}

/**
 * The head of a screen: what it is, what it is for, and what you can do to it.
 *
 * Every screen builds this the same way, because a page whose title sits in a
 * slightly different place from the last one costs a beat of attention every
 * time somebody moves between them. Actions go on the right, in a `btn-row` even
 * when there is one of them, so a screen that grows a second button does not
 * shuffle the first.
 *
 * The scheme for `actions`, top to bottom of the page and left to right within
 * a row:
 *
 * - **`btn-primary`** — at most one per row: the thing this screen is for.
 * - **`btn`** — everything else.
 * - **`btn-danger`** — anything that removes something, and always last.
 */
export function pageHead(title, sub, actions = []) {
  const list = (Array.isArray(actions) ? actions : [actions]).filter(Boolean);
  return el('header', { class: 'page-head' }, [
    el('div', {}, [
      el('h1', { text: title }),
      sub ? (typeof sub === 'string' ? el('div', { class: 'sub', text: sub }) : el('div', { class: 'sub' }, [sub])) : null,
    ]),
    list.length ? el('div', { class: 'btn-row' }, list) : null,
  ]);
}

/**
 * A row of buttons in labelled groups, separated by a rule.
 *
 * `groups` is `[[label, [buttons]], ...]`; anything after a `null` group goes to
 * the right-hand end. Used wherever a screen has more than about four actions:
 * grouping them by what they act on is what stops "where is delete" being a
 * different answer on each screen.
 */
export function toolbar(groups, trailing = []) {
  const nodes = groups
    .filter(([, buttons]) => buttons.filter(Boolean).length)
    .map(([label, buttons]) =>
      el('div', { class: 'tool-group', title: label }, buttons.filter(Boolean)));
  const end = (Array.isArray(trailing) ? trailing : [trailing]).filter(Boolean);
  if (end.length) nodes.push(el('div', { class: 'toolbar-end' }, end));
  return el('div', { class: 'sheet-toolbar' }, nodes);
}

export function card(title, body, actions = [], hint = '') {
  return el('section', { class: 'card' }, [
    title
      ? el('header', { class: 'card-head' }, [
          el('div', {}, [
            el('h2', { text: title }),
            hint ? el('div', { class: 'hint', text: hint }) : null,
          ]),
          actions.length ? el('div', { class: 'btn-row' }, actions) : null,
        ])
      : null,
    el('div', { class: 'card-body' }, [body]),
  ]);
}

export function table(headers, rows) {
  return el('div', { class: 'table-wrap' }, [
    el('table', {}, [
      el('thead', {}, [
        el('tr', {}, headers.map((h) =>
          typeof h === 'object'
            ? el('th', { class: h.class || '', text: h.text })
            : el('th', { text: h })
        )),
      ]),
      el('tbody', {}, rows),
    ]),
  ]);
}

export function empty(title, message, action) {
  return el('div', { class: 'empty' }, [
    el('h3', { text: title }),
    el('p', { class: 'muted', text: message }),
    action || null,
  ]);
}

export function toast(message, tone = '') {
  const node = el('div', { class: `toast ${tone}`.trim(), text: message });
  document.getElementById('toasts').appendChild(node);
  setTimeout(() => node.remove(), tone === 'err' ? 6500 : 3200);
}

/** Show an error without losing what the server actually said. */
export function fail(error) {
  console.error(error);
  toast(error && error.message ? error.message : String(error), 'err');
}

/**
 * A modal dialog. `render(close)` builds the body; `actions(close)` the footer.
 * Resolves when closed so callers can `await` a decision.
 */
export function modal({ title, render, actions, wide = false }) {
  return new Promise((resolve) => {
    const root = document.getElementById('modal-root');
    let settled = false;

    const close = (value) => {
      if (settled) return;
      settled = true;
      document.removeEventListener('keydown', onKey);
      clear(root);
      resolve(value);
    };
    const onKey = (event) => { if (event.key === 'Escape') close(undefined); };
    document.addEventListener('keydown', onKey);

    const box = el('div', { class: `modal${wide ? ' wide' : ''}` }, [
      el('header', { class: 'modal-head' }, [
        el('h2', { text: title }),
        el('button', { class: 'icon-btn', title: 'Close', on: { click: () => close(undefined) } }, ['×']),
      ]),
      el('div', { class: 'modal-body' }, [render(close)]),
      el('footer', { class: 'modal-foot' }, actions ? actions(close) : [
        button('Close', { on: { click: () => close(undefined) } }),
      ]),
    ]);

    const backdrop = el('div', {
      class: 'modal-backdrop',
      on: { mousedown: (e) => { if (e.target === backdrop) close(undefined); } },
    }, [box]);

    clear(root).appendChild(backdrop);
    const first = box.querySelector('input, select, textarea, button.btn-primary');
    if (first) first.focus();
  });
}

/** Confirm a consequential action, naming the consequence rather than "are you sure". */
export async function confirmDialog({ title, message, confirmLabel = 'Confirm', danger = false, detail }) {
  return (await modal({
    title,
    render: () => el('div', {}, [
      el('p', { text: message }),
      detail || null,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button(confirmLabel, {
        class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`,
        on: { click: () => close(true) },
      }),
    ],
  })) === true;
}

/** Format a value for display without turning 0 into an empty cell. */
export function show(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return String(value);
    return value.toFixed(2);
  }
  return String(value);
}

export function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

/** Debounce, for save-as-you-type without a request per keystroke. */
export function debounce(fn, wait = 400) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  wrapped.flush = (...args) => { clearTimeout(timer); fn(...args); };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

export function download(url) {
  const link = el('a', { href: url, download: '' });
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/**
 * A dropdown anchored to an element but rendered at the top of the document.
 *
 * The schedule grid scrolls inside `overflow: auto`, so an absolutely
 * positioned list inside a cell is clipped by the container and vanishes on the
 * last row. Rendering into `document.body` with `position: fixed` escapes the
 * clip; the trade-off is that the position has to be recomputed on scroll and
 * resize, which is what `reposition` does.
 *
 * Flips above the anchor when there is not enough room below.
 */
export function anchoredList(anchor, { maxHeight = 260 } = {}) {
  const list = el('div', { class: 'ac-list ac-portal' });
  document.body.appendChild(list);

  const reposition = () => {
    const box = anchor.getBoundingClientRect();
    const below = window.innerHeight - box.bottom;
    const wanted = Math.min(maxHeight, list.scrollHeight || maxHeight);
    const flip = below < wanted + 12 && box.top > below;

    list.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - 300))}px`;
    list.style.minWidth = `${Math.max(box.width, 240)}px`;
    list.style.maxHeight = `${Math.min(wanted, flip ? box.top - 12 : below - 12)}px`;
    if (flip) {
      list.style.top = 'auto';
      list.style.bottom = `${window.innerHeight - box.top + 2}px`;
    } else {
      list.style.bottom = 'auto';
      list.style.top = `${box.bottom + 2}px`;
    }
  };

  // Capture phase, because the scrolling element is an ancestor of the anchor
  // and does not bubble scroll events.
  const onScroll = () => reposition();
  window.addEventListener('scroll', onScroll, true);
  window.addEventListener('resize', onScroll);

  return {
    node: list,
    reposition,
    close() {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
      list.remove();
    },
  };
}
