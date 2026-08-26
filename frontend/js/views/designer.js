// The schedule type designer.
//
// Validation is the point of this screen. The renderer enforces none of it, so
// a bad formula used to surface as #REF! in an issued workbook. Every rule lives
// in the backend's parser and is reported here as you type.

import { api } from '../api.js';
import { go } from '../app.js';
import {
  button, card, clear, debounce, el, empty, fail, field, input, modal, mount,
  notice, pill, select, show, table, toast,
} from '../ui.js';

let d = null;

export async function designerView(typeId) {
  const meta = await api.catalogue.meta();
  const existing = typeId ? await api.catalogue.read(typeId) : null;

  d = {
    id: typeId || null,
    meta,
    code: existing ? existing.code : '',
    title: existing ? existing.title : '',
    short: existing ? existing.short : '',
    volume: existing ? existing.volume : '',
    version: existing ? existing.version : 1,
    columns: existing ? existing.columns.map((c) => ({ ...c })) : [
      { kind: 'input', name: 'Unit Reference', unit: '', width: 14, example: 'XX-01' },
      { kind: 'library', name: 'Manufacturer', unit: '', width: 18, example: '' },
    ],
    notes: existing ? [...existing.notes] : [],
    projectNotes: existing ? existing.project_notes : (meta.project_notes || []),
    history: existing ? existing.history : [],
    issues: [],
    preview: null,
  };

  if (!existing) {
    try {
      const settings = await api.settings.read();
      d.projectNotes = settings.house_standard.general_notes || [];
    } catch { /* the greyed preview is a nicety, not a requirement */ }
  }

  draw();
  validate();
}

const validate = debounce(async () => {
  try {
    const result = await api.catalogue.validate(payload());
    d.issues = result.issues;
    d.preview = result.preview;
    drawFeedback();
  } catch (error) { fail(error); }
}, 350);

function payload() {
  return {
    code: d.code, title: d.title, short: d.short, volume: d.volume,
    columns: d.columns, notes: d.notes,
  };
}

function draw() {
  const page = el('div', { class: 'page page-wide' }, [
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/catalogue', text: 'Schedule types' }), ' / ', d.code || 'New type',
    ]),
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: d.id ? `${d.code} — ${d.title}` : 'New schedule type' }),
        el('div', { class: 'sub' }, [
          d.id ? `Version ${d.version}. ` : '',
          'Columns are one of three kinds, and the kind decides where the value comes from.',
        ]),
      ]),
      el('div', { class: 'btn-row' }, [
        d.id ? button('Where is this used?', { on: { click: showUsage } }) : null,
        button(d.id ? 'Save changes' : 'Create type', { class: 'btn btn-primary', on: { click: save } }),
      ]),
    ]),
  ]);

  const feedback = el('div', { id: 'designer-feedback' });
  page.appendChild(feedback);

  page.appendChild(card('Identity', el('div', { class: 'grid-4' }, [
    field('Code', input(d.code, {
      placeholder: 'GASB',
      on: { input: (e) => { d.code = e.target.value.toUpperCase(); e.target.value = d.code; validate(); } },
    }), 'Uppercase, no spaces. Unique across the catalogue.'),
    field('Title', input(d.title, {
      placeholder: 'Gas Boiler Schedule',
      on: { input: (e) => { d.title = e.target.value; validate(); } },
    })),
    field('Short name', input(d.short, {
      placeholder: 'Gas Boilers',
      on: { input: (e) => { d.short = e.target.value; } },
    })),
    field(
      'Volume',
      select(
        [['', 'Not set'], ...Object.entries(d.meta.volume_lookup).map(([k, v]) => [k, `${k} — ${v}`])],
        d.volume,
        { on: { change: (e) => { d.volume = e.target.value; validate(); } } }
      ),
      'Follows the equipment, not the job.'
    ),
  ])));

  page.appendChild(columnsCard());
  page.appendChild(previewCard());
  page.appendChild(notesCard());

  if (d.history.length) {
    page.appendChild(card('History', table(
      ['Version', 'Date', 'Change'],
      d.history.map((h) => el('tr', {}, [
        el('td', { text: `v${h.version}` }),
        el('td', { class: 'tiny muted', text: h.date || '' }),
        el('td', { text: h.change || '' }),
      ]))
    )));
  }

  mount(page);
  drawFeedback();
}

function drawFeedback() {
  const box = document.getElementById('designer-feedback');
  if (!box) return;
  clear(box);

  const errors = d.issues.filter((i) => i.severity === 'error');
  const warnings = d.issues.filter((i) => i.severity === 'warning');

  if (errors.length) {
    box.appendChild(notice(
      `${errors.length} problem(s) must be fixed before this type can be saved:`,
      'error',
      errors.map((i) => (i.column ? `${i.column}: ${i.message}` : i.message))
    ));
  } else if (d.code && d.title) {
    box.appendChild(notice('This type is valid.', 'ok'));
  }

  if (warnings.length) {
    box.appendChild(notice('Worth a look:', 'warn',
      warnings.map((i) => (i.column ? `${i.column}: ${i.message}` : i.message))));
  }

  const preview = document.getElementById('designer-preview');
  if (preview && d.preview) {
    clear(preview).appendChild(renderPreview(d.preview));
  }
}

/* -------------------------------------------------------------- columns --- */

function columnsCard() {
  const body = el('div', { class: 'table-wrap' });
  const render = () => {
    clear(body).appendChild(el('table', {}, [
      el('thead', {}, [
        el('tr', {}, ['', 'Kind', 'Name', 'Unit', 'Width', 'Example / Formula', ''].map((h) =>
          el('th', { text: h })
        )),
      ]),
      el('tbody', {}, d.columns.map((column, index) => columnRow(column, index, render))),
    ]));
  };
  render();

  const add = (kind) => {
    d.columns.push({ kind, name: '', unit: '', width: 14, example: '', formula: kind === 'derived' ? '=' : null, note: '' });
    render();
    validate();
  };

  return card(
    'Columns',
    body,
    [
      button('+ Input', { class: 'btn btn-sm', on: { click: () => add('input') } }),
      button('+ From library', { class: 'btn btn-sm', on: { click: () => add('library') } }),
      button('+ Derived', { class: 'btn btn-sm', on: { click: () => add('derived') } }),
    ],
    'Model Reference is added automatically between the input and library columns.'
  );
}

function columnRow(column, index, render) {
  const move = (delta) => {
    const target = index + delta;
    if (target < 0 || target >= d.columns.length) return;
    [d.columns[index], d.columns[target]] = [d.columns[target], d.columns[index]];
    render();
    validate();
  };

  const kindSelect = select(
    d.meta.kinds.map((k) => [k.kind, k.label]),
    column.kind,
    {
      on: {
        change: (e) => {
          column.kind = e.target.value;
          if (column.kind === 'derived' && !column.formula) column.formula = '=';
          render();
          validate();
        },
      },
    }
  );
  const hint = (d.meta.kinds.find((k) => k.kind === column.kind) || {}).hint || '';

  return el('tr', {}, [
    el('td', { class: 'nowrap' }, [
      el('button', { class: 'icon-btn', title: 'Move up', on: { click: () => move(-1) } }, ['↑']),
      el('button', { class: 'icon-btn', title: 'Move down', on: { click: () => move(1) } }, ['↓']),
    ]),
    el('td', { style: 'min-width:130px' }, [
      kindSelect,
      el('div', { class: 'muted tiny', text: hint }),
    ]),
    el('td', { style: 'min-width:180px' }, [
      input(column.name, {
        on: { input: (e) => { column.name = e.target.value; validate(); } },
      }),
    ]),
    el('td', { style: 'width:90px' }, [
      // No placeholder: a greyed 'l/s' on every unit-less column reads as a
      // real unit at a glance, which is worse than an empty box.
      input(column.unit, {
        title: 'Rendered on the unit row beneath the header. Leave blank if the column has no unit.',
        on: { input: (e) => { column.unit = e.target.value; validate(); } },
      }),
    ]),
    el('td', { style: 'width:70px' }, [
      input(String(column.width), {
        type: 'number',
        on: { input: (e) => { column.width = parseInt(e.target.value, 10) || 14; } },
      }),
    ]),
    el('td', { style: 'min-width:280px' }, [
      column.kind === 'derived'
        ? el('div', {}, [
            input(column.formula || '', {
              class: 'mono',
              placeholder: '={Airflow (l/s)}*2',
              on: { input: (e) => { column.formula = e.target.value; validate(); } },
            }),
            input(column.note || '', {
              placeholder: 'Note shown as the cell comment',
              style: 'margin-top:4px',
              on: { input: (e) => { column.note = e.target.value; validate(); } },
            }),
          ])
        : input(show(column.example), {
            placeholder: 'Example value',
            on: { input: (e) => { column.example = e.target.value; validate(); } },
          }),
    ]),
    el('td', {}, [
      el('button', {
        class: 'icon-btn', title: 'Delete column',
        on: { click: () => { d.columns.splice(index, 1); render(); validate(); } },
      }, ['×']),
    ]),
  ]);
}

/* -------------------------------------------------------------- preview --- */

function previewCard() {
  const box = el('div', { id: 'designer-preview' });
  return card(
    'Preview',
    box,
    [],
    'The header row, unit row and one example row, exactly as they will be rendered.'
  );
}

function renderPreview(preview) {
  return el('div', {}, [
    el('div', { class: 'sheet-wrap', style: 'max-height:none' }, [
      el('table', { class: 'sheet' }, [
        el('thead', {}, [
          el('tr', {}, preview.headers.map((h, i) =>
            el('th', { class: `g-${preview.kinds[i]}`, text: h })
          )),
          el('tr', { class: 'units' }, preview.units.map((u, i) =>
            el('th', { class: `g-${preview.kinds[i]}`, text: u || '' })
          )),
        ]),
        el('tbody', {}, [
          el('tr', {}, preview.examples.map((example, i) =>
            el('td', { class: `cell-${preview.kinds[i]}`, style: 'padding:4px 7px' }, [
              show(example),
            ])
          )),
        ]),
      ]),
    ]),
    el('div', { class: 'legend', style: 'margin-top:10px' }, [
      el('span', {}, [el('span', { class: 'swatch swatch-input' }), 'typed per unit']),
      el('span', {}, [el('span', { class: 'swatch swatch-library' }), 'from the equipment library']),
      el('span', {}, [el('span', { class: 'swatch swatch-derived' }), 'calculated']),
    ]),
    el('div', { class: 'muted tiny', style: 'margin-top:8px' }, [
      `Formulas may use ${d.meta.constants.map((c) => c.alias).join(', ')}. `,
      `Spilling and post-2019 functions are rejected (${d.meta.banned_functions.slice(0, 6).join(', ')}…) `,
      'because the exported workbook writes static formulas only.',
    ]),
  ]);
}

/* ---------------------------------------------------------------- notes --- */

function notesCard() {
  const list = el('div');
  const render = () => {
    clear(list);
    d.projectNotes.forEach((note, i) => {
      list.appendChild(el('div', { class: 'muted tiny', style: 'padding:3px 0' }, [
        `[${i + 1}] ${note}`,
      ]));
    });
    d.notes.forEach((note, i) => {
      list.appendChild(el('div', { style: 'display:flex;gap:6px;margin-top:6px' }, [
        el('span', { class: 'muted tiny', style: 'padding-top:7px', text: `[${d.projectNotes.length + i + 1}]` }),
        el('textarea', {
          rows: 2, value: note,
          on: { input: (e) => { d.notes[i] = e.target.value; } },
        }),
        el('button', {
          class: 'icon-btn',
          on: { click: () => { d.notes.splice(i, 1); render(); } },
        }, ['×']),
      ]));
    });
  };
  render();

  return card(
    'Notes',
    el('div', {}, [
      el('p', { class: 'muted tiny' }, [
        'The greyed notes come from the house standard and appear on every schedule. Add ' +
        'wording below that is specific to this equipment — do not repeat the generic text.',
      ]),
      list,
    ]),
    [button('+ Add note', { class: 'btn btn-sm', on: { click: () => { d.notes.push(''); render(); } } })]
  );
}

/* ----------------------------------------------------------------- save --- */

async function showUsage() {
  try {
    const usage = await api.catalogue.usage(d.id);
    await modal({
      title: `Where ${usage.code} is used`,
      wide: true,
      render: () => (usage.used_by.length
        ? table(
            ['Project', 'Building', 'Built against', 'Current'],
            usage.used_by.map((u) => el('tr', {}, [
              el('td', { text: u.project }),
              el('td', { text: u.building }),
              el('td', {}, [pill(`v${u.pinned_version}`, u.stale ? 'amber' : 'quiet')]),
              el('td', {}, [pill(`v${u.current_version}`, 'quiet')]),
            ]))
          )
        : empty('Not used yet', 'No schedule has been built from this type.')),
    });
  } catch (error) { fail(error); }
}

async function save() {
  const errors = d.issues.filter((i) => i.severity === 'error');
  if (errors.length) {
    fail(new Error('Fix the problems listed above first.'));
    return;
  }

  let change = '';
  if (d.id) {
    const note = input('', { placeholder: 'added Filter Grade' });
    const ok = await modal({
      title: 'Describe the change',
      render: () => el('div', {}, [
        el('p', { class: 'muted' }, [
          'If the columns moved, the version is bumped and this note is recorded. Projects ' +
          'stay pinned to the version they were built against.',
        ]),
        field('What changed', note),
      ]),
      actions: (close) => [
        button('Cancel', { on: { click: () => close(false) } }),
        button('Save', { class: 'btn btn-primary', on: { click: () => close(true) } }),
      ],
    });
    if (!ok) return;
    change = note.value;
  }

  try {
    const saved = d.id
      ? await api.catalogue.update(d.id, { ...payload(), change })
      : await api.catalogue.create(payload());
    toast(d.id ? `${saved.code} saved as v${saved.version}` : `${saved.code} created`, 'ok');
    go('/catalogue');
  } catch (error) { fail(error); }
}
