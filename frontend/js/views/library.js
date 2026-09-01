// The shared equipment library, and the review queue.
//
// Entries go live the moment someone enters them on a schedule, so nobody is
// blocked mid-job. The queue ranks what needs a look rather than gating use:
// v1's submissions inbox existed to stop concurrent writes corrupting a shared
// .xlsx, which is not a problem a database has.

import { api } from '../api.js';
import { store } from '../app.js';
import {
  button, card, clear, confirmDialog, download, el, empty, fail, field, formatDate,
  input, modal, mount, notice, pageHead, pill, select, show, table, toast,
} from '../ui.js';
import { importProducts, importWorkbook, productGrid } from './library-entry.js';

const TONE = { CONFLICT: 'red', DRIFT: 'amber', INCOMPLETE: 'quiet', NEW: 'blue' };

let state = {
  tab: 'browse', code: null, query: '', sort: null, duplicatesOnly: false,
};

export async function libraryView() {
  const types = await api.catalogue.list();
  store.catalogue = types;
  if (!state.code && types.length) state.code = types[0].code;

  const page = el('div', { class: 'page page-wide' }, [
    // The whole-library round trip: everything out as one workbook, a sheet
    // per type, corrected in Excel and brought back.
    pageHead(
      'Equipment library',
      'Every product this practice has scheduled. Entered once, then available on every schedule.',
      [
        button('Blank workbook', {
          title: 'Every type’s headings and an example row, ready to fill in',
          on: { click: () => download(api.library.workbookUrl('', false)) },
        }),
        button('Export all', {
          title: 'The whole library as one workbook, a sheet per equipment type',
          on: { click: () => download(api.library.workbookUrl('', true)) },
        }),
        button('Import workbook…', {
          class: 'btn btn-primary',
          title: 'Read a filled-in workbook back, a sheet at a time',
          on: { click: async () => { if (await importWorkbook()) libraryView(); } },
        }),
      ]
    ),
    el('div', { class: 'tabs' }, [
      ['browse', 'Browse'],
      ['review', 'Needs review'],
    ].map(([key, label]) =>
      el('button', {
        class: `tab${state.tab === key ? ' active' : ''}`,
        on: { click: () => { state.tab = key; libraryView(); } },
      }, [label])
    )),
  ]);

  const body = el('div');
  page.appendChild(body);
  mount(page);

  if (state.tab === 'browse') await drawBrowse(body, types);
  else await drawReview(body);
}

/* ----------------------------------------------------------- browsing --- */

/** The key an entry's values are stored under, unit and all. */
const columnKey = (c) => (c.unit ? `${c.name} (${c.unit})` : c.name);

/** A value ordered the way somebody reading a column expects. */
function compareValues(a, b) {
  const left = a === null || a === undefined ? '' : a;
  const right = b === null || b === undefined ? '' : b;
  if (left === '' || right === '') return left === right ? 0 : (left === '' ? 1 : -1);
  const nl = Number(left);
  const nr = Number(right);
  // Numbers numerically, so 900 sorts after 1200 nowhere: a column of sizes
  // ordered as text is a column nobody can read.
  if (!Number.isNaN(nl) && !Number.isNaN(nr)) return nl - nr;
  return String(left).localeCompare(String(right), undefined, { numeric: true });
}

/**
 * Entries that look like the same product entered twice.
 *
 * The database already refuses two entries under one reference, so a true
 * duplicate cannot exist. What can, and what nobody could see, is the same unit
 * under two references: SYS-VSR-500 and SYSVSR500 with identical values. Those
 * are grouped by everything except the reference, and anything the server has
 * flagged is counted too.
 */
function duplicateIds(entries, libColumns) {
  const byShape = new Map();
  for (const entry of entries) {
    const shape = libColumns
      .map((c) => String(entry.values[columnKey(c)] ?? '').trim().toLowerCase())
      .join('\u0000');
    if (!shape.replace(/\u0000/g, '')) continue;  // an entry with nothing in it
    if (!byShape.has(shape)) byShape.set(shape, []);
    byShape.get(shape).push(entry.id);
  }
  const ids = new Set();
  for (const group of byShape.values()) {
    if (group.length > 1) group.forEach((id) => ids.add(id));
  }
  for (const entry of entries) {
    if (entry.flags.some((f) => f.kind === 'DUPLICATE' || f.kind === 'CONFLICT')) {
      ids.add(entry.id);
    }
  }
  return ids;
}

async function drawBrowse(root, types) {
  if (!types.length) {
    root.appendChild(card('', empty('No schedule types yet', 'Create one first.')));
    return;
  }

  const typeSelect = select(
    types.map((t) => [t.code, `${t.code} — ${t.title}`]),
    state.code,
    { on: { change: (e) => { state.code = e.target.value; state.query = ''; libraryView(); } } }
  );
  // Built once and never replaced. Rebuilding the page on every keystroke was
  // what threw the caret out of this box after the first letter.
  const search = input(state.query, {
    placeholder: 'Filter by reference or any value…',
    on: {
      input: () => { state.query = search.value; renderRows(); },
      keydown: (e) => { if (e.key === 'Escape') { search.value = ''; state.query = ''; renderRows(); } },
    },
  });

  // Everything for this type, fetched once. A practice's library is hundreds of
  // rows, not millions, so filtering and sorting happen here — which is what
  // makes typing in the box instant instead of a request per letter.
  const entries = await api.library.list(state.code, '');
  const type = await api.catalogue.read(types.find((t) => t.code === state.code).id);
  const libColumns = type.columns.filter((c) => c.kind === 'library');
  const shown = libColumns.slice(0, 6);
  const duplicates = duplicateIds(entries, libColumns);

  const body = el('tbody');
  const count = el('span', { class: 'muted tiny' });

  const onlyDuplicates = el('input', {
    type: 'checkbox',
    checked: state.duplicatesOnly,
    on: { change: (e) => { state.duplicatesOnly = e.target.checked; renderRows(); } },
  });

  const sortBy = (key) => {
    state.sort = state.sort && state.sort.key === key
      ? { key, dir: -state.sort.dir }
      : { key, dir: 1 };
    renderRows();
  };

  const heading = (label, key) => el('th', {
    class: `sortable${state.sort && state.sort.key === key ? ' sorted' : ''}`,
    title: `Sort by ${label}`,
    on: { click: () => sortBy(key) },
  }, [
    label,
    state.sort && state.sort.key === key
      ? el('span', { class: 'sort-arrow', text: state.sort.dir > 0 ? ' ▲' : ' ▼' })
      : null,
  ]);

  function matching() {
    const needle = state.query.trim().toLowerCase();
    let list = entries;
    if (needle) {
      list = list.filter((e) =>
        e.model_reference.toLowerCase().includes(needle)
        || Object.values(e.values).some((v) => String(v ?? '').toLowerCase().includes(needle))
      );
    }
    if (state.duplicatesOnly) list = list.filter((e) => duplicates.has(e.id));

    const sort = state.sort;
    const sorted = [...list];
    if (sort) {
      // Two of the columns are the entry's own fields rather than a product
      // value, so they are read from the entry rather than from its values.
      const read = (entry) => {
        if (sort.key === '') return entry.model_reference;
        if (sort.key === 'updated_at') return entry.updated_at;
        return entry.values[sort.key];
      };
      sorted.sort((a, b) => compareValues(read(a), read(b)) * sort.dir);
    } else {
      sorted.sort((a, b) => a.model_reference.localeCompare(b.model_reference, undefined, { numeric: true }));
    }
    return sorted;
  }

  function renderRows() {
    const list = matching();
    clear(body);
    for (const entry of list) body.appendChild(entryRow(entry, shown, duplicates));
    if (!list.length) {
      body.appendChild(el('tr', {}, [
        el('td', { colspan: String(shown.length + 3) }, [
          el('div', { class: 'muted', style: 'padding:14px', text:
            state.query || state.duplicatesOnly
              ? 'Nothing here matches.'
              : `No ${state.code} products yet. They are captured the first time somebody ` +
                'types a new Model Reference on a schedule.' }),
        ]),
      ]));
    }
    count.textContent =
      `${list.length} of ${entries.length} product(s)`
      + (duplicates.size ? ` · ${duplicates.size} may be duplicates` : '');
  }

  function entryRow(entry, columns, duplicateSet) {
    const suspect = duplicateSet.has(entry.id);
    return el('tr', { class: suspect ? 'suspect' : '' }, [
      el('td', {}, [
        el('strong', { class: 'mono', text: entry.model_reference }),
        entry.flags.length || suspect
          ? el('div', { style: 'margin-top:3px' }, [
              ...entry.flags.map((f) => pill(f.kind.toLowerCase(), TONE[f.kind] || 'quiet')),
              suspect && !entry.flags.length
                ? pill('same values as another', 'amber')
                : null,
            ])
          : null,
      ]),
      ...columns.map((c) =>
        el('td', { class: 'tiny', text: show(entry.values[columnKey(c)] ?? entry.values[c.name]) })
      ),
      el('td', { class: 'tiny muted nowrap', text: formatDate(entry.updated_at) }),
      el('td', { class: 'cell-actions' }, [
        el('div', { class: 'btn-row' }, [
          button('Edit', { class: 'btn btn-sm', on: { click: () => editEntry(entry, libColumns) } }),
          button('Variants', {
            class: 'btn btn-sm',
            title: 'Start a batch from this product, keeping its common fields',
            on: {
              click: async () => {
                const seed = {
                  'Model Reference': entry.model_reference,
                  ...Object.fromEntries(
                    libColumns.map((c) => [columnKey(c), entry.values[columnKey(c)] ?? ''])
                  ),
                };
                if (await productGrid(state.code, libColumns, { seed: [seed] })) libraryView();
              },
            },
          }),
          button('Remove', {
            class: 'btn btn-sm btn-danger',
            on: { click: () => removeEntry(entry) },
          }),
        ]),
      ]),
    ]);
  }

  root.appendChild(el('section', { class: 'card' }, [
    el('header', { class: 'card-head' }, [
      el('div', { class: 'library-filters' }, [
        typeSelect,
        search,
        el('label', { class: 'tiny nowrap', title: 'Entries that look like the same product entered twice' }, [
          onlyDuplicates, ' possible duplicates',
        ]),
      ]),
      el('div', { class: 'btn-row' }, [
        button(`Export ${state.code}`, {
          title: 'This type as a workbook, headings on row 1, ready to edit and bring back',
          on: { click: () => download(api.library.workbookUrl(state.code, true)) },
        }),
        button('Import…', {
          title: 'Paste a supplier’s list, or fill in the blank workbook and bring it back',
          on: { click: async () => { if (await importProducts(state.code)) libraryView(); } },
        }),
        button('Add products', {
          class: 'btn btn-primary',
          title: 'A table to type several products into at once',
          on: { click: async () => { if (await productGrid(state.code, libColumns)) libraryView(); } },
        }),
      ]),
    ]),
    el('div', { class: 'card-body tight' }, [
      el('div', { class: 'table-wrap' }, [
        el('table', {}, [
          el('thead', {}, [
            el('tr', {}, [
              heading('Model reference', ''),
              ...shown.map((c) => heading(columnKey(c), columnKey(c))),
              heading('Updated', 'updated_at'),
              el('th', {}),
            ]),
          ]),
          body,
        ]),
      ]),
      el('div', { style: 'padding:8px 2px 0' }, [count]),
    ]),
  ]));

  renderRows();
}

async function removeEntry(entry) {
  const ok = await confirmDialog({
    title: `Remove ${entry.model_reference}?`,
    message:
      'It stops appearing in lookups. It is not deleted, so a schedule that already ' +
      'references it keeps its record of what was chosen.',
    confirmLabel: 'Remove from library',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.library.remove(entry.id);
    toast('Removed', 'ok');
    libraryView();
  } catch (error) { fail(error); }
}

async function editEntry(entry, libColumns) {
  const inputs = {};
  for (const c of libColumns) {
    const key = c.unit ? `${c.name} (${c.unit})` : c.name;
    inputs[key] = input(show(entry.values[key] ?? entry.values[c.name]));
  }

  const ok = await modal({
    title: `Edit ${entry.model_reference}`,
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'Correcting a value here corrects every schedule that uses this product — the ' +
        'library is read, not copied.',
      ]),
      el('div', { class: 'grid-3' },
        libColumns.map((c) => {
          const key = c.unit ? `${c.name} (${c.unit})` : c.name;
          return field(key, inputs[key]);
        })),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Save', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok) return;

  try {
    await api.library.update(entry.id, {
      type_code: entry.type_code,
      model_reference: entry.model_reference,
      values: Object.fromEntries(Object.entries(inputs).map(([k, n]) => [k, n.value])),
    });
    toast('Updated', 'ok');
    libraryView();
  } catch (error) { fail(error); }
}

async function drawReview(root) {
  const queue = await api.library.queue();

  if (!queue.length) {
    root.appendChild(el('section', { class: 'card' }, [
      empty(
        'Nothing needs review',
        'New products are flagged here when they look like duplicates, conflict with an ' +
        'existing entry, or are spelled slightly differently from one already in the library.'
      ),
    ]));
    return;
  }

  root.appendChild(notice(
    `${queue.length} entr${queue.length === 1 ? 'y is' : 'ies are'} worth a look. They are ` +
    'already usable — this list ranks what to check, it does not hold anything back.',
    'info'
  ));

  for (const entry of queue) {
    root.appendChild(el('section', { class: 'card' }, [
      el('header', { class: 'card-head' }, [
        el('div', {}, [
          el('h2', {}, [
            el('span', { class: 'mono', text: entry.model_reference }),
            el('span', { class: 'muted', text: ` · ${entry.type_code}` }),
          ]),
          el('div', { class: 'hint', text: `updated ${formatDate(entry.updated_at)}` }),
        ]),
        el('div', { class: 'btn-row' }, [
          button('Approve', {
            class: 'btn btn-sm btn-primary',
            on: {
              click: async () => {
                try {
                  await api.library.setState(entry.id, 'approved');
                  toast('Approved', 'ok');
                  libraryView();
                } catch (error) { fail(error); }
              },
            },
          }),
          button('Reject', {
            class: 'btn btn-sm btn-danger',
            on: {
              click: async () => {
                try {
                  await api.library.setState(entry.id, 'rejected');
                  toast('Rejected — it will no longer appear in lookups', 'ok');
                  libraryView();
                } catch (error) { fail(error); }
              },
            },
          }),
        ]),
      ]),
      el('div', { class: 'card-body' }, [
        el('div', {}, entry.flags.map((f) =>
          el('div', { style: 'display:flex;gap:8px;align-items:baseline;margin-bottom:6px' }, [
            pill(f.kind.toLowerCase(), TONE[f.kind] || 'quiet'),
            el('span', { class: 'tiny', text: f.message }),
            el('button', {
              class: 'btn btn-sm',
              style: 'margin-left:auto',
              on: {
                click: async () => {
                  try {
                    await api.library.resolveFlag(f.id);
                    libraryView();
                  } catch (error) { fail(error); }
                },
              },
            }, ['Dismiss']),
          ])
        )),
        el('dl', { class: 'kv', style: 'margin-top:10px' },
          Object.entries(entry.values).flatMap(([k, v]) => [
            el('dt', { text: k }),
            el('dd', { text: show(v) }),
          ])),
      ]),
    ]));
  }
}
