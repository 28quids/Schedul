// The schedule type designer.
//
// The table *is* the editor. Column names are typed where they will be read,
// widths are dragged where they will be judged, and a column is moved by
// dragging it to where it should go — because the thing being designed is a
// sheet, and editing a sheet through a list beside a picture of a sheet is a
// translation nobody should have to do in their head.
//
// Everything that is not part of that picture — the formula, the unit, the
// example, where the column appears — lives in a drawer on the selected column.
// It is the same clean column model underneath; only the way in has changed.
//
// Validation is still the point of this screen. The renderer enforces none of
// it, so a bad formula used to surface as #REF! in an issued workbook. Every
// rule lives in the backend's parser and is reported here as you type.

import { api } from '../api.js';
import { go } from '../app.js';
import {
  button, card, clear, confirmDialog, debounce, el, empty, fail, field, input, modal,
  mount, notice, pageHead, pill, select, show, table, textarea, toast,
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
    //: Which column the drawer is showing, by identity rather than index, so
    //: reordering does not quietly move the drawer onto a different column.
    selected: null,
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
    drawFeedback();
  } catch (error) { fail(error); }
}, 350);

function payload() {
  return {
    code: d.code, title: d.title, short: d.short, volume: d.volume,
    columns: d.columns, notes: d.notes,
  };
}

/**
 * The columns in the order they sit on the sheet.
 *
 * Inputs, then the automatic Model Reference, then library columns, then
 * derived. This mirrors `ScheduleType.layout()` in the backend, which stays the
 * authority — the copy here is so the table redraws as it is dragged rather than
 * waiting on a round trip. `null` marks Model Reference, which is inserted
 * automatically and is not one of the authored columns.
 */
function layout() {
  const of = (kind) => d.columns.filter((c) => c.kind === kind);
  return [...of('input'), null, ...of('library'), ...of('derived')];
}

function draw() {
  const page = el('div', { class: 'page page-wide' }, [
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/catalogue', text: 'Schedule types' }), ' / ', d.code || 'New type',
    ]),
    pageHead(
      d.id ? `${d.code} — ${d.title}` : 'New schedule type',
      (d.id ? `Version ${d.version}. ` : '') +
        'Type in the table below. Drag a column to move it, drag its edge to size it.',
      [
        d.id ? button('Where is this used?', { on: { click: showUsage } }) : null,
        button(d.id ? 'Save changes' : 'Create type', {
          class: 'btn btn-primary', on: { click: save },
        }),
      ]
    ),
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

  page.appendChild(sheetCard());
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
  drawDrawer();
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
}

/* ------------------------------------------------------------ the sheet --- */

function sheetCard() {
  const host = el('div', { id: 'designer-sheet' });
  renderSheet(host);

  return card(
    'Columns',
    host,
    [
      button('+ Input', { class: 'btn btn-sm', on: { click: () => addColumn('input') } }),
      button('+ From library', { class: 'btn btn-sm', on: { click: () => addColumn('library') } }),
      button('+ Derived', { class: 'btn btn-sm', on: { click: () => addColumn('derived') } }),
    ],
    'Model Reference is added automatically between the input and library columns. ' +
    'Every “from library” column becomes a field on this type’s equipment library, so ' +
    'the type is usable as soon as it is saved.'
  );
}

/** Redraw only the table, so a drag does not rebuild the whole page. */
function refreshSheet() {
  const host = document.getElementById('designer-sheet');
  if (host) renderSheet(host);
  drawDrawer();
}

function renderSheet(host) {
  clear(host);
  const columns = layout();

  const header = el('tr', {}, columns.map((column, index) => headerCell(column, index)));
  const units = el('tr', { class: 'units' }, columns.map((column) =>
    el('th', {
      class: `g-${column ? column.kind : 'input'}`,
      text: column ? prettyUnit(column.unit) : '',
    })
  ));
  const examples = el('tr', {}, columns.map((column) =>
    el('td', {
      class: `cell-${column ? column.kind : 'input'}`,
      style: 'padding:4px 7px',
      text: column ? show(column.example) : 'AHU-EXAMPLE-01',
    })
  ));

  host.appendChild(el('div', { class: 'sheet-wrap', style: 'max-height:none' }, [
    el('table', { class: 'sheet designer-sheet' }, [
      el('thead', {}, [header, units]),
      el('tbody', {}, [examples]),
    ]),
  ]));

  host.appendChild(el('div', { class: 'legend', style: 'margin-top:10px' }, [
    el('span', {}, [el('span', { class: 'swatch swatch-input' }), 'typed per unit']),
    el('span', {}, [el('span', { class: 'swatch swatch-library' }), 'from the equipment library']),
    el('span', {}, [el('span', { class: 'swatch swatch-derived' }), 'calculated']),
  ]));

  host.appendChild(el('div', { class: 'muted tiny', style: 'margin-top:8px' }, [
    `Formulas may use ${d.meta.constants.map((c) => c.alias).join(', ')}. `,
    `Spilling and post-2019 functions are rejected (${d.meta.banned_functions.slice(0, 6).join(', ')}…) `,
    'because the exported workbook writes static formulas only.',
  ]));

  if (!d.columns.length) {
    host.appendChild(empty(
      'No columns yet',
      'Add an input column for what the engineer types, and a library column for what ' +
      'comes from the product.'
    ));
  }
}

/** One header cell: the name, its kind, and the handles that move and size it. */
function headerCell(column, index) {
  if (!column) {
    // Model Reference. Not one of the authored columns, so nothing about it is
    // editable — the user never defines it and must not be able to move it.
    return el('th', { class: 'g-input auto-column', title: 'Added automatically as the lookup key' }, [
      el('div', { class: 'col-head' }, [
        el('span', { class: 'col-name-fixed', text: 'Model Reference' }),
      ]),
      el('div', { class: 'muted tiny', text: 'automatic' }),
    ]);
  }

  const th = el('th', {
    class: `g-${column.kind}${d.selected === column ? ' col-selected' : ''}`,
    // The stored width is in Excel characters; the header carries a grip, a
    // name field, a kind and a menu, so it cannot go below what those need.
    // The number itself is untouched — only how narrow the cell may be drawn.
    style: `width:${headerWidth(column)}px;min-width:${headerWidth(column)}px`,
    draggable: true,
    dataset: { columnIndex: String(d.columns.indexOf(column)) },
  });

  const name = el('input', {
    class: 'col-name',
    value: column.name,
    placeholder: 'Column name',
    title: 'The header, as it prints. Type here.',
    on: {
      input: (e) => { column.name = e.target.value; validate(); },
      // Typing must not start a drag, and clicking must select rather than move.
      mousedown: (e) => e.stopPropagation(),
      dragstart: (e) => { e.preventDefault(); e.stopPropagation(); },
      focus: () => selectColumn(column, { keepFocus: true }),
    },
  });

  const kind = select(
    d.meta.kinds.map((k) => [k.kind, k.label]),
    column.kind,
    {
      class: 'col-kind',
      title: (d.meta.kinds.find((k) => k.kind === column.kind) || {}).hint || '',
      on: {
        mousedown: (e) => e.stopPropagation(),
        change: (e) => {
          column.kind = e.target.value;
          if (column.kind === 'derived' && !column.formula) column.formula = '=';
          refreshSheet();
          validate();
        },
      },
    }
  );

  th.appendChild(el('div', { class: 'col-head' }, [
    el('span', { class: 'col-grip', title: 'Drag to move this column' }, ['⠿']),
    name,
    el('button', {
      class: 'icon-btn col-more',
      title: 'Unit, example, formula and where it appears',
      on: { click: (e) => { e.stopPropagation(); selectColumn(column); } },
    }, ['⋯']),
  ]));
  th.appendChild(kind);
  th.appendChild(insertHandle(column));
  th.appendChild(resizeHandle(th, column));

  wireDrag(th, column);
  return th;
}

/**
 * How wide to draw a header cell.
 *
 * The stored width is in Excel characters, which is what the sheet uses. The
 * header here also carries a grip, a name field, a menu and a kind, so it has a
 * floor — narrower and the name is unreadable, which defeats typing it in
 * place. The stored number is untouched; only the drawing has a minimum.
 */
function headerWidth(column) {
  return Math.max(column.width * 7, 136);
}

function prettyUnit(unit) {
  // The backend renders these properly on the sheet; this is the same
  // substitution for the two that actually occur in the house schedules.
  return String(unit || '').replace(/degC/g, '°C').replace(/m2/g, 'm²').replace(/m3/g, 'm³');
}

/* --------------------------------------------------------- moving them --- */

let draggingColumn = null;

function wireDrag(th, column) {
  th.addEventListener('dragstart', (event) => {
    draggingColumn = column;
    th.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    // Firefox needs something set or the drag never starts.
    event.dataTransfer.setData('text/plain', column.name || 'column');
  });
  th.addEventListener('dragend', () => {
    draggingColumn = null;
    document.querySelectorAll('.designer-sheet th').forEach((n) =>
      n.classList.remove('dragging', 'drop-before', 'drop-after'));
    refreshSheet();
  });
  th.addEventListener('dragover', (event) => {
    if (!draggingColumn || draggingColumn === column) return;
    event.preventDefault();
    const box = th.getBoundingClientRect();
    const after = event.clientX > box.left + box.width / 2;
    th.classList.toggle('drop-before', !after);
    th.classList.toggle('drop-after', after);
  });
  th.addEventListener('dragleave', () => {
    th.classList.remove('drop-before', 'drop-after');
  });
  th.addEventListener('drop', (event) => {
    event.preventDefault();
    if (!draggingColumn || draggingColumn === column) return;
    const box = th.getBoundingClientRect();
    const after = event.clientX > box.left + box.width / 2;
    moveColumn(draggingColumn, column, after);
  });
}

/**
 * Move one column next to another.
 *
 * A column dropped among columns of another kind takes that kind: the sheet is
 * grouped by kind — inputs, then library, then derived — so dropping a library
 * column between two inputs can only mean one thing, and refusing the drop
 * would just look broken.
 */
function moveColumn(column, target, after) {
  const from = d.columns.indexOf(column);
  if (from < 0) return;
  d.columns.splice(from, 1);

  if (column.kind !== target.kind) {
    column.kind = target.kind;
    if (column.kind === 'derived' && !column.formula) column.formula = '=';
  }
  const to = d.columns.indexOf(target) + (after ? 1 : 0);
  d.columns.splice(to, 0, column);

  refreshSheet();
  validate();
}

/** A right edge that sets the column's width in characters, as Excel means it. */
function resizeHandle(th, column) {
  const handle = el('span', { class: 'col-resize', title: 'Drag to set the width' });

  handle.addEventListener('mousedown', (event) => {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startWidth = column.width;

    const onMove = (move) => {
      // Roughly 7px per character at this font size; the stored value is in
      // characters because that is what Excel's column width means.
      const chars = Math.round((move.clientX - startX) / 7);
      column.width = Math.max(4, Math.min(80, startWidth + chars));
      th.style.width = th.style.minWidth = `${headerWidth(column)}px`;
      th.title = `${column.width} characters wide`;
      const box = document.querySelector('[data-width-input]');
      if (box && d.selected === column) box.value = String(column.width);
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      validate();
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });

  return handle;
}

/** A + between two columns, so a new one lands where it is needed. */
function insertHandle(column) {
  return el('button', {
    class: 'col-insert',
    title: 'Insert a column here',
    on: {
      click: (event) => {
        event.stopPropagation();
        const at = d.columns.indexOf(column);
        const fresh = blankColumn(column.kind);
        d.columns.splice(at, 0, fresh);
        refreshSheet();
        selectColumn(fresh);
        validate();
      },
    },
  }, ['+']);
}

function blankColumn(kind) {
  return {
    kind,
    name: '',
    unit: '',
    width: 14,
    example: '',
    formula: kind === 'derived' ? '=' : null,
    note: '',
    visibility: {},
  };
}

function addColumn(kind) {
  const fresh = blankColumn(kind);
  d.columns.push(fresh);
  refreshSheet();
  selectColumn(fresh);
  validate();
}

/* --------------------------------------------------------------- drawer --- */

function selectColumn(column, { keepFocus = false } = {}) {
  d.selected = column;
  document.querySelectorAll('.designer-sheet th').forEach((n) =>
    n.classList.remove('col-selected'));
  const index = d.columns.indexOf(column);
  const th = document.querySelector(`.designer-sheet th[data-column-index="${index}"]`);
  if (th) th.classList.add('col-selected');
  drawDrawer({ keepFocus });
}

function closeDrawer() {
  d.selected = null;
  const existing = document.getElementById('designer-drawer');
  if (existing) existing.remove();
  document.querySelectorAll('.designer-sheet th').forEach((n) =>
    n.classList.remove('col-selected'));
}

/**
 * Everything about a column that is not visible in the sheet.
 *
 * A drawer rather than more table: the unit, the formula and the visibility are
 * settings on one column, and putting them in the grid is what made the old
 * screen a spreadsheet of a spreadsheet.
 */
function drawDrawer({ keepFocus = false } = {}) {
  const existing = document.getElementById('designer-drawer');
  if (existing) existing.remove();
  const column = d.selected;
  if (!column || !d.columns.includes(column)) return;

  const kindHint = (d.meta.kinds.find((k) => k.kind === column.kind) || {}).hint || '';

  const body = el('div', { class: 'drawer-body' }, [
    el('div', { class: 'muted tiny', text: kindHint }),

    field('Unit', input(column.unit, {
      // No placeholder: a greyed 'l/s' on every unit-less column reads as a real
      // unit at a glance, which is worse than an empty box.
      title: 'Rendered on the unit row beneath the header. Leave blank if there is none.',
      on: { input: (e) => { column.unit = e.target.value; refreshSheetUnits(); validate(); } },
    }), 'Printed on the row under the header, so the header itself stays short.'),

    field('Width', input(String(column.width), {
      type: 'number',
      dataset: { widthInput: 'true' },
      on: {
        input: (e) => {
          column.width = parseInt(e.target.value, 10) || 14;
          refreshSheetWidths();
          validate();
        },
      },
    }), 'In characters, as Excel measures it. Or drag the column edge.'),

    column.kind === 'derived'
      ? el('div', {}, [
          field('Formula', input(column.formula || '', {
            class: 'mono',
            placeholder: '={Airflow (l/s)}*2',
            on: { input: (e) => { column.formula = e.target.value; validate(); } },
          }), 'Reference other columns by name in braces.'),
          field('Note', input(column.note || '', {
            placeholder: 'Shown as the cell comment',
            on: { input: (e) => { column.note = e.target.value; validate(); } },
          }), 'Becomes the comment on the header, explaining the calculation.'),
        ])
      : field('Example', input(show(column.example), {
          placeholder: 'Example value',
          on: { input: (e) => { column.example = e.target.value; refreshSheetExamples(); validate(); } },
        }), 'Shown in the preview row and in an empty exported sheet.'),

    el('div', { class: 'field' }, [
      el('label', { text: 'Where it appears' }),
      el('div', { class: 'checks' }, [
        visibilityToggle(column, 'editor', 'The editor'),
        visibilityToggle(column, 'xlsx', 'Exported .xlsx'),
        visibilityToggle(column, 'pdf', 'Issued PDF'),
      ]),
      el('span', { class: 'help' }, [
        'Hiding a column keeps its values and its formulas — it just stops printing. ' +
        'That is how something internal, such as a price, stays off an issued document.',
      ]),
    ]),

    el('div', { class: 'btn-row', style: 'margin-top:16px' }, [
      button('Delete column', {
        class: 'btn btn-danger btn-sm',
        on: { click: () => deleteColumn(column) },
      }),
    ]),
  ]);

  const drawer = el('aside', { id: 'designer-drawer', class: 'drawer' }, [
    el('header', { class: 'drawer-head' }, [
      el('div', {}, [
        el('strong', { text: column.name || 'Untitled column' }),
        el('div', { class: 'muted tiny' }, [
          pill(column.kind, column.kind === 'library' ? 'green' : column.kind === 'derived' ? 'amber' : 'blue'),
        ]),
      ]),
      el('button', { class: 'icon-btn', title: 'Close', on: { click: closeDrawer } }, ['×']),
    ]),
    body,
  ]);

  document.body.appendChild(drawer);
  if (!keepFocus) {
    const first = drawer.querySelector('input');
    if (first) first.focus();
  }
}

function visibilityToggle(column, target, label) {
  const box = el('input', {
    type: 'checkbox',
    checked: (column.visibility || {})[target] !== false,
    on: {
      change: (e) => {
        const visibility = { ...(column.visibility || {}) };
        if (e.target.checked) delete visibility[target];
        else visibility[target] = false;
        column.visibility = visibility;
        validate();
      },
    },
  });
  return el('label', { class: 'tiny check' }, [box, label]);
}

async function deleteColumn(column) {
  const ok = await confirmDialog({
    title: `Delete ${column.name || 'this column'}?`,
    message:
      'It disappears from every schedule of this type. Values already typed into it are ' +
      'kept in the record but stop being shown or exported.',
    confirmLabel: 'Delete column',
    danger: true,
  });
  if (!ok) return;
  const at = d.columns.indexOf(column);
  if (at >= 0) d.columns.splice(at, 1);
  closeDrawer();
  refreshSheet();
  validate();
}

/* Cheap partial updates, so typing in the drawer moves the sheet without a
   rebuild that would steal the caret. */

function refreshSheetWidths() {
  d.columns.forEach((column, index) => {
    const th = document.querySelector(`.designer-sheet th[data-column-index="${index}"]`);
    if (th) th.style.width = th.style.minWidth = `${headerWidth(column)}px`;
  });
}

function refreshSheetUnits() {
  const cells = document.querySelectorAll('.designer-sheet tr.units th');
  layout().forEach((column, i) => {
    if (cells[i]) cells[i].textContent = column ? prettyUnit(column.unit) : '';
  });
}

function refreshSheetExamples() {
  const cells = document.querySelectorAll('.designer-sheet tbody td');
  layout().forEach((column, i) => {
    if (cells[i] && column) cells[i].textContent = show(column.example);
  });
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
      list.appendChild(el('div', { class: 'note-row' }, [
        el('span', { class: 'muted tiny', text: `[${d.projectNotes.length + i + 1}]` }),
        textarea(note, {
          rows: 2,
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
        'wording below that is specific to this equipment — do not repeat the generic text. ' +
        'A project and an individual schedule can add to or override the result.',
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

/**
 * Save, having first said what the change lands on.
 *
 * Widths, order and visibility reach every schedule of this type at once, and
 * are meant to. Adding, removing or renaming a column changes what a schedule
 * holds, and rows already typed are keyed by column name — so the dry run runs
 * first and the warnings are shown before the save, not after somebody finds a
 * duty missing.
 */
async function save() {
  const errors = d.issues.filter((i) => i.severity === 'error');
  if (errors.length) {
    fail(new Error('Fix the problems listed above first.'));
    return;
  }

  let change = '';
  if (d.id) {
    let impact = null;
    try {
      impact = await api.catalogue.impact(d.id, payload());
    } catch { /* the save itself is still safe without the preview */ }

    if (impact && impact.diff.summary === 'no change') {
      toast('Nothing has changed', 'ok');
      return;
    }

    const note = input('', { placeholder: impact ? impact.diff.summary : 'added Filter Grade' });
    const ok = await modal({
      title: 'Describe the change',
      wide: Boolean(impact && impact.affected_count),
      render: () => el('div', {}, [
        impact ? impactSummary(impact) : null,
        el('p', { class: 'muted tiny' }, [
          'The version is bumped and this note is recorded against it.',
        ]),
        field('What changed', note, 'Left blank, the change itself is described.'),
      ]),
      actions: (close) => [
        button('Cancel', { on: { click: () => close(false) } }),
        button('Save', {
          class: `btn ${impact && impact.diff.structural ? 'btn-danger' : 'btn-primary'}`,
          on: { click: () => close(true) },
        }),
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

function impactSummary(impact) {
  const parts = [];
  const diff = impact.diff;

  parts.push(notice(
    diff.summary.charAt(0).toUpperCase() + diff.summary.slice(1) + '.',
    diff.structural ? 'warn' : 'info'
  ));

  if (diff.warnings.length) {
    parts.push(notice('Before you save:', 'error', diff.warnings));
  }

  if (impact.affected_count) {
    parts.push(el('p', { class: 'tiny' }, [
      `${impact.affected_count} schedule(s) use this type`,
      impact.rows_at_risk
        ? `, holding ${impact.rows_at_risk} filled row(s) between them.`
        : '. Layout changes reach them immediately, which is the intention.',
    ]));
    parts.push(table(
      ['Project', 'Building', 'Schedule', { text: 'Filled rows', class: 'num' }],
      impact.affected.slice(0, 8).map((a) => el('tr', {}, [
        el('td', { class: 'tiny', text: a.project }),
        el('td', { class: 'tiny', text: a.building }),
        el('td', { class: 'tiny' }, [el('strong', { text: a.code })]),
        el('td', { class: 'num tiny', text: String(a.rows) }),
      ]))
    ));
    if (impact.affected.length > 8) {
      parts.push(el('p', { class: 'muted tiny' }, [
        `and ${impact.affected.length - 8} more.`,
      ]));
    }
  } else {
    parts.push(el('p', { class: 'muted tiny' }, [
      'No schedule has been built from this type yet, so nothing is affected.',
    ]));
  }

  return el('div', {}, parts);
}
