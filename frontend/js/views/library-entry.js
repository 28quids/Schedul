// Getting products into the library quickly: a grid to type into, and an
// importer for a supplier's spreadsheet.
//
// The old way in was one modal per product, which is why libraries stay thin. A
// manufacturer's radiator range is forty rows that differ in three fields, so
// both of these are built around that shape: the fields that repeat are locked
// and carried down, and the ones that vary are the only ones anybody types.
//
// The importer plans on the server and shows the plan before anything is
// written. A careless column mapping can overwrite a hundred correct values in
// one click, so the dry run is the default here as it is there.

import { api } from '../api.js';
import {
  button, card, clear, confirmDialog, download, el, empty, fail, field, input,
  modal, notice, pill, select, show, table, toast,
} from '../ui.js';
import { parseTsv } from '../grid/clipboard.js';

const REFERENCE = 'Model Reference';

/** The column keys a product carries: the lookup key, then the library fields. */
function columnKeys(libColumns) {
  return [REFERENCE, ...libColumns.map((c) => (c.unit ? `${c.name} (${c.unit})` : c.name))];
}

function columnLabels(libColumns) {
  return ['Model reference', ...libColumns.map((c) => (c.unit ? `${c.name} (${c.unit})` : c.name))];
}

/* ------------------------------------------------------------ grid entry --- */

/**
 * A table of products to fill in, rather than one product at a time.
 *
 * `locked` is the batch helper: a column ticked as locked is carried into every
 * new row from the one above it. Entering a manufacturer's range means ticking
 * Manufacturer and Series, typing them once, and then typing only the sizes.
 */
export async function productGrid(code, libColumns, { seed = [] } = {}) {
  const keys = columnKeys(libColumns);
  const labels = columnLabels(libColumns);
  const locked = new Set();
  let rows = seed.length ? seed.map((r) => ({ ...r })) : [blankRow(keys)];

  const body = el('div');
  const problems = el('div');

  const blank = () => blankRow(keys);

  const render = () => {
    clear(body);
    body.appendChild(el('div', { class: 'table-wrap entry-grid' }, [
      el('table', {}, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { class: 'rowno', text: '#' }),
            ...keys.map((key, i) => el('th', {}, [
              el('div', { text: labels[i] }),
              i === 0
                ? el('div', { class: 'muted tiny', text: 'the lookup key' })
                : el('label', { class: 'tiny lock-toggle', title: 'Carry this value into every new row' }, [
                    el('input', {
                      type: 'checkbox',
                      checked: locked.has(key),
                      on: {
                        change: (e) => {
                          if (e.target.checked) locked.add(key); else locked.delete(key);
                        },
                      },
                    }),
                    'same for all',
                  ]),
            ])),
            el('th', {}),
          ]),
        ]),
        el('tbody', {}, rows.map((row, index) => el('tr', {}, [
          el('td', { class: 'rowno', text: String(index + 1) }),
          ...keys.map((key) => el('td', { class: 'cell-input' }, [
            input(show(row[key]), {
              on: { input: (e) => { row[key] = e.target.value; } },
            }),
          ])),
          el('td', { class: 'cell-actions' }, [
            el('button', {
              class: 'icon-btn', title: 'Copy this row into a new one below',
              on: { click: () => { rows.splice(index + 1, 0, { ...row }); render(); } },
            }, ['⧉']),
            el('button', {
              class: 'icon-btn', title: 'Remove this row',
              on: {
                click: () => {
                  rows.splice(index, 1);
                  if (!rows.length) rows.push(blank());
                  render();
                },
              },
            }, ['×']),
          ]),
        ]))),
      ]),
    ]));
  };
  render();

  const addRow = () => {
    const previous = rows[rows.length - 1] || {};
    const fresh = blank();
    // The locked columns are the family's: manufacturer, series, material. They
    // are what makes forty rows forty rows rather than forty products.
    for (const key of locked) fresh[key] = previous[key] ?? '';
    rows.push(fresh);
    render();
  };

  const duplicateLast = () => {
    const previous = rows[rows.length - 1];
    if (!previous) return;
    rows.push({ ...previous });
    render();
  };

  const copyDown = () => {
    const first = rows[0];
    if (!first) return;
    const chosen = [...locked];
    if (!chosen.length) {
      toast('Tick “same for all” on the columns to copy down first', 'err');
      return;
    }
    for (const row of rows.slice(1)) {
      for (const key of chosen) row[key] = first[key];
    }
    render();
    toast(`Copied ${chosen.length} column(s) into ${rows.length - 1} row(s)`, 'ok');
  };

  const pasteBlock = async () => {
    const area = el('textarea', {
      rows: 8,
      placeholder: 'Paste rows copied from Excel. Columns are matched left to right.',
    });
    const ok = await modal({
      title: 'Paste rows into the editor',
      wide: true,
      render: () => el('div', {}, [
        el('p', { class: 'muted' }, [
          'Matched left to right against: ', labels.join(', '), '.',
        ]),
        area,
      ]),
      actions: (close) => [
        button('Cancel', { on: { click: () => close(false) } }),
        button('Add rows', { class: 'btn btn-primary', on: { click: () => close(true) } }),
      ],
    });
    if (!ok || !area.value.trim()) return;

    const matrix = parseTsv(area.value);
    const added = matrix
      .filter((line) => line.some((cell) => cell.trim() !== ''))
      .map((line) => {
        const row = blank();
        line.forEach((cell, i) => { if (keys[i]) row[keys[i]] = cell.trim(); });
        return row;
      });
    // A single blank starting row is scaffolding, not data.
    if (rows.length === 1 && !filled(rows[0])) rows = [];
    rows.push(...added);
    render();
    toast(`${added.length} row(s) added — check them before saving`, 'ok');
  };

  const validate = () => {
    const found = [];
    const seen = new Map();
    rows.forEach((row, index) => {
      const reference = String(row[REFERENCE] || '').trim();
      if (!filled(row)) return;
      if (!reference) {
        found.push(`Row ${index + 1} has no model reference. It is the key every schedule row points at.`);
        return;
      }
      const key = reference.toLowerCase();
      if (seen.has(key)) {
        found.push(`Row ${index + 1} repeats the reference ${reference}, already on row ${seen.get(key)}.`);
      } else {
        seen.set(key, index + 1);
      }
    });
    return found;
  };

  const ok = await modal({
    title: `Add ${code} products`,
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'Type across the row, then down. Tick “same for all” on the fields a whole ' +
        'manufacturer range shares — they are carried into every new row, so only the ' +
        'sizes and duties have to be typed.',
      ]),
      el('div', { class: 'btn-row', style: 'margin-bottom:10px' }, [
        button('+ Row', { class: 'btn btn-sm', on: { click: addRow } }),
        button('Duplicate last', { class: 'btn btn-sm', on: { click: duplicateLast } }),
        button('Copy locked down', { class: 'btn btn-sm', on: { click: copyDown } }),
        button('Paste from Excel…', { class: 'btn btn-sm', on: { click: pasteBlock } }),
      ]),
      problems,
      body,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Save all', {
        class: 'btn btn-primary',
        on: {
          click: () => {
            clear(problems);
            const found = validate();
            if (found.length) {
              problems.appendChild(notice('Fix these before saving:', 'error', found));
              return;
            }
            close(true);
          },
        },
      }),
    ],
  });
  if (!ok) return 0;

  const payload = rows.filter(filled).map((row) => ({
    type_code: code,
    model_reference: String(row[REFERENCE] || '').trim(),
    values: Object.fromEntries(
      Object.entries(row).filter(([k, v]) => k !== REFERENCE && String(v).trim() !== '')
    ),
  }));
  if (!payload.length) return 0;

  try {
    const saved = await api.library.saveMany(code, payload);
    toast(`${saved.length} product(s) saved to the library`, 'ok');
    return saved.length;
  } catch (error) { fail(error); return 0; }
}

function blankRow(keys) {
  return Object.fromEntries(keys.map((k) => [k, '']));
}

function filled(row) {
  return Object.values(row).some((v) => String(v ?? '').trim() !== '');
}

/* --------------------------------------------------------------- import --- */

/**
 * Import a supplier's product list.
 *
 * Paste, map the columns, look at what would happen, and only then apply. The
 * plan comes from the server — the same planner that carries the import out —
 * so what is confirmed is what happens.
 */
/**
 * The columns this type takes, as a table with the headings in it.
 *
 * A list of column names in a sentence is something to read; a header row is
 * something to copy. This is the same row the blank workbook carries, so
 * pasting it into a new spreadsheet, filling in underneath and pasting the lot
 * back is a route that works without anybody being told about it.
 */
function headerTable(code, columns) {
  const headerLine = columns.join('\t');
  return el('section', { class: 'panel' }, [
    el('div', { class: 'panel-head' }, [
      el('strong', { text: `What ${code} products carry` }),
      el('div', { class: 'btn-row' }, [
        button('Copy headings', {
          class: 'btn btn-sm',
          title: 'Paste them into a blank spreadsheet and fill in underneath',
          on: {
            click: async () => {
              try {
                await navigator.clipboard.writeText(headerLine);
                toast('Headings copied — paste them into row 1 of a new sheet', 'ok');
              } catch {
                toast('Your browser would not let the page copy. Use the workbook instead.', 'err');
              }
            },
          },
        }),
        button('Blank workbook', {
          class: 'btn btn-sm',
          title: 'An .xlsx with these headings and an example row, ready to fill in',
          on: { click: () => download(api.library.workbookUrl(code, false)) },
        }),
        button(`Export ${code}`, {
          class: 'btn btn-sm',
          title: 'The same file with what is already in the library in it',
          on: { click: () => download(api.library.workbookUrl(code, true)) },
        }),
      ]),
    ]),
    el('div', { class: 'table-wrap', style: 'max-height:120px' }, [
      el('table', { class: 'headings' }, [
        el('thead', {}, [
          el('tr', {}, columns.map((c, i) => el('th', {
            class: i === 0 ? 'key' : '',
            text: c,
          }))),
        ]),
        el('tbody', {}, [
          el('tr', {}, columns.map((c, i) => el('td', {
            class: 'tiny muted',
            text: i === 0 ? 'the lookup key' : '',
          }))),
        ]),
      ]),
    ]),
    el('p', { class: 'muted tiny', style: 'margin-top:8px' }, [
      'Column A is the key: two rows with the same reference are the same product, and ' +
      'importing one that is already there updates it rather than adding a second.',
    ]),
  ]);
}

export async function importProducts(code) {
  let columns = [];
  try {
    columns = (await api.library.importColumns(code)).columns;
  } catch (error) { fail(error); return 0; }

  const area = el('textarea', {
    rows: 9,
    placeholder: 'Paste the supplier’s rows here. A first row naming the columns is recognised.',
  });
  const updateExisting = el('input', { type: 'checkbox', checked: true });
  const mappingBox = el('div', { class: 'mapping-row' });
  const summary = el('div');

  let plan = null;
  let mapping = null;

  const drawMapping = () => {
    clear(mappingBox);
    if (!plan || !plan.columns.length) return;
    mappingBox.appendChild(el('div', { class: 'muted tiny', text: 'Each pasted column goes to:' }));
    const controls = el('div', { class: 'mapping-controls' });
    plan.columns.forEach((target, index) => {
      const heading = (plan.header && plan.header[index]) || `Column ${index + 1}`;
      controls.appendChild(el('div', { class: 'mapping-item' }, [
        el('div', { class: 'tiny mono', text: heading }),
        select(
          [['', 'Ignore this column'], ...columns.map((c) => [c, c])],
          target || '',
          {
            on: {
              change: (e) => {
                mapping = plan.columns.map((c, i) => (i === index ? (e.target.value || null) : c));
                refresh();
              },
            },
          }
        ),
      ]));
    });
    mappingBox.appendChild(controls);
  };

  const refresh = async () => {
    if (!area.value.trim()) { clear(summary); clear(mappingBox); return; }
    clear(summary).appendChild(el('div', { class: 'muted', text: 'Working out what this would do…' }));
    try {
      plan = await api.library.importPreview({
        type_code: code,
        text: area.value,
        mapping,
        update_existing: updateExisting.checked,
      });
      mapping = plan.columns;
      drawMapping();
      clear(summary).appendChild(renderPlan(plan));
    } catch (error) {
      clear(summary).appendChild(notice(error.message, 'error'));
    }
  };

  let timer = null;
  area.addEventListener('input', () => {
    clearTimeout(timer);
    // A fresh paste is a fresh mapping: the columns have changed underneath it.
    mapping = null;
    timer = setTimeout(refresh, 350);
  });
  updateExisting.addEventListener('change', refresh);

  const ok = await modal({
    title: `Import ${code} products`,
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'Paste a block from a supplier’s spreadsheet, or fill in the blank workbook and ' +
        'bring it back. Nothing is written until you have seen what it would do.',
      ]),
      headerTable(code, columns),
      el('p', { class: 'muted tiny', style: 'margin:14px 0 4px' }, ['Or paste the rows here:']),
      area,
      el('label', { class: 'tiny', style: 'display:flex;gap:6px;margin-top:10px;align-items:center' }, [
        updateExisting,
        'Update products already in the library where the pasted values differ',
      ]),
      mappingBox,
      summary,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Import', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok || !plan) return 0;

  if (!plan.can_apply) {
    toast('Nothing to import — every row is already in the library or was skipped', 'err');
    return 0;
  }

  if (plan.destructive) {
    const changing = plan.rows.filter((r) => r.action === 'update').length;
    const confirmed = await confirmDialog({
      title: `Overwrite ${changing} existing product(s)?`,
      message:
        'Library values are read rather than copied, so changing a product changes every ' +
        'schedule that uses it. Only the fields the paste actually fills are touched.',
      confirmLabel: 'Import and update',
      danger: true,
    });
    if (!confirmed) return 0;
  }

  try {
    const applied = await api.library.importApply({
      type_code: code,
      text: area.value,
      mapping,
      update_existing: updateExisting.checked,
    });
    await showSummary(applied);
    return applied.applied;
  } catch (error) { fail(error); return 0; }
}

const ACTION_TONE = { create: 'green', update: 'amber', unchanged: 'quiet', skip: 'red' };

function renderPlan(plan) {
  const parts = [];
  const counts = plan.counts;

  if (plan.warnings.length) {
    parts.push(notice('Worth knowing:', 'warn', plan.warnings));
  }

  parts.push(notice(
    [
      `${counts.create} new product(s)`,
      counts.update ? `${counts.update} to update` : '',
      counts.unchanged ? `${counts.unchanged} already correct` : '',
      counts.skip ? `${counts.skip} skipped` : '',
    ].filter(Boolean).join(' · '),
    plan.destructive ? 'warn' : 'info'
  ));

  if (plan.rows.length) {
    parts.push(el('div', { class: 'table-wrap', style: 'max-height:260px;overflow:auto' }, [
      table(
        ['', 'Model reference', 'What would happen', 'Values'],
        plan.rows.slice(0, 40).map((row) => el('tr', {}, [
          el('td', {}, [pill(row.action, ACTION_TONE[row.action] || 'quiet')]),
          el('td', { class: 'mono tiny', text: row.model_reference || '—' }),
          el('td', { class: 'tiny', text: row.reason || describeChanges(row) }),
          el('td', { class: 'tiny muted', text: summarise(row.values) }),
        ]))
      ),
    ]));
    if (plan.rows.length > 40) {
      parts.push(el('p', { class: 'muted tiny' }, [
        `Showing the first 40 of ${plan.rows.length}.`,
      ]));
    }
  }
  return el('div', {}, parts);
}

function describeChanges(row) {
  if (row.action === 'create') return 'added to the library';
  if (row.action === 'update') {
    return row.changes
      .map((c) => `${c.column}: ${show(c.before)} → ${show(c.after)}`)
      .join('; ');
  }
  return '';
}

function summarise(values) {
  return Object.entries(values || {})
    .slice(0, 4)
    .map(([k, v]) => `${k.replace(/\s*\([^)]*\)$/, '')}: ${v}`)
    .join(' · ');
}

async function showSummary(applied) {
  const counts = applied.counts;
  await modal({
    title: 'Import finished',
    wide: true,
    render: () => el('div', {}, [
      notice(
        `${applied.applied} product(s) written: ${counts.create} added, ${counts.update} updated. ` +
        `${counts.unchanged} were already correct and ${counts.skip} were skipped.`,
        'ok'
      ),
      counts.skip
        ? table(
            ['Model reference', 'Why it was skipped'],
            applied.rows
              .filter((r) => r.action === 'skip')
              .map((r) => el('tr', {}, [
                el('td', { class: 'mono tiny', text: r.model_reference || '—' }),
                el('td', { class: 'tiny', text: r.reason }),
              ]))
          )
        : null,
      el('p', { class: 'muted tiny' }, [
        'Imported products are flagged for review like any other, so anything that looks ' +
        'like a duplicate or a misspelling is listed under “Needs review”.',
      ]),
    ]),
  });
}

/* ----------------------------------------------------- workbook import --- */

/**
 * Bring a filled-in workbook back, one sheet per type.
 *
 * This is the mass route the paste importer cannot be: export everything,
 * correct it in Excel where correcting a hundred rows is a drag of the fill
 * handle, and put it back. The plan comes from the server per sheet, and a tab
 * whose name does not match a type is reported rather than guessed at —
 * importing a hundred radiators into the fan coil library because somebody
 * renamed a tab is not a recoverable mistake.
 */
export async function importWorkbook() {
  const picker = el('input', { type: 'file', accept: '.xlsx,.xlsm' });
  const updateExisting = el('input', { type: 'checkbox', checked: true });
  const summary = el('div');
  let plan = null;

  const refresh = async () => {
    const file = picker.files && picker.files[0];
    if (!file) { clear(summary); plan = null; return; }
    clear(summary).appendChild(el('div', { class: 'muted', text: 'Reading the workbook…' }));
    try {
      plan = await api.library.importWorkbook(file, {
        apply: false, updateExisting: updateExisting.checked,
      });
      clear(summary).appendChild(renderWorkbookPlan(plan));
    } catch (error) {
      plan = null;
      clear(summary).appendChild(notice(error.message, 'error'));
    }
  };
  picker.addEventListener('change', refresh);
  updateExisting.addEventListener('change', refresh);

  const ok = await modal({
    title: 'Import an equipment workbook',
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'One sheet per equipment type, named after the type’s code, with the headings on ' +
        'row 1. Export the library, fill it in, and bring the same file back.',
      ]),
      el('div', { class: 'btn-row', style: 'margin-bottom:12px' }, [
        button('Blank workbook for every type', {
          class: 'btn btn-sm',
          on: { click: () => download(api.library.workbookUrl('', false)) },
        }),
        button('Export the whole library', {
          class: 'btn btn-sm',
          on: { click: () => download(api.library.workbookUrl('', true)) },
        }),
      ]),
      field('Workbook', picker),
      el('label', { class: 'tiny', style: 'display:flex;gap:6px;margin-top:10px;align-items:center' }, [
        updateExisting,
        'Update products already in the library where the workbook’s values differ',
      ]),
      summary,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Import', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!ok || !plan) return 0;

  if (!plan.can_apply) {
    toast('Nothing to import — every row is already in the library or was skipped', 'err');
    return 0;
  }
  if (plan.destructive) {
    const confirmed = await confirmDialog({
      title: `Overwrite ${plan.counts.update} existing product(s)?`,
      message:
        'Library values are read rather than copied, so changing a product changes every ' +
        'schedule that uses it. Only the fields the workbook actually fills are touched.',
      confirmLabel: 'Import and update',
      danger: true,
    });
    if (!confirmed) return 0;
  }

  try {
    const applied = await api.library.importWorkbook(picker.files[0], {
      apply: true, updateExisting: updateExisting.checked,
    });
    await modal({
      title: 'Import finished',
      wide: true,
      render: () => el('div', {}, [
        notice(
          `${applied.applied} product(s) written: ${applied.counts.create} added, ` +
          `${applied.counts.update} updated. ${applied.counts.unchanged} were already ` +
          `correct and ${applied.counts.skip} were skipped.`,
          'ok'
        ),
        renderWorkbookPlan(applied),
      ]),
    });
    return applied.applied;
  } catch (error) { fail(error); return 0; }
}

function renderWorkbookPlan(plan) {
  const parts = [];
  const unknown = plan.sheets.filter((s) => !s.recognised);
  if (unknown.length) {
    parts.push(notice(
      `${unknown.length} sheet(s) were left alone:`, 'warn',
      unknown.map((s) => s.message)
    ));
  }

  const known = plan.sheets.filter((s) => s.recognised);
  if (!known.length) {
    parts.push(notice('No sheet in that workbook matched an equipment type.', 'error'));
    return el('div', {}, parts);
  }

  parts.push(table(
    ['Sheet', 'New', 'To update', 'Already correct', 'Skipped'],
    known.map((sheet) => el('tr', {}, [
      el('td', {}, [
        el('strong', { class: 'mono', text: sheet.sheet }),
        sheet.warnings && sheet.warnings.length
          ? el('div', { class: 'muted tiny', text: sheet.warnings.join(' · ') })
          : null,
      ]),
      el('td', { class: 'tiny', text: String(sheet.counts.create) }),
      el('td', { class: 'tiny', text: String(sheet.counts.update) }),
      el('td', { class: 'tiny muted', text: String(sheet.counts.unchanged) }),
      el('td', { class: 'tiny muted', text: String(sheet.counts.skip) }),
    ]))
  ));

  const skipped = known.flatMap((s) =>
    (s.rows || []).filter((r) => r.action === 'skip' && r.reason)
      .map((r) => ({ sheet: s.sheet, ...r }))
  );
  if (skipped.length) {
    parts.push(el('details', { style: 'margin-top:10px' }, [
      el('summary', { class: 'tiny' }, [`${skipped.length} row(s) skipped — why`]),
      table(
        ['Sheet', 'Model reference', 'Why'],
        skipped.slice(0, 40).map((r) => el('tr', {}, [
          el('td', { class: 'tiny muted', text: r.sheet }),
          el('td', { class: 'mono tiny', text: r.model_reference || '—' }),
          el('td', { class: 'tiny', text: r.reason }),
        ]))
      ),
    ]));
  }
  return el('div', {}, parts);
}
