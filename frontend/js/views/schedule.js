// The schedule editor.
//
// The colour contract is the same one the printed schedule uses, so the screen
// and the paper mean the same thing: blue on yellow you type, green pulled from
// the equipment library, black calculated.
//
// Derived and library cells are never editable and never sent to the server.
// They are recomputed on every save from what was typed, which is why correcting
// a product in the library fixes every schedule that uses it at once.

import { api } from '../api.js';
import { go, store } from '../app.js';
import {
  button, card, clear, confirmDialog, debounce, download, el, empty, fail, field,
  formatDate, input, modal, mount, notice, pill, select, show, table, toast,
} from '../ui.js';

let view = null;

export async function scheduleView(scheduleId) {
  const [grid, revisions, meta] = await Promise.all([
    api.schedules.grid(scheduleId),
    api.schedules.revisions(scheduleId),
    api.catalogue.meta(),
  ]);
  view = { grid, revisions, meta, tab: view && view.id === scheduleId ? view.tab : 'schedule', id: scheduleId };
  draw();
}

async function reloadGrid(fresh) {
  view.grid = fresh || (await api.schedules.grid(view.id));
  draw();
}

async function reloadRevisions() {
  view.revisions = await api.schedules.revisions(view.id);
  draw();
}

function draw() {
  const { schedule, project_id, project_name, building_ref, building_count } = view.grid;

  const page = el('div', { class: 'page page-wide' }, [
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/projects', text: 'Projects' }), ' / ',
      el('a', { href: `#/projects/${project_id}`, text: project_name }), ' / ',
      building_count > 1 ? `${building_ref} / ` : '',
      schedule.code,
    ]),
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: schedule.title }),
        el('div', { class: 'sub' }, [el('span', { class: 'dn', text: schedule.docnum })]),
      ]),
      el('div', { class: 'btn-row' }, [
        schedule.locked ? pill('issued', 'amber') : null,
        button('Export .xlsx', {
          on: { click: () => download(`/api/schedules/${view.id}/export.xlsx`) },
        }),
        store.pdfAvailable
          ? button('Export PDF', {
              class: 'btn btn-primary',
              on: { click: () => download(`/api/schedules/${view.id}/export.pdf`) },
            })
          : null,
      ]),
    ]),
    el('div', { class: 'tabs' }, [
      ['schedule', `Schedule (${view.grid.rows.length})`],
      ['revisions', `Revisions (${view.revisions.length})`],
      ['notes', 'Notes'],
    ].map(([key, label]) =>
      el('button', {
        class: `tab${view.tab === key ? ' active' : ''}`,
        on: { click: () => { view.tab = key; draw(); } },
      }, [label])
    )),
  ]);

  const body = el('div');
  page.appendChild(body);
  mount(page);

  if (view.tab === 'schedule') drawGrid(body);
  else if (view.tab === 'revisions') drawRevisions(body);
  else drawNotes(body);
}

/* ----------------------------------------------------------------- grid --- */

function drawGrid(root) {
  const { columns, rows } = view.grid;

  root.appendChild(el('div', {
    class: 'btn-row',
    style: 'margin-bottom:12px;align-items:center',
  }, [
    button('Add row', { class: 'btn btn-primary', on: { click: addRow } }),
    button('Paste from Excel…', { on: { click: pasteRows } }),
    el('div', { class: 'legend', style: 'margin-left:auto' }, [
      el('span', {}, [el('span', { class: 'swatch swatch-input' }), 'you type']),
      el('span', {}, [el('span', { class: 'swatch swatch-library' }), 'from the equipment library']),
      el('span', {}, [el('span', { class: 'swatch swatch-derived' }), 'calculated']),
    ]),
  ]));

  if (!rows.length) {
    root.appendChild(el('section', { class: 'card' }, [
      empty(
        'No equipment scheduled yet',
        'Add a row and start typing. Pick a Model Reference and the product columns fill ' +
        'themselves in; the calculated columns follow as you go.',
        button('Add the first row', { class: 'btn btn-primary', on: { click: addRow } })
      ),
    ]));
    return;
  }

  const head = el('thead', {}, [
    el('tr', {}, [
      el('th', { class: 'rowno', text: '#' }),
      ...columns.map((c) => el('th', {
        class: `g-${c.kind}`,
        title: c.note || c.formula || '',
      }, [c.name])),
      el('th', {}),
    ]),
    el('tr', { class: 'units' }, [
      el('th', { class: 'rowno' }),
      ...columns.map((c) => el('th', { class: `g-${c.kind}`, text: c.unit_display || '' })),
      el('th', {}),
    ]),
  ]);

  const body = el('tbody', {}, rows.map((row, index) => renderRow(row, index, columns)));

  root.appendChild(el('div', { class: 'sheet-wrap' }, [
    el('table', { class: 'sheet' }, [head, body]),
  ]));

  const problems = rows.reduce((n, r) => n + Object.keys(r.problems || {}).length, 0);
  if (problems) {
    root.appendChild(el('div', { style: 'margin-top:12px' }, [
      notice(
        `${problems} cell(s) could not be calculated or looked up. Hover a red cell for the reason.`,
        'warn'
      ),
    ]));
  }
}

function renderRow(row, index, columns) {
  const tr = el('tr', {}, [el('td', { class: 'rowno', text: String(index + 1) })]);

  for (const column of columns) {
    const key = column.legacy_name;
    const problem = (row.problems || {})[key];

    if (column.editable) {
      const cell = el('td', { class: 'cell-input' });
      if (key === 'Model Reference') {
        cell.appendChild(modelReferenceCell(row, column));
      } else {
        const box = input(row.values[key] ?? '', {
          on: {
            change: (e) => saveCell(row, key, e.target.value),
            keydown: (e) => gridKeys(e),
          },
        });
        cell.appendChild(box);
      }
      tr.appendChild(cell);
    } else {
      const value = row.computed[key];
      tr.appendChild(el('td', {
        class: `cell-${column.kind}${problem ? ' cell-problem' : ''}`,
        title: problem || '',
        text: problem ? '—' : show(value),
      }));
    }
  }

  tr.appendChild(el('td', { class: 'cell-actions' }, [
    el('button', {
      class: 'icon-btn',
      title: 'Delete this row',
      on: {
        click: async () => {
          try {
            await reloadGrid(await api.schedules.deleteRow(view.id, row.id));
          } catch (error) { fail(error); }
        },
      },
    }, ['×']),
  ]));

  return tr;
}

/** Arrow-key movement between cells, so the grid behaves like a spreadsheet. */
function gridKeys(event) {
  const keys = { ArrowUp: [-1, 0], ArrowDown: [1, 0], Enter: [1, 0] };
  const move = keys[event.key];
  if (!move) return;
  event.preventDefault();

  const cell = event.target.closest('td');
  const row = cell.closest('tr');
  const columnIndex = [...row.children].indexOf(cell);
  const target = move[0] > 0 ? row.nextElementSibling : row.previousElementSibling;
  if (!target) return;
  const next = target.children[columnIndex];
  const box = next && next.querySelector('input');
  if (box) { box.focus(); box.select(); }
}

const saveCell = debounce(async (row, key, value) => {
  const values = { ...row.values, [key]: value };
  try {
    await reloadGrid(await api.schedules.updateRow(view.id, row.id, values));
  } catch (error) { fail(error); }
}, 350);

async function addRow() {
  try {
    await reloadGrid(await api.schedules.addRow(view.id, {}));
  } catch (error) { fail(error); }
}

/* ------------------------------------------------- model reference cell --- */

// The one cell that reaches into the shared equipment library. Typing filters
// what the organisation already has; if it is not there, the product is captured
// once and is immediately available to every other schedule.
function modelReferenceCell(row, column) {
  const wrap = el('div', { class: 'ac-wrap' });
  const box = input(row.values['Model Reference'] ?? '', {
    placeholder: 'pick or type…',
    on: {
      keydown: (event) => {
        if (event.key === 'Escape') { closeList(); return; }
        gridKeys(event);
      },
      input: () => search(box.value),
      focus: () => search(box.value),
      blur: () => setTimeout(closeList, 160),
      change: (e) => saveCell(row, 'Model Reference', e.target.value),
    },
  });

  let list = null;
  const closeList = () => { if (list) { list.remove(); list = null; } };

  const search = async (query) => {
    let entries = [];
    try {
      entries = await api.library.list(view.grid.schedule.code, query);
    } catch { /* an empty library is not an error worth shouting about */ }

    closeList();
    list = el('div', { class: 'ac-list' });

    for (const entry of entries.slice(0, 40)) {
      const summary = Object.entries(entry.values)
        .filter(([, v]) => v !== null && v !== '')
        .slice(0, 3)
        .map(([k, v]) => `${k.replace(/\s*\([^)]*\)$/, '')}: ${v}`)
        .join(' · ');
      list.appendChild(el('div', {
        class: 'ac-item',
        on: {
          mousedown: (event) => {
            event.preventDefault();
            box.value = entry.model_reference;
            closeList();
            saveCell.flush(row, 'Model Reference', entry.model_reference);
          },
        },
      }, [
        el('div', { class: 'ref', text: entry.model_reference }),
        summary ? el('div', { class: 'meta', text: summary }) : null,
      ]));
    }

    const typed = box.value.trim();
    if (typed && !entries.some((e) => e.model_reference === typed)) {
      list.appendChild(el('div', {
        class: 'ac-item ac-new',
        on: {
          mousedown: (event) => {
            event.preventDefault();
            closeList();
            captureProduct(row, typed);
          },
        },
      }, [`Add “${typed}” to the equipment library…`]));
    }

    if (list.childElementCount) wrap.appendChild(list);
  };

  wrap.appendChild(box);
  return wrap;
}

async function captureProduct(row, reference) {
  const columns = view.grid.columns.filter((c) => c.kind === 'library');
  const inputs = {};
  for (const column of columns) {
    inputs[column.legacy_name] = input('', {
      placeholder: column.example !== '' && column.example !== null ? String(column.example) : '',
    });
  }

  const findingsBox = el('div');
  const checkDrift = debounce(async () => {
    clear(findingsBox);
    try {
      const { findings } = await api.library.inspect({
        type_code: view.grid.schedule.code,
        model_reference: reference,
        values: Object.fromEntries(Object.entries(inputs).map(([k, n]) => [k, n.value])),
      });
      const notable = findings.filter((f) => f.kind !== 'NEW' && f.kind !== 'INCOMPLETE');
      for (const finding of notable) {
        findingsBox.appendChild(notice(finding.message, 'warn'));
      }
    } catch { /* advisory only */ }
  }, 500);
  for (const node of Object.values(inputs)) node.addEventListener('input', checkDrift);

  const ok = await modal({
    title: `Add ${reference} to the equipment library`,
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'Saved to the shared library and usable straight away, on this schedule and every ' +
        'other. It is flagged for review rather than held back.',
      ]),
      findingsBox,
      el('div', { class: 'grid-3' }, columns.map((c) =>
        field(c.unit_display ? `${c.name} (${c.unit_display})` : c.name, inputs[c.legacy_name])
      )),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Save to library', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok) return;

  try {
    await api.library.save({
      type_code: view.grid.schedule.code,
      model_reference: reference,
      values: Object.fromEntries(
        Object.entries(inputs)
          .map(([k, n]) => [k, n.value])
          .filter(([, v]) => v !== '')
      ),
    });
    await reloadGrid(
      await api.schedules.updateRow(view.id, row.id, {
        ...row.values, 'Model Reference': reference,
      })
    );
    toast(`${reference} saved to the library`, 'ok');
  } catch (error) { fail(error); }
}

/* ------------------------------------------------------------ paste rows --- */

async function pasteRows() {
  const area = el('textarea', {
    rows: 10,
    placeholder: 'Paste cells copied from Excel here. The first row may be a header.',
  });
  const inputColumns = view.grid.columns.filter((c) => c.editable);

  const ok = await modal({
    title: 'Paste rows from Excel',
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'Columns are matched left to right against the ones you type: ',
        inputColumns.map((c) => c.name).join(', '),
        '. This replaces every row on the schedule.',
      ]),
      area,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Replace rows', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok || !area.value.trim()) return;

  const lines = area.value.replace(/\r/g, '').split('\n').filter((l) => l.trim());
  const first = lines[0].split('\t').map((c) => c.trim().toLowerCase());
  const looksLikeHeader = inputColumns.some((c) => first.includes(c.name.toLowerCase()));
  const dataLines = looksLikeHeader ? lines.slice(1) : lines;

  const rows = dataLines.map((line) => {
    const cells = line.split('\t');
    const values = {};
    inputColumns.forEach((column, i) => {
      const raw = (cells[i] ?? '').trim();
      if (raw !== '') {
        const asNumber = Number(raw);
        values[column.legacy_name] = raw !== '' && !Number.isNaN(asNumber) ? asNumber : raw;
      }
    });
    return { values };
  });

  try {
    await reloadGrid(await api.schedules.replaceRows(view.id, rows));
    toast(`${rows.length} row(s) pasted`, 'ok');
  } catch (error) { fail(error); }
}

/* ------------------------------------------------------------ revisions --- */

function drawRevisions(root) {
  const statuses = (view.meta.status_codes || []).map(([code, desc]) => `${code} - ${desc}`);

  const rows = view.revisions.map((r) => el('tr', {}, [
    el('td', {}, [
      el('strong', { text: r.code }),
      r.is_current ? pill('current', 'green') : null,
    ]),
    el('td', { class: 'tiny', text: r.status || '—' }),
    el('td', { class: 'tiny nowrap', text: formatDate(r.issue_date) }),
    el('td', { class: 'tiny', text: [r.prepared_by, r.checked_by, r.approved_by].filter(Boolean).join(' / ') || '—' }),
    el('td', { class: 'tiny', text: r.description || '' }),
    el('td', { class: 'num mono tiny muted', text: String(r.sort_key) }),
    el('td', {}, [
      el('button', {
        class: 'icon-btn', title: 'Delete',
        on: {
          click: async () => {
            try {
              view.revisions = await api.schedules.deleteRevision(view.id, r.id);
              draw();
            } catch (error) { fail(error); }
          },
        },
      }, ['×']),
    ]),
  ]));

  root.appendChild(card(
    'Revision log',
    view.revisions.length
      ? table(['Rev', 'Status', 'Date', 'By', 'Description', { text: 'Rank', class: 'num' }, ''], rows)
      : empty('No revisions yet', 'Add one when the schedule is ready to issue.'),
    [
      button('Add revision', { class: 'btn btn-primary', on: { click: () => addRevision(statuses, false) } }),
      button('Add published (C)', { on: { click: () => addRevision(statuses, true) } }),
    ],
    'Ranked by series then number, so a published C-revision is current even when a ' +
    'preliminary row was entered after it.'
  ));

  const current = view.revisions.find((r) => r.is_current);
  if (current) {
    root.appendChild(notice(
      `The cover and register show ${current.code}${current.status ? ` (${current.status})` : ''}.`,
      'info'
    ));
  }
}

async function addRevision(statuses, published) {
  let suggested = '';
  try {
    suggested = (await api.schedules.nextRevision(view.id, published)).code;
  } catch { /* the server will pick one if we cannot */ }

  const code = input(suggested);
  const status = select(statuses, statuses[2] || statuses[0]);
  const date = el('input', { type: 'date', value: new Date().toISOString().slice(0, 10) });
  const description = input('');

  const ok = await modal({
    title: 'Add a revision',
    render: () => el('div', {}, [
      el('div', { class: 'grid-2' }, [
        field('Revision', code, 'P for preliminary, C for published.'),
        field('Date', date),
      ]),
      el('div', { style: 'margin-top:12px' }, [field('Suitability status', status)]),
      el('div', { style: 'margin-top:12px' }, [field('Description', description)]),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Add revision', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok) return;

  try {
    view.revisions = await api.schedules.addRevision(view.id, {
      code: code.value.trim(),
      status: status.value,
      issue_date: date.value || null,
      description: description.value,
    });
    view.grid = await api.schedules.grid(view.id);
    draw();
  } catch (error) { fail(error); }
}

/* ---------------------------------------------------------------- notes --- */

function drawNotes(root) {
  root.appendChild(card(
    'General notes',
    el('div', {}, [
      el('p', { class: 'muted' }, [
        'These print in the notes block at the top of the schedule: the practice-wide ' +
        'wording first, then anything specific to this equipment type.',
      ]),
      el('ol', { style: 'padding-left:20px;font-size:12.5px;line-height:1.7' },
        view.grid.notes.map((n) => el('li', { text: n }))),
    ]),
    [button('Edit type notes', {
      on: { click: () => go(`/catalogue`) },
    })]
  ));
}
