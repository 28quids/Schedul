// The schedule editor.
//
// The colour contract is the same one the printed schedule uses, so the screen
// and the paper mean the same thing: blue on yellow you type, green pulled from
// the equipment library, black calculated.
//
// Derived and library cells are never editable and never sent to the server.
// They are recomputed on every save from what was typed, which is why correcting
// a product in the library fixes every schedule that uses it at once.
//
// **The grid is never rebuilt while you are typing in it.** Saving a cell
// patches the computed cells in place and leaves every input's DOM node, value
// and caret alone. Rebuilding was destroying the focused input mid-keystroke,
// which is what made the grid feel like it was eating input. A full redraw
// happens only when the row structure changes, and focus is restored after it.
//
// **The grid is a spreadsheet, not a form.** There is one active cell and a
// rectangle around it: drag or Shift+Arrow to extend it, copy and paste blocks
// through it, clear or delete what it covers. The rules for what a keystroke
// means and what a pasted block would overwrite live in `../grid/`, away from
// the DOM, because they are the parts that have to be exactly right.

import { api } from '../api.js';
import { go, setContext, store } from '../app.js';
import {
  anchoredList, button, card, clear, confirmDialog, debounce, download, el, empty,
  fail, field, formatDate, input, modal, mount, notice, pageHead, pill, select, show,
  table, textarea, toast, toolbar,
} from '../ui.js';
import {
  bounds, cellSelection, cells as selectedCells, clampTo, columns as selectedColumns,
  activeCell, contains, isSingleCell, move, nextInRange, rows as selectedRows,
  selectRows, size, step, withActive,
} from '../grid/selection.js';
import { decide } from '../grid/keys.js';
import { productGrid } from './library-entry.js';
import { parseTsv, planBlockPaste, selectionMatrix, toTsv } from '../grid/clipboard.js';

let view = null;

/** DOM index so a save can find one cell without touching the rest. */
let cellIndex = new Map();
const cellKey = (rowId, column) => `${rowId} ${column}`;

/** The active cell and the rectangle around it, as row and column indices. */
let sel = null;
/** True while a drag-select is in progress. */
let dragging = false;
/**
 * The fill handle's drag, while one is in progress.
 *
 * `{ box, to }` — the selection the drag started from and the row it currently
 * reaches. Kept separate from `sel` so the selection itself does not move while
 * somebody is deciding how far to drag.
 */
let fillDrag = null;
/** Which column a run of tabbing began in, so Enter comes back to it. */
let tabAnchor = null;

/** Per-row debounce timers, so typing across a row coalesces into one save. */
const pending = new Map();

/**
 * Library cells taken over but not yet given a value.
 *
 * An override is stored only once it has one — an empty override is how a row
 * is put back on the library, so the two cannot both be written. Until then the
 * cell is an input here and nowhere else, which is what lets it be typed into
 * the instant the pencil is clicked rather than after a round trip.
 */
const draftOverrides = new Set();

export async function scheduleView(scheduleId) {
  const [grid, revisions, meta] = await Promise.all([
    api.schedules.grid(scheduleId),
    api.schedules.revisions(scheduleId),
    api.catalogue.meta(),
  ]);
  // The other schedules in this building, so the sidebar can move between them
  // without a trip back to the project page.
  let siblings = [];
  try {
    const project = await api.projects.read(grid.project_id);
    const building = project.buildings.find((b) => b.id === grid.building_id);
    siblings = (building ? building.schedules : []).map((s) => ({
      id: s.id, code: s.code, title: s.title,
    }));
  } catch { /* the sidebar is a convenience, not a requirement */ }
  const sameSchedule = view && view.id === scheduleId;
  view = {
    grid, revisions, meta, siblings, id: scheduleId,
    tab: sameSchedule ? view.tab : 'schedule',
  };
  if (!sameSchedule) { sel = null; tabAnchor = null; draftOverrides.clear(); }
  draw();
}

function draw() {
  const { schedule, project_id, project_name, building_ref, building_count } = view.grid;

  setContext({
    projectId: project_id,
    projectName: project_name,
    building: building_count > 1 ? building_ref : '',
    scheduleId: view.id,
    schedules: view.siblings || [{ id: view.id, code: schedule.code, title: schedule.title }],
  });

  const page = el('div', { class: 'page page-wide' }, [
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/projects', text: 'Projects' }), ' / ',
      el('a', { href: `#/projects/${project_id}`, text: project_name }), ' / ',
      building_count > 1 ? `${building_ref} / ` : '',
      schedule.code,
    ]),
    pageHead(
      schedule.title,
      el('span', { class: 'dn', text: schedule.docnum }),
      [
        schedule.locked ? pill('issued', 'amber') : null,
        el('span', { class: 'saving', id: 'save-state' }),
        // An export is a document being sent to somebody, so it is plain by
        // default. The working copy keeps the editing colours for anyone who
        // wants the file to look like the screen they typed it into.
        button('Working copy', {
          title: 'The same numbers, with the editing colours kept',
          on: {
            click: () => download(`/api/schedules/${view.id}/export.xlsx?theme=editor`),
          },
        }),
        // Whichever deliverable this machine can produce is the primary action:
        // without LibreOffice there was no primary button on the page at all.
        button('Export .xlsx', {
          class: store.pdfAvailable ? 'btn' : 'btn btn-primary',
          title: 'The issued document: neutral print styling, no editing colours',
          on: { click: () => download(`/api/schedules/${view.id}/export.xlsx`) },
        }),
        store.pdfAvailable
          ? button('Export PDF', {
              class: 'btn btn-primary',
              on: { click: () => download(`/api/schedules/${view.id}/export.pdf`) },
            })
          : null,
      ]
    ),
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

/** Column indices Tab and Enter walk: the ones somebody can type into. */
function typeableIndexes() {
  const overridden = new Set(
    view.grid.rows.flatMap((r) => Object.keys(r.overrides || {}))
  );
  return view.grid.columns
    .map((c, i) => (c.editable || overridden.has(c.legacy_name) ? i : -1))
    .filter((i) => i >= 0);
}

function extent() {
  return { rowCount: view.grid.rows.length, columnCount: view.grid.columns.length };
}

function columnAt(index) { return view.grid.columns[index]; }
function rowAt(index) { return view.grid.rows[index]; }

/** Whether this particular cell accepts typing: an input, or an overridden one. */
function isTypeable(row, column) {
  return Boolean(column && (column.editable || isOverridden(row, column.legacy_name)));
}

function drawGrid(root) {
  const { columns, rows } = view.grid;
  cellIndex = new Map();
  sel = clampTo(sel, extent());

  root.appendChild(gridToolbar());

  if (!rows.length) {
    root.appendChild(el('section', { class: 'card' }, [
      empty(
        'No equipment scheduled yet',
        'Add a row and start typing. Pick a Model Reference and the product columns fill ' +
        'themselves in; the calculated columns follow as you go.',
        button('Add the first row', { class: 'btn btn-primary', on: { click: () => addRow() } })
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
        // The designer's width is in Excel characters; roughly 7px each at this
        // font size. Honouring it here is what makes the editor and the exported
        // sheet the same shape, so a column widened in the designer looks wider
        // in both.
        style: `min-width:${Math.max(48, Math.min((c.width || 14) * 7, 300))}px`,
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

  const sheet = el('div', { class: 'sheet-wrap' }, [
    el('table', { class: 'sheet selectable' }, [head, body]),
  ]);

  // One listener for the whole grid rather than one per input: the keyboard
  // rules are about the grid, and a cell that is read-only has no input to
  // listen on at all.
  sheet.addEventListener('keydown', onGridKeyDown);
  sheet.addEventListener('copy', onCopy);
  sheet.addEventListener('paste', onPaste);
  sheet.addEventListener('mousedown', onMouseDown);
  sheet.addEventListener('mouseover', onMouseOver);

  root.appendChild(sheet);
  root.appendChild(el('div', { class: 'sheet-hint', style: 'margin-top:8px' }, [
    'Arrows move, Enter goes down, Tab goes right. Shift+arrows or a drag select a ' +
    'block; Ctrl+C and Ctrl+V copy and paste one. Drag the small square at the corner ' +
    'of the selection to fill — a reference ending in digits counts up, and holding ' +
    'Ctrl copies instead. Delete clears the selection, Ctrl+D fills it down, Ctrl+Z undoes.',
  ]));

  renderTypeDrift(root);
  renderProblemSummary(root);
  paintSelection();
}

/**
 * Say when the schedule type has moved on since this schedule was set up.
 *
 * The columns here are always the type's current ones — that is what makes a
 * change in the designer take effect — so this is not a warning that anything
 * is broken. It is where a new column or a different width came from, on the
 * screen where somebody would notice it.
 */
function renderTypeDrift(root) {
  const drift = view.grid.type_drift;
  if (!drift || !drift.current) return;

  const changes = (drift.changes || []).filter((c) => c.change);
  root.appendChild(el('div', { style: 'margin-top:12px' }, [
    notice(
      `This schedule was set up against ${view.grid.schedule.code} v${drift.built_against}; ` +
      `the type is now at v${drift.current}. Its columns follow the type, so what you see ` +
      'here is current.',
      'info',
      changes.map((c) => `v${c.version}${c.date ? ` (${c.date})` : ''}: ${c.change}`)
    ),
  ]));
}

/**
 * The grid's toolbar. Built once per redraw, then updated in place.
 *
 * In place is the whole point. It used to be rebuilt whenever the selection
 * moved, and a save triggered by the caret leaving a cell moves the selection —
 * so clicking Fill down with the caret still in a cell replaced the button
 * between the mousedown and the mouseup, the click never landed on anything,
 * and the first press of every toolbar button silently did nothing. Mutating
 * the labels a node already has cannot do that.
 */
let toolbarNodes = null;

function gridToolbar() {
  const rowCount = el('input', {
    type: 'number', min: '1', max: '500', value: '1', class: 'row-count',
    title: 'How many rows to add',
    on: {
      input: () => refreshToolbar(),
      keydown: (event) => {
        if (event.key === 'Enter') { event.preventDefault(); addRows(); }
      },
    },
  });

  const nodes = {
    rowCount,
    add: button('Add row', {
      class: 'btn btn-primary',
      title: 'Add a row at the end. Type a number beside it to add several.',
      on: { click: () => addRows() },
    }),
    duplicate: button('Duplicate', {
      title: 'Copy the selected row and insert it below',
      on: { click: duplicateSelected },
    }),
    remove: button('Delete row', {
      class: 'btn btn-danger',
      title: 'Remove the selected rows. Undoable.',
      on: { click: deleteSelectedRows },
    }),
    fillDown: button('Fill down', {
      title: 'Copy the top cell of the selection into the rest of it (Ctrl+D). ' +
        'Or drag the small square at the corner of the selection.',
      on: { click: () => fillSelection('copy') },
    }),
    fillSeries: button('Fill series', {
      title: 'Count up from the top of the selection, e.g. RAD-001, RAD-002, RAD-003',
      on: { click: () => fillSelection('series') },
    }),
    pasteRows: button('Paste rows…', { on: { click: pasteRows } }),
    workbook: button('Excel…', {
      title: 'Take this schedule out as a spreadsheet, fill it in, and bring it back',
      on: { click: workbookDialog },
    }),
    override: button('Override cell', { on: { click: overrideSelection } }),
    restore: button('Restore from library', { on: { click: restoreSelection } }),
    undo: button('Undo', { on: { click: undoEdit } }),
    redo: button('Redo', { on: { click: redoEdit } }),
    columns: button('Columns…', {
      title: 'Which columns show here, on the Excel export and on the PDF',
      on: { click: columnsDialog },
    }),
  };
  toolbarNodes = nodes;

  const bar = toolbar([
    ['Rows', [
      el('div', { class: 'add-rows' }, [nodes.add, rowCount]),
      nodes.duplicate,
      nodes.remove,
    ]],
    ['Fill', [nodes.fillDown, nodes.fillSeries, nodes.pasteRows, nodes.workbook]],
    ['Equipment library', [nodes.override, nodes.restore]],
    ['History', [nodes.undo, nodes.redo]],
  ], [
    nodes.columns,
    el('div', { class: 'legend' }, [
      el('span', {}, [el('span', { class: 'swatch swatch-input' }), 'you type']),
      el('span', {}, [el('span', { class: 'swatch swatch-library' }), 'library']),
      el('span', {}, [el('span', { class: 'swatch swatch-derived' }), 'calculated']),
    ]),
  ]);
  refreshToolbar();
  return bar;
}

/** Bring the toolbar's labels and enabled states up to date. Replaces nothing. */
function refreshToolbar() {
  const nodes = toolbarNodes;
  if (!nodes || !nodes.add.isConnected) return;

  const history = view.grid.history || {};
  const selectedRowCount = sel ? size(sel).rows : 0;
  const libraryCells = selectedLibraryCells();
  const overridable = libraryCells.reduce((n, e) => n + e.keys.length, 0);
  const overridden = libraryCells.reduce(
    (n, e) => n + e.keys.filter((k) => isOverridden(e.row, k)).length, 0
  );

  const wanted = Math.max(1, Number(nodes.rowCount.value) || 1);
  nodes.add.textContent = wanted > 1 ? `Add ${wanted} rows` : 'Add row';

  nodes.remove.textContent =
    selectedRowCount > 1 ? `Delete ${selectedRowCount} rows` : 'Delete row';

  nodes.override.textContent =
    overridable > 1 ? `Override ${overridable} cells` : 'Override cell';
  nodes.override.disabled = !overridable;
  nodes.override.title = overridable
    ? 'Take every library cell in the selection over, so this schedule can say ' +
      'something different from the library'
    : 'Select one or more library (green) cells first';

  nodes.restore.disabled = !overridden;
  nodes.restore.title = overridden
    ? `Put ${overridden} overridden cell(s) back on the library value`
    : 'Nothing in the selection is overridden';

  nodes.undo.disabled = !history.can_undo;
  nodes.undo.title = history.can_undo
    ? `Undo ${history.undo_label} (Ctrl+Z)` : 'Nothing to undo';
  nodes.redo.disabled = !history.can_redo;
  nodes.redo.title = history.can_redo
    ? `Redo ${history.redo_label}` : 'Nothing to redo';
}

/** The library cells the selection covers, grouped by row. */
function selectedLibraryCells() {
  if (!sel) return [];
  const out = [];
  for (const r of selectedRows(sel)) {
    const row = rowAt(r);
    if (!row) continue;
    const keys = [];
    for (const c of selectedColumns(sel)) {
      const column = columnAt(c);
      if (column && column.kind === 'library') keys.push(column.legacy_name);
    }
    if (keys.length) out.push({ row, keys });
  }
  return out;
}

/* ---------------------------------------------------- library overrides --- */

/**
 * Take every library cell in the selection over at once.
 *
 * A row that diverges from the library usually diverges in company: the same
 * unit at a different duty is a block of cells, not one. Doing them one pencil
 * at a time is the difference between a feature somebody uses and one they work
 * around by typing the value into the notes.
 */
async function overrideSelection() {
  const entries = selectedLibraryCells();
  if (!entries.length) {
    toast('Select one or more library cells first', 'err');
    return;
  }

  const edits = [];
  let count = 0;
  for (const { row, keys } of entries) {
    const overrides = {};
    for (const key of keys) {
      if (isOverridden(row, key)) continue;
      const current = row.computed[key];
      // An empty override is how a cell is put back on the library, so a cell
      // with nothing to carry over would undo itself the moment it was saved.
      if (current === null || current === undefined || current === '') continue;
      overrides[key] = current;
      count += 1;
    }
    if (Object.keys(overrides).length) edits.push({ row_id: row.id, values: {}, overrides });
  }

  if (!edits.length) {
    toast('Those cells have no library value to take over yet', 'err');
    return;
  }

  try {
    view.grid = await api.schedules.editCells(view.id, edits, 'override_cells');
    redrawPreservingFocus();
    toast(`${count} cell(s) taken over — type over them, or ↺ to restore`, 'ok');
  } catch (error) { fail(error); }
}

/** Put every overridden cell in the selection back on the library value. */
async function restoreSelection() {
  const entries = selectedLibraryCells();
  const edits = [];
  let count = 0;
  for (const { row, keys } of entries) {
    const overrides = {};
    for (const key of keys) {
      if (!isOverridden(row, key)) continue;
      overrides[key] = '';
      draftOverrides.delete(cellKey(row.id, key));
      count += 1;
    }
    if (Object.keys(overrides).length) edits.push({ row_id: row.id, values: {}, overrides });
  }
  if (!edits.length) {
    toast('Nothing in the selection is overridden', 'err');
    return;
  }
  try {
    view.grid = await api.schedules.editCells(view.id, edits, 'restore_cells');
    redrawPreservingFocus();
    toast(`${count} cell(s) back on the library value — Ctrl+Z to undo`, 'ok');
  } catch (error) { fail(error); }
}

/* ------------------------------------------------- the schedule workbook --- */

/**
 * Take the schedule out as a spreadsheet, fill it in, bring it back.
 *
 * The other export is the deliverable — cover, revision page, calculated
 * columns and all. This is the working file: the columns somebody types into,
 * with the headings on row 1. Filling in a hundred rows is quicker where the
 * fill handle and the keyboard are already familiar, and there is no reason the
 * tool should insist otherwise.
 *
 * Product and calculated columns are not in it. One is looked up and the other
 * is worked out, so a filled-in copy of either would be a stale copy of
 * something the schedule already knows.
 */
async function workbookDialog() {
  const picker = el('input', { type: 'file', accept: '.xlsx,.xlsm' });
  const mode = select(
    [
      ['append', 'Add to the end (safe)'],
      ['replace', 'Replace every row'],
    ],
    'append'
  );
  const summary = el('div');
  let plan = null;

  const refresh = async () => {
    const file = picker.files && picker.files[0];
    if (!file) { clear(summary); plan = null; return; }
    clear(summary).appendChild(el('div', { class: 'muted', text: 'Reading the workbook…' }));
    try {
      plan = await api.schedules.importRows(view.id, file, { mode: mode.value });
      clear(summary).appendChild(renderPastePlan(plan));
    } catch (error) {
      plan = null;
      clear(summary).appendChild(notice(error.message, 'error'));
    }
  };
  picker.addEventListener('change', refresh);
  mode.addEventListener('change', refresh);

  const ok = await modal({
    title: 'This schedule in Excel',
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'The columns you type into, with the headings on row 1. Product and calculated ',
        'columns are left out — one is looked up from the equipment library and the other ',
        'is worked out, so filling them in here would be filling in a copy.',
      ]),
      el('div', { class: 'btn-row', style: 'margin-bottom:14px' }, [
        button('Download this schedule', {
          title: 'What is on it now, ready to edit',
          on: { click: () => download(api.schedules.rowsUrl(view.id, true)) },
        }),
        button('Blank workbook', {
          title: 'The same headings with nothing under them',
          on: { click: () => download(api.schedules.rowsUrl(view.id, false)) },
        }),
      ]),
      field('Bring one back', picker),
      el('div', { style: 'margin-top:12px' }, [
        field(
          'Where',
          mode,
          'A file you exported from this schedule and corrected comes back as a ' +
          'replacement. Adding to the end is for rows that are new.'
        ),
      ]),
      summary,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Import', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok || !plan) return;

  if (!plan.detected_rows) {
    toast('No rows were found in that workbook', 'err');
    return;
  }
  if (plan.destructive) {
    const confirmed = await confirmDialog({
      title: 'Replace every row?',
      message:
        `${plan.populated_removed} filled-in row(s) will be removed and replaced with the ` +
        `${plan.detected_rows} row(s) in the workbook.`,
      confirmLabel: 'Replace rows',
      danger: true,
      detail: el('p', { class: 'muted tiny' }, ['This can be undone with Ctrl+Z.']),
    });
    if (!confirmed) return;
  }

  try {
    const applied = await api.schedules.importRows(view.id, picker.files[0], {
      mode: mode.value, apply: true, confirm: true,
    });
    view.grid = applied.grid;
    sel = null;
    draw();
    toast(`${applied.applied} row(s) brought in — Ctrl+Z to undo`, 'ok');
  } catch (error) { fail(error); }
}

/* -------------------------------------------------------------- columns --- */

/**
 * Which columns this schedule shows, and where.
 *
 * Three answers per column rather than one: a price belongs on the screen and
 * in the working file and not on the copy that goes to the client. Which
 * columns cannot be hidden comes from the server, so the screen cannot offer a
 * switch that would produce a workbook with a broken reference in it.
 */
async function columnsDialog() {
  let data;
  try {
    data = await api.schedules.columns(view.id);
  } catch (error) { fail(error); return; }

  const LABELS = { editor: 'On screen', xlsx: 'Excel', pdf: 'PDF' };
  const SHOW_ALL = {
    editor: 'Show all on screen', xlsx: 'Show all in Excel', pdf: 'Show all in PDF',
  };
  const boxes = new Map();

  const rows = data.columns.map((column) => {
    const cells = data.targets.map((target) => {
      const box = el('input', {
        type: 'checkbox',
        checked: column.visibility[target] !== false,
        disabled: !column.hideable,
      });
      boxes.set(`${column.legacy_name}|${target}`, box);
      return el('td', { class: 'tick' }, [box]);
    });
    return el('tr', {}, [
      el('td', {}, [
        el('div', {}, [column.name]),
        el('div', { class: 'muted tiny' }, [
          column.unit ? `${column.unit} · ` : '',
          column.kind,
          column.reason ? ` · ${column.reason}` : '',
        ]),
      ]),
      ...cells,
    ]);
  });

  const setColumn = (target, shown) => {
    for (const column of data.columns) {
      if (!column.hideable) continue;
      const box = boxes.get(`${column.legacy_name}|${target}`);
      if (box) box.checked = shown;
    }
  };

  const ok = await modal({
    title: 'Columns on this schedule',
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'Hiding a column here changes this schedule only — the equipment type and every ' +
        'other schedule built from it are untouched. The values are still stored; they ' +
        'are simply not printed.',
      ]),
      el('div', { class: 'btn-row', style: 'margin-bottom:10px' }, data.targets.map((t) =>
        button(SHOW_ALL[t], { class: 'btn btn-sm', on: { click: () => setColumn(t, true) } })
      )),
      table(['Column', ...data.targets.map((t) => LABELS[t])], rows),
      el('p', { class: 'muted tiny', style: 'margin-top:10px' }, [
        'The Model Reference and any column a calculation reads cannot be hidden: the ' +
        'workbook would come out with a broken reference in it.',
      ]),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Save', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok) return;

  const payload = {};
  for (const column of data.columns) {
    if (!column.hideable) continue;
    const hidden = {};
    for (const target of data.targets) {
      const box = boxes.get(`${column.legacy_name}|${target}`);
      if (box && !box.checked) hidden[target] = false;
    }
    if (Object.keys(hidden).length) payload[column.legacy_name] = hidden;
  }

  try {
    await api.schedules.setColumns(view.id, payload);
    const hiddenCount = Object.keys(payload).length;
    view.grid = await api.schedules.grid(view.id);
    sel = null;
    draw();
    toast(
      hiddenCount
        ? `${hiddenCount} column(s) hidden somewhere`
        : 'Every column shows everywhere',
      'ok'
    );
  } catch (error) { fail(error); }
}

function renderProblemSummary(root) {
  const existing = document.getElementById('grid-problems');
  const host = existing || el('div', { id: 'grid-problems', style: 'margin-top:12px' });
  clear(host);

  const count = view.grid.rows.reduce((n, r) => n + Object.keys(r.problems || {}).length, 0);
  if (count) {
    host.appendChild(notice(
      `${count} cell(s) could not be calculated or looked up. Hover a red cell for the reason, ` +
      'or use the ! button on the row to fix the model reference.',
      'warn'
    ));
  }
  if (!existing) root.appendChild(host);
}

function isOverridden(row, key) {
  return Boolean(row) && Object.prototype.hasOwnProperty.call(row.overrides || {}, key);
}

/** Whether this cell is currently an input, override drafts included. */
function isTyping(row, key) {
  return isOverridden(row, key) || draftOverrides.has(cellKey(row.id, key));
}

function renderRow(row, index, columns) {
  const tr = el('tr', { dataset: { rowId: row.id, r: String(index) } }, [
    el('td', {
      class: 'rowno',
      text: String(index + 1),
      title: 'Click to select the whole row',
      on: { mousedown: (event) => onRowNumberDown(event, index) },
    }),
  ]);

  columns.forEach((column, c) => {
    const key = column.legacy_name;
    const td = column.editable
      ? renderEditableCell(row, column, key)
      : column.kind === 'library'
        ? renderLibraryCell(row, column, key)
        : renderComputedCell(row, column, key);
    td.dataset.r = String(index);
    td.dataset.c = String(c);
    // A read-only cell still has to be able to hold the keyboard, or arrowing
    // across the grid would stop dead at the first calculated column.
    if (!td.querySelector('input')) td.tabIndex = -1;
    tr.appendChild(td);
    cellIndex.set(cellKey(row.id, key), { td, row, column, r: index, c });
  });

  tr.appendChild(el('td', { class: 'cell-actions' }, [
    el('button', {
      class: 'icon-btn', title: 'Duplicate this row',
      on: { click: () => duplicateRow(row) },
    }, ['⧉']),
    el('button', {
      class: 'icon-btn', title: 'Delete this row',
      on: { click: () => deleteRow(row) },
    }, ['×']),
  ]));

  return tr;
}

function renderComputedCell(row, column, key) {
  const problem = (row.problems || {})[key];
  const value = row.computed[key];
  return el('td', {
    class: `cell-${column.kind}${problem ? ' cell-problem' : ''}`,
    title: problem || '',
    text: problem ? '—' : show(value),
  });
}

/**
 * A library cell: read-only until the user overrides it.
 *
 * Some values legitimately differ between two uses of the same unit — SFP at a
 * different duty, for instance — so a row has to be able to diverge without
 * abandoning the library. An override is stored separately from typed values,
 * shown in the input colour, and reversible.
 *
 * Where the value came from is on the cell itself rather than in a legend: a
 * pencil to take it over, a dot while it is the library's, and a marked cell
 * with a reset button once it is not.
 */
function renderLibraryCell(row, column, key) {
  const overridden = isTyping(row, key);
  const problem = (row.problems || {})[key];

  if (!overridden) {
    const td = renderComputedCell(row, column, key);
    td.classList.add('cell-source-library');
    td.appendChild(el('button', {
      class: 'cell-override',
      title: 'From the equipment library. Click to use a different value on this row only.',
      on: { click: (event) => { event.stopPropagation(); startOverride(row, key); } },
    }, ['✎']));
    return td;
  }

  return overrideCell(row, column, key);
}

/** The editable form of a library cell, once this row diverges from the library. */
function overrideCell(row, column, key) {
  const td = el('td', {
    class: 'cell-input cell-overridden',
    title: 'Overridden on this row. The library value is unchanged; ↺ restores it.',
  });
  const box = input((row.overrides || {})[key] ?? '', {
    on: {
      input: (e) => onOverrideType(row, key, e.target.value),
      blur: () => flushRow(row),
      focus: () => focusFromCell(row.id, key),
      keydown: (e) => { if (e.key === 'Escape') clearOverride(row, key); },
    },
  });
  td.appendChild(el('div', { class: 'with-flag' }, [
    box,
    el('button', {
      class: 'cell-flag cell-reset',
      title: 'Restore the value from the equipment library',
      on: { click: (event) => { event.stopPropagation(); clearOverride(row, key); } },
    }, ['↺']),
  ]));
  return td;
}

/**
 * Take a library cell over, without waiting for the server.
 *
 * The cell becomes an input immediately and takes the caret, because an
 * override that needs a round trip and a redraw before it can be typed into is
 * an override nobody uses. The save follows behind; if it fails the toast says
 * so and the value is still on screen to try again.
 */
function startOverride(row, key) {
  const entry = cellIndex.get(cellKey(row.id, key));
  if (!entry) return;
  const current = row.computed[key];
  row.overrides = { ...(row.overrides || {}), [key]: current ?? '' };

  const replacement = overrideCell(row, entry.column, key);
  replacement.dataset.r = entry.td.dataset.r;
  replacement.dataset.c = entry.td.dataset.c;
  entry.td.replaceWith(replacement);
  cellIndex.set(cellKey(row.id, key), { ...entry, td: replacement });

  const box = replacement.querySelector('input');
  if (box) { box.focus(); box.select(); }
  sel = cellSelection(entry.r, entry.c);
  paintSelection();

  if (current === null || current === undefined || current === '') {
    // Nothing to store yet: an empty override is the signal that restores the
    // library value, so writing one now would undo what was just asked for.
    // The cell is live locally and saves itself as soon as it has a value.
    draftOverrides.add(cellKey(row.id, key));
    saveState('');
    return;
  }
  flushRow(row);
}

function clearOverride(row, key) {
  const entry = cellIndex.get(cellKey(row.id, key));
  const wasDraft = draftOverrides.delete(cellKey(row.id, key));
  const next = { ...(row.overrides || {}) };
  // An empty value is how the server is told to drop the override, which is
  // why this is not simply a delete: the row has to say so explicitly.
  next[key] = '';
  row.overrides = next;

  if (entry) {
    delete row.overrides[key];
    const replacement = renderLibraryCell(row, entry.column, key);
    replacement.dataset.r = entry.td.dataset.r;
    replacement.dataset.c = entry.td.dataset.c;
    replacement.tabIndex = -1;
    entry.td.replaceWith(replacement);
    cellIndex.set(cellKey(row.id, key), { ...entry, td: replacement });
    row.overrides = next;
  }
  if (wasDraft && next[key] === '') {
    // It was never stored, so there is nothing for the server to undo.
    delete row.overrides[key];
    return;
  }
  flushRow(row);
}

function onOverrideType(row, key, value) {
  row.overrides = { ...(row.overrides || {}), [key]: value };
  // Keep the draft marker until the value survives a save, or a redraw between
  // the first keystroke and the response would turn the cell back into text.
  saveState('saving');
  clearTimeout(pending.get(row.id));
  pending.set(row.id, setTimeout(() => flushRow(row), 500));
}

function renderEditableCell(row, column, key) {
  const td = el('td', { class: 'cell-input cell-source-typed' });
  if (key === 'Model Reference') {
    td.appendChild(modelReferenceCell(row));
  } else {
    td.appendChild(cellInput(row, key));
  }
  return td;
}

/* ---------------------------------------------------------- suggestions --- */

/** What this schedule already says in one column. */
function suggestionsFor(key) {
  return (view.grid.suggestions || {})[key] || { values: [], counts: {}, next: null };
}

/**
 * The one value in this column that starts with what has been typed.
 *
 * One, deliberately. Two candidates means completing would be a guess, and a
 * guess that lands in a schedule is worse than the four keystrokes it saved.
 * Numbers are left alone: a duty of 4 is not the start of 450.
 */
function completionFor(key, typed) {
  const text = String(typed || '');
  if (text.length < 1 || /^[\d.,-]+$/.test(text)) return null;
  const lower = text.toLowerCase();
  const matches = suggestionsFor(key).values.filter(
    (v) => v.toLowerCase().startsWith(lower) && v.length > text.length
  );
  // Values differing only in case are the same suggestion, not two.
  const distinct = [...new Set(matches.map((v) => v.toLowerCase()))];
  return distinct.length === 1 ? matches[0] : null;
}

/**
 * The reference this row would get if nobody typed one.
 *
 * Shown as a placeholder on the last empty cell of a column that counts, and
 * taken by pressing Tab or Enter — so adding a row to a schedule of MVHR-005
 * is one keystroke rather than eight.
 */
function nextValueFor(row, key) {
  const suggestion = suggestionsFor(key).next;
  if (!suggestion) return null;
  if ((row.values[key] ?? '') !== '') return null;

  // On exactly one row: the first empty one after the last that has a value.
  // Offering MVHR-006 on every empty row would be offering to make five
  // duplicates, and offering it in a gap halfway up would make one.
  const rows = view.grid.rows;
  let lastFilled = -1;
  rows.forEach((r, i) => {
    if (String(r.values[key] ?? '').trim() !== '') lastFilled = i;
  });
  const target = rows[lastFilled + 1];
  return target && target.id === row.id ? suggestion : null;
}

/**
 * Complete what is being typed, inline, the way an address bar does.
 *
 * The completion goes in as selected text, so carrying on typing replaces it
 * and Backspace removes it. Nothing is committed until the cell is left, which
 * is what makes it safe to be wrong.
 */
function applyCompletion(box, key) {
  const typed = box.value;
  if (box.selectionStart !== typed.length) return;  // mid-word editing
  const completion = completionFor(key, typed);
  if (!completion) return;
  box.value = completion;
  box.setSelectionRange(typed.length, completion.length);
}

function cellInput(row, key, attrs = {}) {
  const box = input(row.values[key] ?? '', {
    ...attrs,
    on: {
      input: (event) => {
        // Only when adding to the end: deleting must not re-complete what was
        // just deleted, which would make Backspace impossible.
        const typing = !event.inputType || !event.inputType.startsWith('delete');
        onType(row, key);
        if (typing) applyCompletion(box, key);
      },
      blur: async () => {
        await flushRow(row);
        offerGroupFill(row, key);
      },
      focus: () => focusFromCell(row.id, key),
      ...(attrs.on || {}),
    },
  });

  const suggestion = nextValueFor(row, key);
  if (suggestion) {
    box.placeholder = suggestion;
    box.dataset.suggest = suggestion;
  }
  return box;
}

/* ------------------------------------------------------- the group offer --- */

/**
 * Offers already turned down, so the same one is not made twice.
 *
 * Keyed by the column and the value. A suggestion that keeps coming back after
 * being dismissed stops being help and becomes something to click past.
 */
const declinedOffers = new Set();

/** Chips are one at a time; a second would be two things asking at once. */
let groupChip = null;

function dismissGroupChip() {
  if (groupChip) { groupChip.remove(); groupChip = null; }
}

/**
 * "The other twelve Cupboards have no airflow. Set them to 28 as well?"
 *
 * A hundred flats hold the same unit at the same duty, and typing it a hundred
 * times is why somebody builds the schedule in Excel instead. The offer is
 * deliberately narrow, because a wrong bulk edit costs far more than it saves:
 *
 * - only cells that are **empty**, never one somebody has already answered;
 * - only where **two or more** rows share the grouping value, so a one-off is
 *   not treated as a pattern;
 * - only the grouping column that matches **most** rows, and never a column
 *   whose values are unique, which self-excludes references;
 * - once. Turn it down and it stays down for that column and value.
 */
function offerGroupFill(row, key) {
  dismissGroupChip();
  const column = view.grid.columns.find((c) => c.legacy_name === key);
  if (!column || !column.editable) return;

  const value = String(row.values[key] ?? '').trim();
  if (!value) return;
  if (declinedOffers.has(`${key}=${value}`)) return;

  let best = null;
  for (const other of view.grid.columns) {
    if (!other.editable || other.legacy_name === key) continue;
    const groupValue = String(row.values[other.legacy_name] ?? '').trim();
    if (!groupValue) continue;

    const targets = view.grid.rows.filter((r) =>
      r.id !== row.id
      && String(r.values[other.legacy_name] ?? '').trim().toLowerCase()
         === groupValue.toLowerCase()
      && String(r.values[key] ?? '').trim() === ''
    );
    if (targets.length >= 2 && (!best || targets.length > best.targets.length)) {
      best = { column: other, groupValue, targets };
    }
  }
  if (!best) return;

  showGroupChip(row, key, value, best);
}

function showGroupChip(row, key, value, match) {
  const entry = cellIndex.get(cellKey(row.id, key));
  if (!entry) return;
  const column = view.grid.columns.find((c) => c.legacy_name === key);

  const apply = async () => {
    dismissGroupChip();
    const edits = match.targets.map((r) => ({ row_id: r.id, values: { [key]: value } }));
    try {
      view.grid = await api.schedules.editCells(view.id, edits, 'group_fill');
      redrawPreservingFocus();
      toast(`${edits.length} row(s) set to ${value} — Ctrl+Z to undo`, 'ok');
    } catch (error) { fail(error); }
  };

  const chip = el('div', { class: 'fill-chip group-chip' }, [
    el('span', { class: 'tiny' }, [
      `${match.targets.length} other row(s) with ${match.column.name} `,
      el('strong', { text: match.groupValue }),
      ` have no ${column.name}.`,
    ]),
    button(`Set them to ${value}`, { class: 'btn btn-sm btn-primary', on: { click: apply } }),
    el('button', {
      class: 'icon-btn',
      title: 'Not this time',
      on: {
        click: () => { declinedOffers.add(`${key}=${value}`); dismissGroupChip(); },
      },
    }, ['×']),
  ]);

  const box = entry.td.getBoundingClientRect();
  chip.style.top = `${box.bottom + 4}px`;
  chip.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - 420))}px`;
  document.body.appendChild(chip);
  groupChip = chip;
  setTimeout(() => { if (groupChip === chip) dismissGroupChip(); }, 12000);
}

/* ------------------------------------------------------------ selection --- */

/** Move the selection to whichever cell just took the caret. */
function focusFromCell(rowId, key) {
  const entry = cellIndex.get(cellKey(rowId, key));
  if (!entry) return;
  // A cell inside the selected block is the caret moving within it, not a new
  // selection: collapsing here would undo a block the moment it was typed into.
  if (sel && contains(sel, entry.r, entry.c)) {
    const caret = activeCell(sel);
    if (caret.r !== entry.r || caret.c !== entry.c) {
      sel = withActive(sel, { r: entry.r, c: entry.c });
      paintSelection();
    }
    return;
  }
  sel = cellSelection(entry.r, entry.c);
  paintSelection();
}

function cellNode(r, c) {
  const row = rowAt(r);
  const column = columnAt(c);
  if (!row || !column) return null;
  const entry = cellIndex.get(cellKey(row.id, column.legacy_name));
  return entry ? entry.td : null;
}

function paintSelection() {
  for (const { td } of cellIndex.values()) {
    td.classList.remove('sel', 'sel-focus', 'fill-preview');
  }
  document.querySelectorAll('.fill-handle').forEach((n) => n.remove());
  if (!sel) { refreshToolbar(); return; }
  const box = bounds(sel);
  for (const { r, c } of selectedCells(sel)) {
    const node = cellNode(r, c);
    if (node) node.classList.add('sel');
  }
  const caret = activeCell(sel);
  const focused = cellNode(caret.r, caret.c);
  if (focused) focused.classList.add('sel-focus');
  paintFillPreview();
  attachFillHandle(box);
  // The row numbers show the extent too, so a tall selection is obvious even
  // when the top of it has scrolled away.
  document.querySelectorAll('.sheet tbody tr').forEach((tr, index) => {
    const inRange = index >= box.top && index <= box.bottom;
    tr.classList.toggle('row-selected', inRange);
  });
  refreshToolbar();
}

/** Put the caret in a cell, or focus the cell itself when it is read-only. */
function focusCell(r, c, { select = true } = {}) {
  const node = cellNode(r, c);
  if (!node) return false;
  sel = cellSelection(r, c);
  const box = node.querySelector('input');
  if (box) {
    box.focus();
    if (select) box.select();
  } else {
    node.focus({ preventScroll: true });
  }
  node.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  paintSelection();
  return true;
}

function cellFromEvent(event) {
  const td = event.target.closest ? event.target.closest('td[data-c]') : null;
  if (!td) return null;
  return { r: Number(td.dataset.r), c: Number(td.dataset.c) };
}

function onMouseDown(event) {
  if (event.button !== 0) return;
  dismissFillChip();
  dismissGroupChip();
  const cell = cellFromEvent(event);
  if (!cell) return;

  if (event.shiftKey) {
    // Shift+click extends from wherever the selection started, as it does in a
    // spreadsheet, rather than starting a new one.
    event.preventDefault();
    sel = sel
      ? { anchor: sel.anchor, focus: cell, active: { ...activeCell(sel) } }
      : cellSelection(cell.r, cell.c);
    paintSelection();
    return;
  }

  sel = cellSelection(cell.r, cell.c);
  dragging = true;
  paintSelection();
  document.addEventListener('mouseup', endDrag, { once: true });
}

function onRowNumberDown(event, index) {
  event.preventDefault();
  const last = view.grid.columns.length - 1;
  if (event.shiftKey && sel) {
    sel = { anchor: { r: sel.anchor.r, c: 0 }, focus: { r: index, c: last } };
  } else {
    sel = selectRows(index, index, view.grid.columns.length);
  }
  dragging = true;
  paintSelection();
  document.addEventListener('mouseup', endDrag, { once: true });
}

function onMouseOver(event) {
  if (fillDrag) {
    const cell = cellFromEvent(event);
    if (!cell || cell.r === fillDrag.to) return;
    fillDrag.to = cell.r;
    paintFillPreview();
    return;
  }
  if (!dragging || !sel) return;
  const cell = cellFromEvent(event);
  if (!cell) return;
  if (cell.r === sel.focus.r && cell.c === sel.focus.c) return;  // the drag has not moved
  sel = { anchor: sel.anchor, focus: cell, active: { ...activeCell(sel) } };
  paintSelection();
}

function endDrag() {
  dragging = false;
}

/* ----------------------------------------------------------- fill handle --- */

/**
 * The small square at the corner of the selection, dragged to fill.
 *
 * This is the one spreadsheet gesture the grid was missing, and its absence is
 * why typing RAD-001 and dragging it down — the single most common thing anyone
 * does on a schedule — meant finding a button in a toolbar instead. Dragging it
 * counts up where the value ends in digits, as Excel does; holding Ctrl copies
 * instead, and the chip that appears afterwards offers the other one.
 */
function attachFillHandle(box) {
  const corner = cellNode(box.bottom, box.right);
  if (!corner) return;
  corner.appendChild(el('div', {
    class: 'fill-handle',
    title: 'Drag to fill. Counts up from a reference ending in digits; hold Ctrl to copy.',
    on: { mousedown: (event) => startFillDrag(event, box) },
  }));
}

function startFillDrag(event, box) {
  if (event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  fillDrag = { box, to: box.bottom, copy: event.ctrlKey || event.metaKey };
  dismissFillChip();
  document.addEventListener('mouseup', endFillDrag, { once: true });
}

function paintFillPreview() {
  for (const { td } of cellIndex.values()) td.classList.remove('fill-preview');
  if (!fillDrag) return;
  const { box, to } = fillDrag;
  const top = Math.min(to, box.top);
  const bottom = Math.max(to, box.bottom);
  for (let r = top; r <= bottom; r += 1) {
    if (r >= box.top && r <= box.bottom) continue;
    for (let c = box.left; c <= box.right; c += 1) {
      const node = cellNode(r, c);
      if (node) node.classList.add('fill-preview');
    }
  }
}

async function endFillDrag(event) {
  const drag = fillDrag;
  fillDrag = null;
  if (!drag) return;
  paintFillPreview();
  const copy = drag.copy || event.ctrlKey || event.metaKey;

  const down = drag.to > drag.box.bottom;
  const count = down ? drag.to - drag.box.bottom : drag.box.top - drag.to;
  if (count <= 0) { paintSelection(); return; }

  // Down fills from the bottom row of the selection, up from the top: the seed
  // is whichever end the drag started away from, as it is in a spreadsheet.
  const seedIndex = down ? drag.box.bottom : drag.box.top;
  await runFill({
    seedIndex,
    left: drag.box.left,
    right: drag.box.right,
    count,
    direction: down ? 'down' : 'up',
    mode: copy ? 'copy' : 'series',
  });
}

/** Carry out one fill, and leave the chip that offers the other mode. */
async function runFill(request) {
  const seed = rowAt(request.seedIndex);
  if (!seed) return;

  const columns = [];
  for (let c = request.left; c <= request.right; c += 1) {
    const column = columnAt(c);
    if (column && column.editable) columns.push(column.legacy_name);
  }
  if (!columns.length) {
    toast('Those columns are calculated or looked up, so they cannot be filled', 'err');
    paintSelection();
    return;
  }

  try {
    await flushRow(seed);
    view.grid = await api.schedules.fill(view.id, {
      columns,
      start_position: seed.position,
      count: request.count,
      mode: request.mode,
      direction: request.direction,
      // Null lets the server work out which number counts from the cells that
      // are already filled. A value here is somebody overruling that from the
      // chip, so it has to travel.
      index: request.index ?? null,
    });
    toast(
      request.mode === 'series'
        ? `Filled ${request.count} row(s), counting up`
        : `Copied into ${request.count} row(s)`,
      'ok'
    );
    // Extend the selection over what was just filled, as a spreadsheet does.
    const far = request.direction === 'down'
      ? request.seedIndex + request.count
      : request.seedIndex - request.count;
    sel = { anchor: { r: request.seedIndex, c: request.left }, focus: { r: far, c: request.right } };
    redrawPreservingFocus();
    showFillChip(far, request);
  } catch (error) { fail(error); }
}

let fillChip = null;

function dismissFillChip() {
  if (fillChip) { fillChip.remove(); fillChip = null; }
}

/**
 * Excel's autofill options button, in the one form that earns its place.
 *
 * "Usually a series, but give me the option" is exactly what this is: the fill
 * has already happened the way it usually should, and changing your mind is one
 * click rather than an undo and a different button.
 */
function showFillChip(atRow, request) {
  dismissFillChip();
  const anchor = cellNode(atRow, request.right);
  if (!anchor) return;

  const other = request.mode === 'series' ? 'copy' : 'series';
  const label = other === 'copy' ? 'Copy the same value instead' : 'Count up instead';

  // How many numbers the seed holds. With more than one, which of them counts
  // is a real question — 'RM0.01 2 Bedroom' could mean the room or the beds —
  // and the honest place to settle it is here, having seen what happened.
  const seedRow = rowAt(request.seedIndex);
  const seedColumn = columnAt(request.left);
  const seedValue = seedRow && seedColumn
    ? String(seedRow.values[seedColumn.legacy_name] ?? '') : '';
  const numbers = seedValue.match(/\d+/g) || [];
  const canChoose = request.mode === 'series' && numbers.length > 1;

  const chip = el('div', { class: 'fill-chip' }, [
    el('span', {
      class: 'tiny',
      text: request.mode === 'series' ? 'Counted up' : 'Copied down',
    }),
    el('button', {
      class: 'btn btn-sm',
      on: {
        click: async () => {
          dismissFillChip();
          await runFill({ ...request, mode: other });
        },
      },
    }, [label]),
    canChoose
      ? button('Count a different number', {
          class: 'btn btn-sm',
          title: `This value holds ${numbers.length} numbers: ${numbers.join(', ')}`,
          on: {
            click: async () => {
              dismissFillChip();
              // Walk the runs leftwards from the last, which is the default.
              const current = request.index ?? -1;
              const at = current < 0 ? numbers.length + current : current;
              const next = (at - 1 + numbers.length) % numbers.length;
              await runFill({ ...request, index: next });
            },
          },
        })
      : null,
  ]);

  const box = anchor.getBoundingClientRect();
  chip.style.top = `${box.bottom + 4}px`;
  chip.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - 260))}px`;
  document.body.appendChild(chip);
  fillChip = chip;
  setTimeout(() => { if (fillChip === chip) dismissFillChip(); }, 8000);
}

/* ------------------------------------------------------------- keyboard --- */

function onGridKeyDown(event) {
  if (!sel) return;
  const box = event.target.tagName === 'INPUT' ? event.target : null;

  // Tab or Enter on an empty cell takes the reference being offered. Only when
  // it is empty: a suggestion must never overwrite something somebody typed.
  if (box && (event.key === 'Tab' || event.key === 'Enter') && box.value === ''
      && box.dataset.suggest) {
    box.value = box.dataset.suggest;
    delete box.dataset.suggest;
    box.dispatchEvent(new Event('input', { bubbles: false }));
  }
  const action = decide(event, {
    editing: Boolean(box),
    atStart: box ? box.selectionStart === 0 && box.selectionEnd === 0 : true,
    atEnd: box
      ? box.selectionStart === box.value.length && box.selectionEnd === box.value.length
      : true,
    rangeSelected: !isSingleCell(sel),
  });
  if (action.type === 'none') return;

  switch (action.type) {
    case 'move': {
      event.preventDefault();
      if (!action.extend) tabAnchor = null;
      const next = move(sel, action.dr, action.dc, extent(), { extend: action.extend });
      if (!next) return;
      if (action.extend) {
        sel = next;
        paintSelection();
      } else {
        focusCell(next.focus.r, next.focus.c);
      }
      return;
    }
    case 'step': {
      event.preventDefault();
      // Tab walks a selected block too, in the same order, so Tab and Enter do
      // not disagree about what the selection is for.
      if (!isSingleCell(sel)) {
        stepInsideSelection(action.dc);
        return;
      }
      if (tabAnchor === null && action.dc > 0) tabAnchor = sel.focus.c;
      const next = stepTypeable(action.dc);
      if (next) focusCell(next.r, next.c);
      return;
    }
    case 'enter': {
      event.preventDefault();
      // With a block selected, Enter walks the block: across the row, then down
      // to the start of the next, wrapping at the end. Filling in a chosen
      // rectangle is then typing and Enter, without steering.
      if (!isSingleCell(sel)) {
        stepInsideSelection(1);
        return;
      }
      const column = tabAnchor === null ? sel.focus.c : tabAnchor;
      if (sel.focus.r + 1 < view.grid.rows.length) {
        focusCell(sel.focus.r + 1, column);
      } else {
        // On the last row Enter continues onto a new one, but only when this
        // row has something in it, so a stray Enter cannot litter empty rows.
        const row = rowAt(sel.focus.r);
        const hasContent = row && Object.values(row.values || {}).some((v) => v !== '' && v != null);
        if (hasContent) addRow(column);
      }
      return;
    }
    case 'fill-down':
      event.preventDefault();
      fillSelection(action.mode);
      return;
    case 'clear':
      event.preventDefault();
      clearSelection();
      return;
    case 'select-all':
      event.preventDefault();
      sel = {
        anchor: { r: 0, c: 0 },
        focus: { r: view.grid.rows.length - 1, c: view.grid.columns.length - 1 },
      };
      paintSelection();
      return;
    case 'undo':
      event.preventDefault();
      undoEdit();
      return;
    case 'redo':
      event.preventDefault();
      redoEdit();
      return;
    case 'escape':
      if (sel) {
        const caret = activeCell(sel);
        sel = cellSelection(caret.r, caret.c);
        paintSelection();
      }
      return;
    default:
      // 'copy' and 'paste' arrive as clipboard events too, which carry the data.
  }
}

/**
 * Move the caret one cell along inside the selected block, keeping the block.
 *
 * The block is anchor-to-focus, so moving the focus would shrink the very
 * rectangle being walked. The caret is carried separately for exactly this.
 */
function stepInsideSelection(step) {
  const next = nextInRange(sel, step);
  if (!next) return;
  const block = sel;
  focusCell(next.r, next.c, { select: true });
  sel = withActive(block, next);
  paintSelection();
}

/** Tab steps between cells somebody can type into, skipping calculated ones. */
function stepTypeable(dc) {
  const typeable = typeableIndexes();
  if (!typeable.length) return null;
  const position = typeable.indexOf(sel.focus.c);
  const scan = position >= 0 ? position : nearestTypeable(typeable, sel.focus.c);

  let index = scan + dc;
  let r = sel.focus.r;
  if (index >= typeable.length) { index = 0; r += 1; }
  if (index < 0) { index = typeable.length - 1; r -= 1; }
  if (r < 0 || r >= view.grid.rows.length) return null;
  return { r, c: typeable[index] };
}

function nearestTypeable(typeable, c) {
  let best = 0;
  typeable.forEach((index, position) => {
    if (index <= c) best = position;
  });
  return best;
}

/* ------------------------------------------------------------ clipboard --- */

function displayValue(row, column) {
  const key = column.legacy_name;
  if (column.editable) return row.values[key] ?? '';
  if (isOverridden(row, key)) return row.overrides[key] ?? '';
  return show(row.computed[key]);
}

function onCopy(event) {
  if (!sel || isSingleCell(sel)) return;  // let the browser copy inside the cell
  const matrix = selectionMatrix({
    rows: view.grid.rows,
    columns: view.grid.columns,
    bounds: bounds(sel),
    display: displayValue,
  });
  event.clipboardData.setData('text/plain', toTsv(matrix));
  event.preventDefault();
  const { rows: rowCount, columns: columnCount } = size(sel);
  toast(`${rowCount} × ${columnCount} cells copied`, 'ok');
}

function onPaste(event) {
  if (!sel) return;
  const text = event.clipboardData ? event.clipboardData.getData('text/plain') : '';
  // A plain value pasted into a cell is the browser's job. Anything with a tab
  // or a line break is a block, and belongs to the grid.
  if (!text || !/[\t\n]/.test(text)) return;
  event.preventDefault();
  applyBlockPaste(parseTsv(text));
}

/**
 * Paste a block of cells over the grid, starting at the active cell.
 *
 * Nothing is written until the counts have been shown: how many cells would be
 * overwritten, how many rows would be added, and how many cells fall on
 * calculated columns and are skipped.
 */
async function applyBlockPaste(matrix) {
  if (!matrix.length) return;
  const box = bounds(sel);
  const plan = planBlockPaste({
    matrix,
    rows: view.grid.rows,
    columns: view.grid.columns,
    top: box.top,
    left: box.left,
    // The rectangle the paste was aimed at, so a smaller block repeats to fill
    // it rather than writing one cell into a selection of eight.
    selection: box,
  });

  if (!plan.edits.length && !plan.overflow) {
    toast('Nothing in that block can be pasted here — the columns are calculated', 'err');
    return;
  }

  if (plan.overwritten || plan.overflow || plan.skipped) {
    const ok = await confirmDialog({
      title: `Paste ${plan.height} × ${plan.width} cells?`,
      message:
        `${plan.cells} cell(s) will be written` +
        (plan.repeated ? ', repeating the copied block to fill the selection' : '') +
        (plan.overwritten ? `, ${plan.overwritten} of them over values already there` : '') +
        (plan.overflow ? `, and ${plan.overflow} new row(s) added at the end` : '') +
        (plan.skipped ? `. ${plan.skipped} cell(s) fall on calculated columns and are skipped` : '') +
        '.',
      confirmLabel: 'Paste',
      danger: plan.overwritten > 0,
      detail: el('p', { class: 'muted tiny' }, ['This can be undone with Ctrl+Z.']),
    });
    if (!ok) return;
  }

  try {
    if (plan.edits.length) {
      view.grid = await api.schedules.editCells(view.id, plan.edits, 'paste');
    }
    if (plan.overflow && plan.overflowRows.length) {
      // Rows past the end are a schedule-level paste: they are new rows, and
      // the backend's planner is what decides what a new row is.
      const editable = view.grid.columns.filter((c) => c.editable);
      const offset = box.left;
      const rows = plan.overflowRows.map((line) => {
        const values = {};
        line.forEach((cell, dc) => {
          const column = view.grid.columns[offset + dc];
          if (column && column.editable && cell.trim() !== '') {
            values[column.legacy_name] = cell.trim();
          }
        });
        return { values };
      });
      view.grid = await api.schedules.paste(view.id, { mode: 'append', rows });
      void editable;
    }
    redrawPreservingFocus();
    toast(`${plan.cells} cell(s) pasted`, 'ok');
  } catch (error) { fail(error); }
}

/* ------------------------------------------------- selection operations --- */

/** Empty every cell the selection covers that somebody could have typed into. */
async function clearSelection() {
  if (!sel) return;
  const edits = [];
  let count = 0;

  for (const r of selectedRows(sel)) {
    const row = rowAt(r);
    if (!row) continue;
    const values = {};
    const overrides = {};
    for (const c of selectedColumns(sel)) {
      const column = columnAt(c);
      if (!column) continue;
      const key = column.legacy_name;
      if (column.editable) {
        if ((row.values[key] ?? '') === '') continue;
        values[key] = '';
        count += 1;
      } else if (isOverridden(row, key)) {
        overrides[key] = '';
        count += 1;
      }
    }
    if (Object.keys(values).length || Object.keys(overrides).length) {
      edits.push({
        row_id: row.id,
        values,
        ...(Object.keys(overrides).length ? { overrides } : {}),
      });
    }
  }

  if (!edits.length) return;
  try {
    view.grid = await api.schedules.editCells(view.id, edits, 'clear_cells');
    redrawPreservingFocus();
    toast(`${count} cell(s) cleared — Ctrl+Z to undo`, 'ok');
  } catch (error) { fail(error); }
}

async function deleteSelectedRows() {
  if (!sel) {
    toast('Select a row first', 'err');
    return;
  }
  const rows = selectedRows(sel).map(rowAt).filter(Boolean);
  if (!rows.length) return;
  if (rows.length === 1) { await deleteRow(rows[0]); return; }

  const populated = rows.filter((r) =>
    Object.values(r.values || {}).some((v) => v !== '' && v != null)
  ).length;
  const ok = await confirmDialog({
    title: `Delete ${rows.length} rows?`,
    message:
      populated
        ? `${populated} of them have been filled in. Everything typed on them is removed ` +
          'from the schedule.'
        : 'They are empty, so nothing typed is lost.',
    confirmLabel: `Delete ${rows.length} rows`,
    danger: populated > 0,
    detail: el('p', { class: 'muted tiny' }, ['This can be undone with Ctrl+Z.']),
  });
  if (!ok) return;

  try {
    view.grid = await api.schedules.deleteRows(view.id, rows.map((r) => r.id));
    sel = null;
    draw();
    toast(`${rows.length} rows deleted — Ctrl+Z to undo`, 'ok');
  } catch (error) { fail(error); }
}

/**
 * Fill from the top of the selection down through the rest of it.
 *
 * A selection one cell tall means "to the bottom of the schedule", which is what
 * the fill-down button meant before ranges existed. Anything taller is bounded
 * by the selection, so filling never runs away past what was asked for.
 */
async function fillSelection(mode) {
  if (!sel) {
    toast('Select a cell first, then fill down', 'err');
    return;
  }
  const box = bounds(sel);
  const height = box.bottom - box.top;
  const count = height > 0 ? height : view.grid.rows.length - box.top - 1;
  if (count <= 0) {
    toast('Nothing below this row to fill', 'err');
    return;
  }
  // The same path the corner handle takes, so the toolbar and the drag cannot
  // disagree about what a fill does — and so both leave the chip that offers
  // the other way round.
  await runFill({
    seedIndex: box.top,
    left: box.left,
    right: box.right,
    count,
    direction: 'down',
    mode,
  });
}

async function undoEdit() {
  try {
    view.grid = await api.schedules.undo(view.id);
    redrawPreservingFocus();
    toast('Undone', 'ok');
  } catch (error) {
    if (error.status === 409) toast(error.message, 'err');
    else fail(error);
  }
}

async function redoEdit() {
  try {
    view.grid = await api.schedules.redo(view.id);
    redrawPreservingFocus();
    toast('Redone', 'ok');
  } catch (error) {
    if (error.status === 409) toast(error.message, 'err');
    else fail(error);
  }
}

/* --------------------------------------------------------------- saving --- */

// Typing updates the local model immediately and schedules a save. Nothing
// re-renders here: the input the user is in must survive untouched.
function onType(row, key) {
  const cell = cellIndex.get(cellKey(row.id, key));
  const box = cell && cell.td.querySelector('input');
  if (!box) return;
  row.values[key] = box.value;
  saveState('saving');
  clearTimeout(pending.get(row.id));
  pending.set(row.id, setTimeout(() => flushRow(row), 500));
}

/**
 * Settle every debounced save before something redraws the grid.
 *
 * Typing schedules a save 500ms out. Anything that rebuilds the rows in the
 * meantime — adding a row, a fill, a paste — would otherwise race it, and the
 * loser is whatever the user typed last.
 */
async function flushPending() {
  const rows = [...pending.keys()]
    .map((id) => view.grid.rows.find((r) => r.id === id))
    .filter(Boolean);
  for (const row of rows) await flushRow(row);
}

async function flushRow(row) {
  clearTimeout(pending.get(row.id));
  pending.delete(row.id);
  try {
    absorb(await api.schedules.updateRow(view.id, row.id, row.values, row.overrides || {}));
    saveState('saved');
  } catch (error) {
    saveState('error');
    fail(error);
  }
}

/**
 * Take a fresh grid from the server and update only what changed.
 *
 * Input cells are left alone entirely, including their DOM nodes, so a save
 * landing mid-keystroke cannot steal focus or clobber what is being typed. Only
 * the server can tell us the computed values, so those are patched.
 */
function absorb(fresh) {
  const overrideKeys = (row) => Object.keys(row.overrides || {}).sort().join('|');
  const sameShape =
    fresh.rows.length === view.grid.rows.length &&
    fresh.rows.every((r, i) => r.id === view.grid.rows[i].id) &&
    // An override appearing or clearing swaps a read-only cell for an input.
    // The cell is swapped in place when the user does it, so this only fires
    // when something else changed the shape.
    fresh.rows.every((r, i) => overrideKeys(r) === overrideKeys(view.grid.rows[i]));

  view.grid.schedule = fresh.schedule;
  view.grid.history = fresh.history;
  view.grid.suggestions = fresh.suggestions;

  if (!sameShape) {
    view.grid = fresh;
    redrawPreservingFocus();
    return;
  }

  fresh.rows.forEach((freshRow, i) => {
    const local = view.grid.rows[i];
    local.computed = freshRow.computed;
    local.problems = freshRow.problems;
    local.overrides = {
      ...(freshRow.overrides || {}),
      // A draft the server has not been told about yet is still on screen.
      ...Object.fromEntries(
        Object.entries(local.overrides || {})
          .filter(([k]) => draftOverrides.has(cellKey(local.id, k)))
      ),
    };

    for (const column of view.grid.columns) {
      const key = column.legacy_name;
      const entry = cellIndex.get(cellKey(local.id, key));
      if (!entry) continue;

      if (!column.editable && !isTyping(local, key)) {
        const problem = (freshRow.problems || {})[key];
        const selected = entry.td.className.includes('sel');
        entry.td.className =
          `cell-${column.kind}${problem ? ' cell-problem' : ''}` +
          (column.kind === 'library' ? ' cell-source-library' : '');
        entry.td.title = problem || '';
        entry.td.textContent = problem ? '—' : show(freshRow.computed[key]);
        if (column.kind === 'library') {
          entry.td.appendChild(el('button', {
            class: 'cell-override',
            title: 'From the equipment library. Click to use a different value on this row only.',
            on: { click: (event) => { event.stopPropagation(); startOverride(local, key); } },
          }, ['✎']));
        }
        if (selected) entry.td.classList.add('sel');
      } else if (key === 'Model Reference') {
        updateModelReferenceFlag(local, entry.td, freshRow);
      }
    }
  });

  refreshSuggestions();
  // Repaints the selection as well as the toolbar, because a patched cell has
  // lost the classes and the fill handle that were sitting on it.
  paintSelection();
  renderProblemSummary(document.querySelector('.page-wide > div:last-child'));
}

/**
 * Move the offered reference to wherever it now belongs.
 *
 * A save patches the computed cells and deliberately leaves the inputs alone,
 * so nothing rebuilds them — which means the ghost has to be moved by hand
 * from the row that has just been filled in to the one below it.
 */
function refreshSuggestions() {
  for (const [, entry] of cellIndex) {
    const box = entry.td.querySelector('input');
    if (!box || !entry.column.editable) continue;
    const suggestion = nextValueFor(entry.row, entry.column.legacy_name);
    if (suggestion) {
      box.placeholder = suggestion;
      box.dataset.suggest = suggestion;
    } else if (box.dataset.suggest) {
      box.placeholder = '';
      delete box.dataset.suggest;
    }
  }
}

function saveState(state) {
  const node = document.getElementById('save-state');
  if (!node) return;
  node.className = `saving${state === 'error' ? ' err' : ''}`;
  node.textContent =
    state === 'saving' ? 'Saving…' : state === 'error' ? 'Not saved' : 'Saved';
  if (state === 'saved') setTimeout(() => { if (node.textContent === 'Saved') node.textContent = ''; }, 1500);
}

/**
 * Redraw, then put the keyboard back where it was.
 *
 * Every row operation rebuilds the table, and a rebuild leaves focus on nothing
 * — which silently kills every shortcut, because the grid's key handler is on
 * the table. Undo not working straight after a fill was exactly that.
 */
function redrawPreservingFocus() {
  const keep = sel ? { ...activeCell(sel) } : null;
  const rectangle = sel;
  draw();
  if (keep) {
    focusCell(keep.r, keep.c, { select: false });
    if (rectangle) { sel = clampTo(rectangle, extent()); paintSelection(); }
  }
}

/* ------------------------------------------------------- row operations --- */

async function addRow(focusColumn) {
  // Whatever is half-typed goes to the server first. Adding a row redraws the
  // grid, and a redraw that lands before the pending save rebuilds the cell
  // from a row the server has not been told about yet — which is what made a
  // reference typed just before Enter flicker and come back on the wrong line.
  await flushPending();
  try {
    const fresh = await api.schedules.addRow(view.id, {});
    view.grid = fresh;
    draw();
    const lastIndex = fresh.rows.length - 1;
    if (lastIndex >= 0) {
      const column = focusColumn !== undefined ? focusColumn : (typeableIndexes()[0] ?? 0);
      focusCell(lastIndex, column);
    }
  } catch (error) { fail(error); }
}

/**
 * Add however many rows the count beside the button asks for.
 *
 * Fifty rows one Enter at a time is how somebody ends up pasting into Excel
 * instead, so the count is a plain number box: type 50, press Enter or the
 * button, and the grid is fifty rows longer.
 */
async function addRows() {
  const box = toolbarNodes && toolbarNodes.rowCount;
  const wanted = Math.max(1, Math.min(500, Number(box ? box.value : 1) || 1));
  if (wanted === 1) { await addRow(); return; }

  await flushPending();
  try {
    view.grid = await api.schedules.addRows(view.id, wanted);
    draw();
    const first = view.grid.rows.length - wanted;
    if (first >= 0) focusCell(first, typeableIndexes()[0] ?? 0);
    toast(`${wanted} rows added — Ctrl+Z to undo`, 'ok');
  } catch (error) { fail(error); }
}

async function duplicateRow(row) {
  try {
    view.grid = await api.schedules.duplicateRow(view.id, row.id);
    draw();
    const index = view.grid.rows.findIndex((r) => r.id === row.id);
    if (index >= 0 && view.grid.rows[index + 1]) {
      focusCell(index + 1, typeableIndexes()[0] ?? 0);
    }
    toast('Row duplicated — Ctrl+Z to undo', 'ok');
  } catch (error) { fail(error); }
}

function duplicateSelected() {
  const row = (sel && rowAt(activeCell(sel).r)) || view.grid.rows[view.grid.rows.length - 1];
  if (row) duplicateRow(row);
}

async function deleteRow(row) {
  const hasContent = Object.values(row.values || {}).some((v) => v !== '' && v != null);
  if (hasContent) {
    const reference = Object.values(row.values)[0];
    const ok = await confirmDialog({
      title: 'Delete this row?',
      message: `${reference ? `"${reference}" and ` : ''}everything typed on it will be removed from the schedule.`,
      confirmLabel: 'Delete row',
      danger: true,
      detail: el('p', { class: 'muted tiny' }, ['This can be undone with Ctrl+Z.']),
    });
    if (!ok) return;
  }
  try {
    view.grid = await api.schedules.deleteRow(view.id, row.id);
    sel = null;
    draw();
  } catch (error) { fail(error); }
}

/* ------------------------------------------------- model reference cell --- */

// The one cell that reaches into the shared equipment library. Typing filters
// what the organisation already has; if it is not there, the product is captured
// once and is immediately available to every other schedule.
function modelReferenceCell(row) {
  const wrap = el('div', { class: 'ac-wrap with-flag' });
  const box = cellInput(row, 'Model Reference', {
    placeholder: 'pick or type…',
    on: {
      input: () => { onType(row, 'Model Reference'); search(box.value); },
      focus: () => {
        focusFromCell(row.id, 'Model Reference');
        search(box.value);
      },
      blur: () => { flushRow(row); setTimeout(closeList, 160); },
      keydown: (e) => {
        if (e.key === 'Escape') { closeList(); return; }
        if (list && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
          e.preventDefault();
          e.stopPropagation();
          moveHighlight(e.key === 'ArrowDown' ? 1 : -1);
          return;
        }
        if (list && e.key === 'Enter' && highlighted >= 0) {
          e.preventDefault();
          e.stopPropagation();
          const item = list.node.querySelectorAll('.ac-item')[highlighted];
          if (item) item.dispatchEvent(new MouseEvent('mousedown'));
        }
      },
    },
  });

  let list = null;
  let highlighted = -1;

  const closeList = () => { if (list) { list.close(); list = null; highlighted = -1; } };

  const moveHighlight = (delta) => {
    const items = list.node.querySelectorAll('.ac-item');
    if (!items.length) return;
    highlighted = (highlighted + delta + items.length) % items.length;
    items.forEach((n, i) => n.classList.toggle('active', i === highlighted));
    items[highlighted].scrollIntoView({ block: 'nearest' });
  };

  const search = async (query) => {
    let entries = [];
    try {
      entries = await api.library.list(view.grid.schedule.code, query);
    } catch { /* an empty library is not an error worth shouting about */ }

    closeList();
    // Anchored to the body rather than the cell: the grid scrolls inside an
    // overflow container, which would clip a dropdown on the last visible row.
    list = anchoredList(box);
    highlighted = -1;

    for (const entry of entries.slice(0, 40)) {
      const summary = Object.entries(entry.values)
        .filter(([, v]) => v !== null && v !== '')
        .slice(0, 3)
        .map(([k, v]) => `${k.replace(/\s*\([^)]*\)$/, '')}: ${v}`)
        .join(' · ');
      list.node.appendChild(el('div', {
        class: 'ac-item',
        on: {
          mousedown: (event) => {
            if (event.preventDefault) event.preventDefault();
            box.value = entry.model_reference;
            row.values['Model Reference'] = entry.model_reference;
            closeList();
            flushRow(row);
          },
        },
      }, [
        el('div', { class: 'ref', text: entry.model_reference }),
        summary ? el('div', { class: 'meta', text: summary }) : null,
      ]));
    }

    const typed = box.value.trim();
    if (typed && !entries.some((e) => e.model_reference === typed)) {
      list.node.appendChild(el('div', {
        class: 'ac-item ac-new',
        on: {
          mousedown: (event) => {
            if (event.preventDefault) event.preventDefault();
            closeList();
            captureProduct(row, typed);
          },
        },
      }, [`Add “${typed}” to the equipment library…`]));
    }

    if (list.node.childElementCount) list.reposition();
    else closeList();
  };

  wrap.appendChild(box);
  return wrap;
}

/**
 * When a reference does not resolve, put the fix on the row itself.
 *
 * A red cell tells the user something is wrong but not what to do about it, and
 * the dropdown only appears while the field has focus. This button is always
 * there once the lookup has failed.
 */
function updateModelReferenceFlag(row, td, freshRow) {
  const wrap = td.querySelector('.ac-wrap');
  if (!wrap) return;
  const existing = wrap.querySelector('.cell-flag');

  const problems = Object.values(freshRow.problems || {});
  const unresolved = problems.some((p) => p && p.includes('not in the equipment library'));

  if (!unresolved) {
    if (existing) existing.remove();
    return;
  }
  if (existing) return;

  wrap.appendChild(el('button', {
    class: 'cell-flag',
    title: 'This model reference is not in the equipment library. Click to fix.',
    on: { click: () => resolveMissingReference(row) },
  }, ['!']));
}

async function resolveMissingReference(row) {
  const reference = String(row.values['Model Reference'] || '').trim();
  const choice = await modal({
    title: `“${reference}” is not in the equipment library`,
    render: () => el('div', {}, [
      el('p', {}, [
        'Nothing in this organisation’s library matches that reference, so the ' +
        'product columns on this row cannot be filled in.',
      ]),
      el('p', { class: 'muted tiny' }, [
        'Adding it saves the product once and makes it available on every other ' +
        'schedule from then on.',
      ]),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(null) } }),
      button('Clear the reference', { on: { click: () => close('clear') } }),
      button('Add it to the library', {
        class: 'btn btn-primary', on: { click: () => close('add') },
      }),
    ],
  });

  if (choice === 'add') captureProduct(row, reference);
  else if (choice === 'clear') {
    row.values['Model Reference'] = '';
    const entry = cellIndex.get(cellKey(row.id, 'Model Reference'));
    const box = entry && entry.td.querySelector('input');
    if (box) box.value = '';
    flushRow(row);
  }
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
        'other. It is flagged for review rather than held back. Fields you do not know ' +
        'yet can be left blank and completed later from the Equipment page.',
      ]),
      findingsBox,
      el('div', { class: 'grid-3' }, columns.map((c) =>
        field(c.unit_display ? `${c.name} (${c.unit_display})` : c.name, inputs[c.legacy_name])
      )),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      // One product is a form; a manufacturer's range is a table. The library
      // screen already has the table, so this is a door to it rather than a
      // second one written here — a range entered from a schedule and a range
      // entered from the library page must not be two different experiences.
      button('Add several…', {
        title: 'The same table the Equipment page uses, for a whole range at once',
        on: { click: () => close('grid') },
      }),
      button('Save to library', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok) return;

  if (ok === 'grid') {
    const seed = {
      'Model Reference': reference,
      ...Object.fromEntries(
        Object.entries(inputs).map(([key, node]) => [key, node.value])
      ),
    };
    const saved = await productGrid(
      view.grid.schedule.code,
      columns.map((c) => ({ name: c.name, unit: c.unit })),
      { seed: [seed] }
    );
    if (!saved) return;
    row.values['Model Reference'] = reference;
    const entry = cellIndex.get(cellKey(row.id, 'Model Reference'));
    const box = entry && entry.td.querySelector('input');
    if (box) box.value = reference;
    await flushRow(row);
    toast(`${saved} product(s) saved to the library`, 'ok');
    return;
  }

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
    row.values['Model Reference'] = reference;
    const entry = cellIndex.get(cellKey(row.id, 'Model Reference'));
    const box = entry && entry.td.querySelector('input');
    if (box) box.value = reference;
    await flushRow(row);
    toast(`${reference} saved to the library`, 'ok');
  } catch (error) { fail(error); }
}

/* ------------------------------------------------------------ paste rows --- */

/**
 * Paste whole rows into the schedule.
 *
 * The summary under the box is the backend's dry run, not a guess made here:
 * the same planner produces it and then performs the paste, so the numbers
 * somebody confirms are the numbers that happen. Appending is the default
 * because it is the mode that cannot lose anything, and replacing has to be
 * confirmed on top of being chosen.
 */
async function pasteRows() {
  const area = el('textarea', {
    rows: 10,
    placeholder: 'Paste cells copied from Excel here. The first row may be a header.',
  });
  const inputColumns = view.grid.columns.filter((c) => c.editable);
  const mode = select(
    [
      ['append', 'Add to the end (safe)'],
      ['insert', 'Insert at the selected row'],
      ['replace', 'Replace every row'],
    ],
    'append'
  );
  const summary = el('div', { class: 'paste-summary' });
  let plan = null;

  const selectedIndex = sel ? bounds(sel).top : 0;

  const refresh = debounce(async () => {
    clear(summary);
    if (!area.value.trim()) return;
    try {
      plan = await api.schedules.pastePreview(view.id, {
        mode: mode.value,
        text: area.value,
        position: selectedIndex,
      });
      clear(summary).appendChild(renderPastePlan(plan));
    } catch (error) {
      clear(summary).appendChild(notice(error.message, 'error'));
    }
  }, 250);

  area.addEventListener('input', refresh);
  mode.addEventListener('change', refresh);

  const ok = await modal({
    title: 'Paste rows from Excel',
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'Columns are matched left to right against the ones you type: ',
        inputColumns.map((c) => c.name).join(', '),
        '. A first row naming the columns is recognised and used to match them instead.',
      ]),
      field('Where', mode),
      el('div', { style: 'margin-top:12px' }, [area]),
      summary,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Paste', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok || !area.value.trim()) return;

  if (!plan) {
    try {
      plan = await api.schedules.pastePreview(view.id, {
        mode: mode.value, text: area.value, position: selectedIndex,
      });
    } catch (error) { fail(error); return; }
  }
  if (!plan.detected_rows) {
    toast('Nothing to paste', 'err');
    return;
  }

  if (plan.destructive) {
    const confirmed = await confirmDialog({
      title: 'Replace every row?',
      message:
        `${plan.populated_removed} filled-in row(s) will be removed and replaced with the ` +
        `${plan.detected_rows} pasted row(s).`,
      confirmLabel: 'Replace rows',
      danger: true,
      detail: el('p', { class: 'muted tiny' }, ['This can be undone with Ctrl+Z.']),
    });
    if (!confirmed) return;
  }

  try {
    view.grid = await api.schedules.paste(view.id, {
      mode: mode.value,
      text: area.value,
      position: selectedIndex,
      confirm: true,
    });
    sel = null;
    draw();
    toast(`${plan.detected_rows} row(s) pasted — Ctrl+Z to undo`, 'ok');
  } catch (error) { fail(error); }
}

function renderPastePlan(plan) {
  const parts = [];
  if (!plan.detected_rows) {
    parts.push(notice('No data rows were found in what you pasted.', 'warn'));
    return el('div', {}, parts);
  }

  const lines = [
    `${plan.detected_rows} row(s) detected` +
      (plan.header_detected ? ', with the first line read as a header' : ''),
    plan.to_append ? `${plan.to_append} row(s) added at the end` : '',
    plan.to_insert ? `${plan.to_insert} row(s) inserted at row ${plan.position + 1}` : '',
    plan.to_remove
      ? `${plan.to_remove} existing row(s) removed, ${plan.populated_removed} of them filled in`
      : '',
    `${plan.existing_rows} row(s) now, ${plan.total_after} after`,
  ].filter(Boolean);

  parts.push(notice(lines.join(' · '), plan.destructive ? 'warn' : 'info', plan.warnings));

  const columns = (plan.columns || []).filter(Boolean);
  if (columns.length && plan.rows.length) {
    parts.push(el('div', { class: 'table-wrap', style: 'max-height:220px;overflow:auto' }, [
      el('table', {}, [
        el('thead', {}, [el('tr', {}, columns.map((c) => el('th', { class: 'tiny', text: c })))]),
        el('tbody', {}, plan.rows.slice(0, 8).map((row) =>
          el('tr', {}, columns.map((c) => el('td', { class: 'tiny', text: show(row[c]) })))
        )),
      ]),
    ]));
    if (plan.rows.length > 8) {
      parts.push(el('p', { class: 'muted tiny' }, [
        `Showing the first 8 of ${plan.rows.length}.`,
      ]));
    }
  }
  return el('div', {}, parts);
}

/* ------------------------------------------------------------ revisions --- */

function drawRevisions(root) {
  const statuses = (view.meta.status_codes || []).map(([code, desc]) => `${code} - ${desc}`);

  const rows = view.revisions.map((r) => el('tr', {}, [
    el('td', {}, [
      el('strong', { text: r.code }),
      r.is_current ? pill('current', 'green') : null,
      r.issued ? pill('issued', 'blue') : null,
    ]),
    el('td', { class: 'tiny', text: r.status || '—' }),
    el('td', { class: 'tiny nowrap', text: formatDate(r.issue_date) }),
    el('td', { class: 'tiny', text: [r.prepared_by, r.checked_by, r.approved_by].filter(Boolean).join(' / ') || '—' }),
    el('td', { class: 'tiny', text: r.description || '' }),
    el('td', { class: 'cell-actions' }, [
      el('div', { class: 'btn-row' }, [
        r.issued
          ? button('Compare', { class: 'btn btn-sm', on: { click: () => showDiff(r) } })
          : button('Issue', {
              class: 'btn btn-sm btn-primary',
              title: 'Freeze what the schedule says now, so later changes cannot alter it',
              on: { click: () => issueRevision(r) },
            }),
        r.issued
          ? button('Download', {
              class: 'btn btn-sm',
              title: 'The workbook exactly as it was issued',
              on: { click: () => download(
                `/api/schedules/${view.id}/revisions/${r.id}/export.xlsx`) },
            })
          : null,
        r.issued
          ? null
          : el('button', {
              class: 'icon-btn', title: 'Delete this revision',
              on: { click: () => deleteRevision(r) },
            }, ['×']),
      ]),
    ]),
  ]));

  root.appendChild(card(
    'Revision log',
    view.revisions.length
      ? table(['Rev', 'Status', 'Date', 'By', 'Description', ''], rows)
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

  const unissued = view.revisions.filter((r) => !r.issued).length;
  if (unissued) {
    root.appendChild(notice(
      'Issuing a revision freezes what the schedule says at that moment, including the ' +
      'values pulled from the equipment library. A correction made later cannot then ' +
      'change what an already-issued document said.',
      'info'
    ));
  }
}

async function issueRevision(revision) {
  const ok = await confirmDialog({
    title: `Issue ${revision.code}?`,
    message:
      'This records what the schedule says right now, including every value pulled from ' +
      'the equipment library. From then on the revision cannot be deleted or renumbered, ' +
      'and later corrections will not change what it said.',
    confirmLabel: `Issue ${revision.code}`,
  });
  if (!ok) return;
  try {
    view.revisions = await api.schedules.issueRevision(view.id, revision.id);
    toast(`${revision.code} issued`, 'ok');
    draw();
  } catch (error) { fail(error); }
}

async function showDiff(revision) {
  const others = view.revisions.filter((r) => r.issued && r.id !== revision.id);
  const against = select(
    [['', 'the schedule as it stands now'], ...others.map((r) => [r.id, r.code])],
    ''
  );
  const body = el('div');

  const load = async () => {
    clear(body).appendChild(el('div', { class: 'muted', text: 'Comparing…' }));
    try {
      const diff = await api.schedules.diff(view.id, revision.id, against.value);
      clear(body).appendChild(renderDiff(diff));
    } catch (error) { clear(body).appendChild(notice(error.message, 'error')); }
  };
  against.addEventListener('change', load);

  // modal() mounts synchronously but its promise resolves only when the dialog
  // closes, so the first comparison has to be kicked off before awaiting it.
  const closed = modal({
    title: `What changed since ${revision.code}`,
    wide: true,
    render: () => el('div', {}, [
      field('Compare against', against),
      el('div', { style: 'margin-top:14px' }, [body]),
    ]),
  });
  load();
  await closed;
}

function renderDiff(diff) {
  const parts = [];
  const total = diff.changed.length + diff.added.length + diff.removed.length;

  if (!total) {
    parts.push(notice(
      `Nothing has changed. ${diff.unchanged} row(s) are identical.`, 'ok'
    ));
    return el('div', {}, parts);
  }

  parts.push(notice(
    `${diff.changed.length} row(s) changed, ${diff.added.length} added, ` +
    `${diff.removed.length} removed. ${diff.unchanged} unchanged.`,
    'warn'
  ));

  if (diff.added.length) {
    parts.push(el('p', { class: 'tiny' }, [
      el('strong', { text: 'Added: ' }), diff.added.join(', '),
    ]));
  }
  if (diff.removed.length) {
    parts.push(el('p', { class: 'tiny' }, [
      el('strong', { text: 'Removed: ' }), diff.removed.join(', '),
    ]));
  }

  if (diff.changed.length) {
    parts.push(table(
      ['Row', 'Column', 'Was', 'Now'],
      diff.changed.flatMap((row) =>
        row.fields.map((f, i) => el('tr', {}, [
          el('td', { class: 'tiny', text: i === 0 ? row.reference : '' }),
          el('td', { class: 'tiny', text: f.column }),
          el('td', { class: 'tiny strike', text: show(f.before) }),
          el('td', { class: 'tiny' }, [el('strong', { text: show(f.after) })]),
        ]))
      )
    ));
  }
  return el('div', {}, parts);
}

async function deleteRevision(revision) {
  const ok = await confirmDialog({
    title: `Delete revision ${revision.code}?`,
    message:
      'It is removed from the revision log, and the cover and register will show whichever ' +
      'revision then ranks highest.',
    confirmLabel: 'Delete revision',
    danger: true,
  });
  if (!ok) return;
  try {
    view.revisions = await api.schedules.deleteRevision(view.id, revision.id);
    view.grid = await api.schedules.grid(view.id);
    draw();
  } catch (error) { fail(error); }
}

async function addRevision(statuses, published) {
  let suggested = '';
  try {
    suggested = (await api.schedules.nextRevision(view.id, published)).code;
  } catch { /* the server will pick one if we cannot */ }

  const project = view.grid.schedule;
  const code = input(suggested);
  const status = select(statuses, statuses[2] || statuses[0]);
  const date = el('input', { type: 'date', value: new Date().toISOString().slice(0, 10) });
  const description = input('');
  const prepared = input('');
  const checked = input('');
  const approved = input('');

  const ok = await modal({
    title: 'Add a revision',
    render: () => el('div', {}, [
      el('div', { class: 'grid-2' }, [
        field('Revision', code, 'P for preliminary, C for published.'),
        field('Date', date),
      ]),
      el('div', { style: 'margin-top:12px' }, [field('Suitability status', status)]),
      el('div', { class: 'grid-3', style: 'margin-top:12px' }, [
        field('Prepared by', prepared),
        field('Checked by', checked),
        field('Approved by', approved),
      ]),
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
      prepared_by: prepared.value.trim(),
      checked_by: checked.value.trim(),
      approved_by: approved.value.trim(),
      description: description.value,
    });
    view.grid = await api.schedules.grid(view.id);
    draw();
  } catch (error) { fail(error); }
}

/* ---------------------------------------------------------------- notes --- */

/**
 * The notes that print here, and where each one comes from.
 *
 * Four layers, general to specific: the practice's standing wording, what this
 * job adds, what this equipment type says, and — only when it has to — what
 * this one document says instead. The merged result is shown in printing order
 * with each line labelled, because "why does this schedule say that" is the
 * question, and an unlabelled list cannot answer it.
 */
async function drawNotes(root) {
  const box = el('div', { class: 'muted', text: 'Loading…' });
  root.appendChild(box);

  let notes;
  try {
    notes = await api.schedules.notes(view.id);
  } catch (error) { clear(box).appendChild(notice(error.message, 'error')); return; }
  clear(box);

  view.grid.notes = notes.notes;
  view.grid.notes_customised = notes.notes_customised;

  const SOURCES = {
    organisation: ['house standard', 'quiet'],
    project: ['this project', 'blue'],
    type: ['this equipment type', 'green'],
    schedule: ['this schedule', 'amber'],
  };

  const merged = el('ol', { class: 'note-list' }, notes.note_layers.map((n) => {
    const [label, tone] = SOURCES[n.source] || [n.source, 'quiet'];
    return el('li', {}, [
      el('span', { class: 'note-text', text: n.text }),
      pill(label, tone),
    ]);
  }));

  box.appendChild(card(
    'What prints on this schedule',
    el('div', {}, [
      el('p', { class: 'muted' }, [
        notes.notes_customised
          ? 'This schedule has taken its notes over, so only its own print. The layers it ' +
            'would go back to are below.'
          : 'In printing order: the practice-wide wording first, then anything this project ' +
            'adds, then wording specific to this equipment type.',
      ]),
      notes.note_layers.length
        ? merged
        : empty('No notes', 'Nothing will print in the notes block.'),
    ]),
    notes.notes_customised
      ? [
          button('Revert to the project defaults', {
            class: 'btn btn-danger',
            on: { click: () => revertNotes() },
          }),
        ]
      : [
          button('Give this schedule its own notes', {
            title: 'Starts from what it says now, so one line can be changed',
            on: { click: () => customiseNotes() },
          }),
        ]
  ));

  if (notes.notes_customised) {
    box.appendChild(scheduleNotesEditor(notes));
    box.appendChild(card(
      'What it would go back to',
      el('ol', { class: 'note-list muted' },
        notes.inherited.map((n) => el('li', {}, [
          el('span', { class: 'note-text', text: n.text }),
          pill((SOURCES[n.source] || [n.source])[0], 'quiet'),
        ]))),
      [],
      'Reverting drops this schedule\u2019s own notes and follows these again.'
    ));
    return;
  }

  box.appendChild(card(
    'Where they come from',
    el('div', { class: 'layer-grid' }, [
      layerPanel(
        'House standard',
        notes.layers.organisation,
        'Every schedule in the practice.',
        button('Edit', { class: 'btn btn-sm', on: { click: () => go('/settings') } })
      ),
      layerPanel(
        'This project',
        notes.layers.project,
        'Every schedule on this job.',
        button('Edit', {
          class: 'btn btn-sm',
          on: { click: () => go(`/projects/${view.grid.project_id}`) },
        })
      ),
      layerPanel(
        'This equipment type',
        notes.layers.type,
        `Every ${view.grid.schedule.code} schedule anywhere.`,
        button('Edit', { class: 'btn btn-sm', on: { click: () => go('/catalogue') } })
      ),
    ])
  ));
}

function layerPanel(title, items, hint, action) {
  return el('div', { class: 'layer-panel' }, [
    el('div', { class: 'layer-head' }, [
      el('strong', { text: title }),
      action || null,
    ]),
    el('div', { class: 'muted tiny', text: hint }),
    items && items.length
      ? el('ul', { class: 'note-list tiny' }, items.map((n) => el('li', { text: n })))
      : el('div', { class: 'muted tiny', style: 'margin-top:6px', text: 'None.' }),
  ]);
}

/** Editing this schedule's own notes, once it has diverged. */
function scheduleNotesEditor(notes) {
  let draft = [...notes.notes];
  const list = el('div');

  const render = () => {
    clear(list);
    draft.forEach((note, index) => {
      list.appendChild(el('div', { class: 'note-row' }, [
        el('span', { class: 'muted tiny', text: `[${index + 1}]` }),
        textarea(note, {
          rows: 2,
          on: { input: (e) => { draft[index] = e.target.value; } },
        }),
        el('button', {
          class: 'icon-btn', title: 'Remove this note',
          on: { click: () => { draft.splice(index, 1); render(); } },
        }, ['×']),
      ]));
    });
    if (!draft.length) {
      list.appendChild(el('p', { class: 'muted tiny' }, [
        'No notes. This schedule will print none at all, which is different from ' +
        'inheriting — use Revert to follow the project again.',
      ]));
    }
  };
  render();

  return card(
    'This schedule\u2019s notes',
    list,
    [
      button('+ Add note', {
        class: 'btn btn-sm',
        on: { click: () => { draft.push(''); render(); } },
      }),
      button('Save notes', {
        class: 'btn btn-primary',
        on: {
          click: async () => {
            try {
              await api.schedules.setNotes(view.id, draft.filter((n) => n.trim()));
              toast('Notes saved', 'ok');
              draw();
            } catch (error) { fail(error); }
          },
        },
      }),
    ],
    'These replace the inherited notes on this document only.'
  );
}

async function customiseNotes() {
  try {
    await api.schedules.customiseNotes(view.id);
    toast('This schedule now has its own notes', 'ok');
    draw();
  } catch (error) { fail(error); }
}

async function revertNotes() {
  const ok = await confirmDialog({
    title: 'Revert to the project defaults?',
    message:
      'This schedule\u2019s own notes are discarded and it follows the house standard, the ' +
      'project and the equipment type again.',
    confirmLabel: 'Revert',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.schedules.setNotes(view.id, null);
    toast('Reverted to the inherited notes', 'ok');
    draw();
  } catch (error) { fail(error); }
}
