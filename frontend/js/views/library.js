// The shared equipment library, and the review queue.
//
// Entries go live the moment someone enters them on a schedule, so nobody is
// blocked mid-job. The queue ranks what needs a look rather than gating use:
// v1's submissions inbox existed to stop concurrent writes corrupting a shared
// .xlsx, which is not a problem a database has.

import { api } from '../api.js';
import { store } from '../app.js';
import {
  button, card, clear, confirmDialog, el, empty, fail, field, formatDate, input,
  modal, mount, notice, pill, select, show, table, toast,
} from '../ui.js';

const TONE = { CONFLICT: 'red', DRIFT: 'amber', INCOMPLETE: 'quiet', NEW: 'blue' };

let state = { tab: 'browse', code: null, query: '' };

export async function libraryView() {
  const types = await api.catalogue.list();
  store.catalogue = types;
  if (!state.code && types.length) state.code = types[0].code;

  const page = el('div', { class: 'page page-wide' }, [
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: 'Equipment library' }),
        el('div', {
          class: 'sub',
          text: 'Every product this practice has scheduled. Entered once, then available on every schedule.',
        }),
      ]),
    ]),
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

async function drawBrowse(root, types) {
  if (!types.length) {
    root.appendChild(card('', empty('No schedule types yet', 'Create one first.')));
    return;
  }

  const typeSelect = select(
    types.map((t) => [t.code, `${t.code} — ${t.title}`]),
    state.code,
    { on: { change: (e) => { state.code = e.target.value; libraryView(); } } }
  );
  const search = input(state.query, { placeholder: 'Filter by reference or value…' });

  const entries = await api.library.list(state.code, state.query);
  const type = await api.catalogue.read(types.find((t) => t.code === state.code).id);
  const libColumns = type.columns.filter((c) => c.kind === 'library');

  search.addEventListener('input', () => {
    state.query = search.value;
    clearTimeout(search._timer);
    search._timer = setTimeout(() => libraryView(), 300);
  });

  const rows = entries.map((entry) =>
    el('tr', {}, [
      el('td', {}, [
        el('strong', { class: 'mono', text: entry.model_reference }),
        entry.flags.length
          ? el('div', { style: 'margin-top:3px' },
              entry.flags.map((f) => pill(f.kind.toLowerCase(), TONE[f.kind] || 'quiet')))
          : null,
      ]),
      ...libColumns.slice(0, 6).map((c) =>
        el('td', { class: 'tiny', text: show(entry.values[`${c.name}${c.unit ? ` (${c.unit})` : ''}`] ?? entry.values[c.name]) })
      ),
      el('td', { class: 'tiny muted nowrap', text: formatDate(entry.updated_at) }),
      el('td', { class: 'cell-actions' }, [
        el('div', { class: 'btn-row' }, [
          button('Edit', {
            class: 'btn btn-sm',
            on: { click: () => editEntry(entry, libColumns) },
          }),
          button('Remove', {
            class: 'btn btn-sm btn-danger',
            on: {
              click: async () => {
                const ok = await confirmDialog({
                  title: `Remove ${entry.model_reference}?`,
                  message:
                    'It stops appearing in lookups. It is not deleted, so a schedule that ' +
                    'already references it keeps its record of what was chosen.',
                  confirmLabel: 'Remove from library',
                  danger: true,
                });
                if (!ok) return;
                try {
                  await api.library.remove(entry.id);
                  toast('Removed', 'ok');
                  libraryView();
                } catch (error) { fail(error); }
              },
            },
          }),
        ]),
      ]),
    ])
  );

  root.appendChild(el('section', { class: 'card' }, [
    el('header', { class: 'card-head' }, [
      el('div', { style: 'display:flex;gap:10px;flex-wrap:wrap;flex:1' }, [
        el('div', { style: 'min-width:240px' }, [typeSelect]),
        el('div', { style: 'flex:1;min-width:200px' }, [search]),
      ]),
      button('Add product', {
        class: 'btn btn-primary',
        on: { click: () => addEntry(libColumns) },
      }),
    ]),
    el('div', { class: 'card-body tight' }, [
      entries.length
        ? table(
            [
              'Model reference',
              ...libColumns.slice(0, 6).map((c) => (c.unit ? `${c.name} (${c.unit})` : c.name)),
              'Updated', '',
            ],
            rows
          )
        : empty(
            `No ${state.code} products yet`,
            'They are captured the first time someone types a new Model Reference on a schedule.'
          ),
    ]),
  ]));
}

async function addEntry(libColumns) {
  const reference = input('', { placeholder: 'SYS-VSR-500' });
  const inputs = {};
  for (const c of libColumns) {
    inputs[c.unit ? `${c.name} (${c.unit})` : c.name] = input('');
  }

  const ok = await modal({
    title: `Add a ${state.code} product`,
    wide: true,
    render: () => el('div', {}, [
      field('Model reference', reference, 'The lookup key. It is what a schedule row points at.'),
      el('div', { class: 'grid-3', style: 'margin-top:14px' },
        libColumns.map((c) => field(
          c.unit ? `${c.name} (${c.unit})` : c.name,
          inputs[c.unit ? `${c.name} (${c.unit})` : c.name]
        ))),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Save', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok || !reference.value.trim()) return;

  try {
    await api.library.save({
      type_code: state.code,
      model_reference: reference.value.trim(),
      values: Object.fromEntries(
        Object.entries(inputs).map(([k, n]) => [k, n.value]).filter(([, v]) => v !== '')
      ),
    });
    toast('Saved to the library', 'ok');
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
