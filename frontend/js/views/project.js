// One project: its setup, its buildings and their schedules, numbering, health.
//
// The building selector is hidden entirely while there is one building, because
// most jobs have one and the layer is pure noise on those. The data model still
// has the building; only the UI collapses.

import { api } from '../api.js';
import { go, refresh, store } from '../app.js';
import {
  button, card, clear, confirmDialog, download, el, empty, fail, field, formatDate,
  input, modal, mount, notice, pill, select, show, table, toast,
} from '../ui.js';

let state = { project: null, buildingId: null, tab: 'schedules' };

export async function projectView(projectId) {
  const [project, catalogue] = await Promise.all([
    api.projects.read(projectId),
    api.catalogue.list(),
  ]);
  store.catalogue = catalogue;

  const keepBuilding =
    state.project && state.project.id === projectId &&
    project.buildings.some((b) => b.id === state.buildingId);

  state = {
    project,
    catalogue,
    buildingId: keepBuilding ? state.buildingId : (project.buildings[0] || {}).id,
    tab: state.project && state.project.id === projectId ? state.tab : 'schedules',
  };

  draw();
}

function building() {
  return state.project.buildings.find((b) => b.id === state.buildingId) || state.project.buildings[0];
}

async function reload() {
  const project = await api.projects.read(state.project.id);
  state.project = project;
  if (!project.buildings.some((b) => b.id === state.buildingId)) {
    state.buildingId = (project.buildings[0] || {}).id;
  }
  store.projects = await api.projects.list();
  draw();
}

function draw() {
  const p = state.project;
  const multi = p.buildings.length > 1;

  const page = el('div', { class: 'page page-wide' }, [
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/projects', text: 'Projects' }), ' / ', p.number || p.name || 'Project',
    ]),
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: p.name || p.number || 'Untitled project' }),
        el('div', { class: 'sub' }, [
          `${p.number || 'no number'} · ${p.client || 'no client'} · `,
          `${p.schedule_count} schedule${p.schedule_count === 1 ? '' : 's'}`,
          multi ? ` across ${p.buildings.length} buildings` : '',
        ]),
      ]),
      el('div', { class: 'btn-row' }, [
        button('Export all (.xlsx)', {
          on: { click: () => download(`/api/projects/${p.id}/export.zip?fmt=xlsx`) },
        }),
        store.pdfAvailable
          ? button('Export all (PDF)', {
              on: { click: () => download(`/api/projects/${p.id}/export.zip?fmt=pdf`) },
            })
          : null,
        button('MAINPROJECTINFO', {
          title: 'The master setup and read document, as a workbook',
          on: { click: () => download(`/api/projects/${p.id}/projectinfo.xlsx`) },
        }),
      ]),
    ]),
    tabs(),
  ]);

  const body = el('div');
  page.appendChild(body);
  mount(page);

  if (state.tab === 'schedules') drawSchedules(body);
  else if (state.tab === 'setup') drawSetup(body);
  else if (state.tab === 'numbering') drawNumbering(body);
  else drawHealth(body);
}

function tabs() {
  const items = [
    ['schedules', 'Schedules'],
    ['setup', 'Setup'],
    ['numbering', 'Numbering'],
    ['health', 'Health check'],
  ];
  return el('div', { class: 'tabs' }, items.map(([key, label]) =>
    el('button', {
      class: `tab${state.tab === key ? ' active' : ''}`,
      on: { click: () => { state.tab = key; draw(); } },
    }, [label])
  ));
}

/* ------------------------------------------------------------ schedules --- */

function drawSchedules(root) {
  const p = state.project;
  const multi = p.buildings.length > 1;
  const b = building();

  if (multi) root.appendChild(buildingBar());
  else root.appendChild(singleBuildingBar());

  if (!b) {
    root.appendChild(card('Schedules', empty('No building yet', 'Add one to start.')));
    return;
  }

  const used = new Set(b.schedules.map((s) => s.code));
  const available = state.catalogue.filter((t) => !used.has(t.code));

  const rows = b.schedules.map((s) => {
    const stale = s.type_version < s.latest_type_version;
    return el('tr', { class: 'clickable', on: { click: () => go(`/schedules/${s.id}`) } }, [
      el('td', { class: 'num mono', text: String(s.number) }),
      el('td', {}, [
        el('strong', { text: s.code }),
        el('div', { class: 'muted tiny', text: s.title }),
      ]),
      el('td', {}, [el('span', { class: 'dn', text: s.docnum || '—' })]),
      el('td', { class: 'num', text: s.row_count ? String(s.row_count) : '—' }),
      el('td', {}, [
        s.revision ? pill(s.revision, s.revision.startsWith('C') ? 'green' : 'blue') : el('span', { class: 'muted', text: '—' }),
      ]),
      el('td', { class: 'tiny muted nowrap', text: formatDate(s.issue_date) }),
      el('td', {}, [s.status ? pill(s.status, s.status === 'S0' ? 'quiet' : 'blue') : '']),
      el('td', {}, [
        s.locked ? pill('issued', 'amber') : null,
        stale ? pill(`type v${s.latest_type_version}`, 'amber') : null,
      ]),
      el('td', { class: 'cell-actions', on: { click: (e) => e.stopPropagation() } }, [
        el('div', { class: 'btn-row' }, [
          button('xlsx', {
            class: 'btn btn-sm',
            on: { click: () => download(`/api/schedules/${s.id}/export.xlsx`) },
          }),
          store.pdfAvailable
            ? button('PDF', {
                class: 'btn btn-sm',
                on: { click: () => download(`/api/schedules/${s.id}/export.pdf`) },
              })
            : null,
          button('Remove', {
            class: 'btn btn-sm btn-danger',
            on: { click: () => removeSchedule(s) },
          }),
        ]),
      ]),
    ]);
  });

  root.appendChild(el('section', { class: 'card' }, [
    el('header', { class: 'card-head' }, [
      el('div', {}, [
        el('h2', { text: multi ? `Schedules in ${b.ref}` : 'Schedules' }),
        el('div', { class: 'hint', text: 'Numbering restarts in each building, so blocks stay independent.' }),
      ]),
      el('div', { class: 'btn-row' }, [
        available.length
          ? select(
              [['', 'Add a schedule…'], ...available.map((t) => [t.code, `${t.code} — ${t.title}`])],
              '',
              { on: { change: (e) => { if (e.target.value) addSchedule(e.target.value); } } }
            )
          : el('span', { class: 'muted tiny', text: 'Every catalogue type is already here' }),
      ]),
    ]),
    el('div', { class: 'card-body tight' }, [
      b.schedules.length
        ? table(
            [
              { text: 'No.', class: 'num' }, 'Type', 'Document number',
              { text: 'Rows', class: 'num' }, 'Rev', 'Issued', 'Status', '', '',
            ],
            rows
          )
        : empty(
            'No schedules in this building yet',
            'Pick a type above. It gets the next number in this building and is ready to fill in.'
          ),
    ]),
  ]));

  if (b.retired_numbers.length) {
    root.appendChild(notice(
      `Retired numbers in ${b.ref}: ${b.retired_numbers.join(', ')}. ` +
      'These are never reallocated, so a number that has been issued keeps its meaning.',
      'info'
    ));
  }
}

function singleBuildingBar() {
  return el('div', { class: 'btn-row', style: 'margin-bottom:14px' }, [
    button('Add a building', {
      title: 'Splits this project into blocks, each with its own schedules and numbering',
      on: { click: () => addBuilding() },
    }),
    el('span', {
      class: 'muted tiny',
      text: 'This job has one building, so the building layer is hidden.',
    }),
  ]);
}

function buildingBar() {
  const p = state.project;
  const b = building();
  return el('div', { class: 'card' }, [
    el('div', { class: 'card-body', style: 'display:flex;gap:12px;flex-wrap:wrap;align-items:center' }, [
      el('div', { class: 'seg' }, p.buildings.map((item) =>
        el('button', {
          class: item.id === state.buildingId ? 'active' : '',
          on: { click: () => { state.buildingId = item.id; draw(); } },
        }, [item.ref])
      )),
      el('span', { class: 'muted tiny', text: b ? b.name || '' : '' }),
      el('div', { style: 'margin-left:auto' }, [
        el('div', { class: 'btn-row' }, [
          button('Add building', { class: 'btn btn-sm', on: { click: () => addBuilding() } }),
          button('Clone this building', { class: 'btn btn-sm', on: { click: () => cloneBuilding() } }),
          button('Rename ref', { class: 'btn btn-sm', on: { click: () => renameBuilding() } }),
          button('Delete building', {
            class: 'btn btn-sm btn-danger',
            on: { click: () => deleteBuilding() },
          }),
        ]),
      ]),
    ]),
  ]);
}

async function addSchedule(code) {
  try {
    await api.projects.addSchedule(state.project.id, state.buildingId, code);
    toast(`${code} added`, 'ok');
    await reload();
  } catch (error) { fail(error); }
}

async function removeSchedule(schedule) {
  const ok = await confirmDialog({
    title: `Remove ${schedule.code}?`,
    message:
      `Number ${schedule.number} is retired and will not be reused. The rows you have ` +
      'typed are kept, and the schedule can be restored.',
    confirmLabel: 'Remove from project',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.projects.archiveSchedule(state.project.id, schedule.id);
    toast(`${schedule.code} removed`, 'ok');
    await reload();
  } catch (error) { fail(error); }
}

async function addBuilding() {
  const ref = input('', { placeholder: 'HQ014' });
  const name = input('', { placeholder: 'East Wing' });
  const result = await modal({
    title: 'Add a building',
    render: () => el('div', {}, [
      el('div', { class: 'grid-2' }, [
        field('Building reference', ref, 'The code from the client or asset register, e.g. HQ049 or NB17.'),
        field('Name', name),
      ]),
      el('p', { class: 'help muted', style: 'margin-top:12px' }, [
        'It starts empty. Numbering restarts at the first number, so this block does not ' +
        'depend on what the others did.',
      ]),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Add building', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });
  if (!result || !ref.value.trim()) return;
  try {
    const updated = await api.projects.addBuilding(state.project.id, {
      ref: ref.value.trim(), name: name.value.trim(),
    });
    // Switch to what was just created: staying on the previous building looks
    // like nothing happened, and the next thing anyone does is fill this one in.
    const created = updated.buildings.find((b) => b.ref === ref.value.trim());
    if (created) state.buildingId = created.id;
    await reload();
  } catch (error) { fail(error); }
}

async function cloneBuilding() {
  const source = building();
  const { codes } = await api.projects.cloneCandidates(state.project.id, source.id);
  const chosen = new Set(codes);

  const ref = input('', { placeholder: 'NB17' });
  const name = input('', { placeholder: 'New Block' });

  const chipFor = (code, on) =>
    el('button', {
      class: `chip${on ? ' on' : ''}`,
      on: {
        click: (event) => {
          if (chosen.has(code)) chosen.delete(code); else chosen.add(code);
          event.currentTarget.classList.toggle('on', chosen.has(code));
        },
      },
    }, [code]);

  const ok = await modal({
    title: `Clone ${source.ref}`,
    wide: true,
    render: () => el('div', {}, [
      el('div', { class: 'grid-2' }, [
        field('New building reference', ref),
        field('Name', name),
      ]),
      el('p', { class: 'help muted', style: 'margin:14px 0 6px' }, [
        `${source.ref}'s types are ticked. Untick what this block does not have, and tick ` +
        'anything extra it does. Only the selection is copied — never the rows you have typed.',
      ]),
      el('div', { class: 'chips' }, [
        ...codes.map((c) => chipFor(c, true)),
        ...state.catalogue
          .filter((t) => !codes.includes(t.code))
          .map((t) => chipFor(t.code, false)),
      ]),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Create building', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });

  if (!ok || !ref.value.trim()) return;
  try {
    const updated = await api.projects.cloneBuilding(state.project.id, source.id, {
      ref: ref.value.trim(),
      name: name.value.trim(),
      codes: [...chosen],
    });
    const created = updated.buildings.find((b) => b.ref === ref.value.trim());
    if (created) state.buildingId = created.id;
    toast('Building cloned', 'ok');
    await reload();
  } catch (error) { fail(error); }
}

async function renameBuilding() {
  const b = building();
  const ref = input(b.ref);
  const planBox = el('div');

  const preview = async () => {
    clear(planBox);
    if (!ref.value.trim() || ref.value.trim() === b.ref) return;
    try {
      const plan = await api.projects.renameBuilding(state.project.id, b.id, {
        ref: ref.value.trim(),
      });
      planBox.appendChild(renderPlan(plan, { showNumbers: false }));
    } catch (error) { fail(error); }
  };

  const ok = await modal({
    title: `Rename ${b.ref}`,
    wide: true,
    render: () => el('div', {}, [
      field('New building reference', ref, 'Every document number in this building changes. Nothing else is touched.'),
      button('Preview the change', { style: 'margin-top:10px', on: { click: preview } }),
      planBox,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Apply rename', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });

  if (!ok || !ref.value.trim() || ref.value.trim() === b.ref) return;
  try {
    await api.projects.renameBuilding(state.project.id, b.id, { ref: ref.value.trim(), apply: true });
    toast('Building renamed', 'ok');
    await reload();
  } catch (error) {
    if (error.status === 409) {
      const force = await confirmDialog({
        title: 'Some schedules have been issued',
        message: `${error.message} Renaming changes the identity of a document already sent out.`,
        confirmLabel: 'Rename anyway',
        danger: true,
      });
      if (!force) return;
      try {
        await api.projects.renameBuilding(state.project.id, b.id, {
          ref: ref.value.trim(), apply: true, force: true,
        });
        await reload();
      } catch (inner) { fail(inner); }
    } else {
      fail(error);
    }
  }
}

async function deleteBuilding() {
  const b = building();
  const ok = await confirmDialog({
    title: `Delete ${b.ref}?`,
    message:
      `Its ${b.schedules.length} schedule(s) are removed from the project. The rows you have ` +
      'typed are kept and nothing on disk is deleted.',
    confirmLabel: 'Delete building',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.projects.deleteBuilding(state.project.id, b.id);
    await reload();
  } catch (error) { fail(error); }
}

/* ---------------------------------------------------------------- setup --- */

function drawSetup(root) {
  const p = state.project;
  const fields = {
    number: input(p.number), name: input(p.name), client: input(p.client),
    site_address: input(p.site_address), architect: input(p.architect),
    main_contractor: input(p.main_contractor),
    riba_stage: select(
      ['Stage 1', 'Stage 2', 'Stage 3', 'Stage 4', 'Stage 5', 'Stage 6', 'Stage 7'],
      p.riba_stage
    ),
    prepared_by: input(p.prepared_by), checked_by: input(p.checked_by),
    approved_by: input(p.approved_by),
  };

  const save = async () => {
    try {
      await api.projects.update(p.id, {
        ...Object.fromEntries(Object.entries(fields).map(([k, n]) => [k, n.value])),
        naming_overrides: p.naming_overrides,
        design_constants: p.design_constants,
      });
      toast('Saved', 'ok');
      await reload();
    } catch (error) { fail(error); }
  };

  root.appendChild(card(
    'Project details',
    el('div', {}, [
      el('div', { class: 'grid-3' }, [
        field('Project number', fields.number),
        field('Project name', fields.name),
        field('Client', fields.client),
      ]),
      el('div', { class: 'grid-3', style: 'margin-top:12px' }, [
        field('Site address', fields.site_address),
        field('Architect', fields.architect),
        field('Main contractor', fields.main_contractor),
      ]),
      el('div', { class: 'grid-4', style: 'margin-top:12px' }, [
        field('RIBA stage', fields.riba_stage),
        field('Prepared by', fields.prepared_by),
        field('Checked by', fields.checked_by),
        field('Approved by', fields.approved_by),
      ]),
    ]),
    [button('Save', { class: 'btn btn-primary', on: { click: save } })],
    'Every schedule in this project reads these. Change one here and it changes everywhere.'
  ));

  // Design constants: the seven values derived formulas reference.
  const constants = { ...p.effective_constants };
  const constantInputs = {};
  const constantRows = Object.entries(constants).map(([name, value]) => {
    const overridden = Object.prototype.hasOwnProperty.call(p.design_constants || {}, name);
    const node = input(String(value), { type: 'number', step: 'any' });
    constantInputs[name] = node;
    return el('tr', {}, [
      el('td', { text: name }),
      el('td', { style: 'width:140px' }, [node]),
      el('td', {}, [overridden ? pill('project', 'blue') : el('span', { class: 'muted tiny', text: 'house standard' })]),
    ]);
  });

  const saveConstants = async () => {
    const overrides = {};
    for (const [name, node] of Object.entries(constantInputs)) {
      const value = parseFloat(node.value);
      if (!Number.isNaN(value)) overrides[name] = value;
    }
    try {
      await api.projects.update(p.id, {
        number: p.number, name: p.name, client: p.client,
        site_address: p.site_address, architect: p.architect,
        main_contractor: p.main_contractor, riba_stage: p.riba_stage,
        prepared_by: p.prepared_by, checked_by: p.checked_by, approved_by: p.approved_by,
        naming_overrides: p.naming_overrides,
        design_constants: overrides,
      });
      toast('Design constants saved', 'ok');
      await reload();
    } catch (error) { fail(error); }
  };

  root.appendChild(card(
    'Design constants',
    table(['Constant', 'Value', 'Source'], constantRows),
    [button('Save constants', { class: 'btn btn-primary', on: { click: saveConstants } })],
    'Derived columns calculate from these. Overriding one here affects this project only.'
  ));

  const preview = p.naming_preview || {};
  root.appendChild(card(
    'Document numbering',
    el('div', {}, [
      preview.error ? notice(preview.error, 'warn') : null,
      el('dl', { class: 'kv', style: 'margin-bottom:14px' }, [
        el('dt', { text: 'Next number' }),
        el('dd', { text: preview.document_number || '—' }),
        el('dt', { text: 'Filename' }),
        el('dd', { text: preview.filename || '—' }),
      ]),
      table(
        ['Token', 'Value', 'Scope', 'Comes from'],
        (preview.tokens || []).map((t) =>
          el('tr', {}, [
            el('td', { class: 'mono', text: t.name }),
            el('td', { class: 'mono', text: t.value || '—' }),
            el('td', {}, [pill(t.scope, t.scope === 'type' ? 'green' : 'quiet')]),
            el('td', { class: 'muted tiny', text: t.source }),
          ])
        )
      ),
    ]),
    [button('Edit the pattern', { on: { click: () => go('/settings') } })],
    'Volume follows the equipment type, so an AHU is always ventilation without anyone setting it.'
  ));
}

/* ------------------------------------------------------------ numbering --- */

function drawNumbering(root) {
  const b = building();
  if (!b || !b.schedules.length) {
    root.appendChild(card('Numbering', empty('Nothing to renumber', 'Add a schedule first.')));
    return;
  }

  const codes = b.schedules.map((s) => s.code);
  const opSelect = select(
    [['set', 'Set a number'], ['swap', 'Swap two'], ['insert', 'Insert at'],
     ['compact', 'Close gaps'], ['rebase', 'Renumber from']],
    'set'
  );
  const codeSelect = select(codes, codes[0]);
  const otherSelect = select(codes, codes[1] || codes[0]);
  const numberInput = input('', { type: 'number', placeholder: '10' });
  const planBox = el('div', { style: 'margin-top:14px' });

  const sync = () => {
    const op = opSelect.value;
    codeSelect.parentElement.style.display = ['set', 'swap', 'insert'].includes(op) ? '' : 'none';
    otherSelect.parentElement.style.display = op === 'swap' ? '' : 'none';
    numberInput.parentElement.style.display = ['set', 'insert', 'rebase'].includes(op) ? '' : 'none';
  };
  opSelect.addEventListener('change', sync);

  const body = {
    get value() {
      return {
        operation: opSelect.value,
        code: codeSelect.value,
        other_code: otherSelect.value,
        number: numberInput.value ? parseInt(numberInput.value, 10) : null,
      };
    },
  };

  const preview = async () => {
    clear(planBox);
    try {
      const plan = await api.projects.renumber(state.project.id, b.id, body.value);
      planBox.appendChild(renderPlan(plan, {
        onApply: plan.can_apply ? () => applyPlan(body.value) : null,
        onOverride: plan.blocked_count
          ? () => overrideLocked(body.value, plan)
          : null,
      }));
    } catch (error) { fail(error); }
  };

  const applyPlan = async (payload, allowLocked = []) => {
    try {
      const result = await api.projects.renumber(state.project.id, b.id, {
        ...payload, apply: true, allow_locked: allowLocked,
      });
      toast(`${result.applied} schedule(s) renumbered`, 'ok');
      await reload();
    } catch (error) { fail(error); }
  };

  const overrideLocked = async (payload, plan) => {
    const blocked = plan.changes.filter((c) => c.blocked);
    const target = blocked[0];
    const typed = input('', { placeholder: target.old_filename || target.old_docnum });
    const ok = await modal({
      title: 'This schedule has been issued',
      render: () => el('div', {}, [
        el('p', {}, [
          'An issued reference is meant to stay stable, so renumbering is refused by default. ',
          'To override, type the filename exactly:',
        ]),
        el('p', { class: 'dn', text: target.old_filename || target.old_docnum }),
        field('Filename', typed),
      ]),
      actions: (close) => [
        button('Cancel', { on: { click: () => close(false) } }),
        button('Override and renumber', {
          class: 'btn btn-danger', on: { click: () => close(true) },
        }),
      ],
    });
    if (!ok) return;
    const expected = target.old_filename || target.old_docnum;
    if (typed.value.trim() !== expected) {
      fail(new Error('The filename did not match, so nothing was changed.'));
      return;
    }
    await applyPlan(payload, blocked.map((c) => c.code));
  };

  root.appendChild(card(
    `Renumber ${state.project.buildings.length > 1 ? b.ref : 'schedules'}`,
    el('div', {}, [
      el('div', { class: 'grid-4' }, [
        field('Operation', opSelect),
        field('Schedule', codeSelect),
        field('With', otherSelect),
        field('Number', numberInput),
      ]),
      el('p', { class: 'help muted', style: 'margin-top:10px' }, [
        'Typing numbers freehand causes collisions immediately, so these operations are the ' +
        'whole interface. Nothing changes until you apply the plan.',
      ]),
      planBox,
    ]),
    [button('Preview', { class: 'btn btn-primary', on: { click: preview } })]
  ));

  sync();

  root.appendChild(card(
    'Current numbering',
    table(
      [{ text: 'No.', class: 'num' }, 'Type', 'Document number', 'Volume', 'Status', ''],
      b.schedules.map((s) => el('tr', {}, [
        el('td', { class: 'num mono', text: String(s.number) }),
        el('td', { text: s.code }),
        el('td', {}, [el('span', { class: 'dn', text: s.docnum })]),
        el('td', { class: 'mono tiny', text: s.volume || '—' }),
        el('td', {}, [s.status ? pill(s.status, 'quiet') : '']),
        el('td', {}, [s.locked ? pill('locked', 'amber') : '']),
      ]))
    )
  ));
}

export function renderPlan(plan, { onApply, onOverride, showNumbers = true } = {}) {
  const changed = plan.changes.filter((c) => c.changed || c.old_docnum !== c.new_docnum);

  if (!changed.length) {
    return notice('This would change nothing.', 'info');
  }

  const rows = changed.map((c) => el('tr', {}, [
    el('td', { text: c.code }),
    showNumbers
      ? el('td', { class: 'num' }, [
          el('span', { class: 'strike', text: String(c.old_number) }),
          el('span', { class: 'arrow', text: '→' }),
          el('strong', { text: String(c.new_number) }),
        ])
      : null,
    el('td', {}, [
      el('div', { class: 'dn strike', text: c.old_docnum || '—' }),
      el('div', { class: 'dn', text: c.new_docnum || '—' }),
    ]),
    el('td', {}, [c.blocked ? pill('blocked', 'red') : pill('ready', 'green')]),
    el('td', { class: 'tiny muted', text: c.blocked || '' }),
  ].filter(Boolean)));

  return el('div', {}, [
    plan.blocked_count
      ? notice(
          `${plan.blocked_count} of ${changed.length} cannot be changed. Nothing will be ` +
          'applied until every row is clear.',
          'warn'
        )
      : notice(`${changed.length} document(s) would change identity.`, 'info'),
    table(
      ['Type', showNumbers ? { text: 'Number', class: 'num' } : null, 'Document number', '', 'Reason']
        .filter(Boolean),
      rows
    ),
    onApply || onOverride
      ? el('div', { class: 'btn-row', style: 'margin-top:12px' }, [
          onApply ? button('Apply', { class: 'btn btn-primary', on: { click: onApply } }) : null,
          onOverride ? button('Override the lock…', { class: 'btn btn-danger', on: { click: onOverride } }) : null,
        ])
      : null,
  ]);
}

/* --------------------------------------------------------------- health --- */

async function drawHealth(root) {
  const p = state.project;
  const box = el('div');
  root.appendChild(box);

  for (const b of p.buildings) {
    let result;
    try {
      result = await api.projects.audit(p.id, b.id);
    } catch (error) { fail(error); continue; }

    const issues = result.issues || [];
    box.appendChild(card(
      p.buildings.length > 1 ? `${b.ref} — ${b.schedules.length} schedule(s)` : 'Health check',
      issues.length
        ? table(
            ['', 'Type', 'Problem'],
            issues.map((i) => el('tr', {}, [
              el('td', {}, [pill(i.severity, i.severity === 'error' ? 'red' : 'amber')]),
              el('td', { text: i.code || '—' }),
              el('td', { text: i.message }),
            ]))
          )
        : notice(
            'Clean. Every number is unique, no retired number is in use, and every stored ' +
            'document number agrees with the tokens.',
            'ok'
          )
    ));
  }

  const archived = await api.projects.archived(p.id).catch(() => []);
  if (archived.length) {
    box.appendChild(card(
      'Removed schedules',
      table(
        ['Type', 'Was number', 'Rows kept', ''],
        archived.map((s) => el('tr', {}, [
          el('td', { text: s.code }),
          el('td', { class: 'num', text: String(s.number) }),
          el('td', { class: 'num', text: String(s.row_count) }),
          el('td', {}, [
            button('Restore', {
              class: 'btn btn-sm',
              on: {
                click: async () => {
                  try {
                    await api.projects.restoreSchedule(p.id, s.id);
                    toast('Restored', 'ok');
                    await reload();
                  } catch (error) { fail(error); }
                },
              },
            }),
          ]),
        ]))
      ),
      [],
      'Removing a schedule never destroys what you typed.'
    ));
  }
}
