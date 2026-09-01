// One project: its setup, its buildings and their schedules, numbering, health.
//
// The building selector is hidden entirely while there is one building, because
// most jobs have one and the layer is pure noise on those. The data model still
// has the building; only the UI collapses.

import { api } from '../api.js';
import { go, refresh, setContext, store } from '../app.js';
import {
  button, card, clear, confirmDialog, download, el, empty, fail, field, formatDate,
  input, modal, mount, notice, pageHead, pill, select, show, table, toast,
} from '../ui.js';

let state = { project: null, buildingId: null, tab: 'schedules', query: '' };

export async function projectView(projectId) {
  const [project, catalogue, meta] = await Promise.all([
    api.projects.read(projectId),
    api.catalogue.list(),
    api.catalogue.meta().catch(() => ({ status_codes: [] })),
  ]);
  store.catalogue = catalogue;
  const statuses = (meta.status_codes || []).map(([c, d]) => `${c} - ${d}`);

  const keepBuilding =
    state.project && state.project.id === projectId &&
    project.buildings.some((b) => b.id === state.buildingId);

  const sameProject = state.project && state.project.id === projectId;
  state = {
    project,
    catalogue,
    statuses,
    buildingId: keepBuilding ? state.buildingId : (project.buildings[0] || {}).id,
    tab: sameProject ? state.tab : 'schedules',
    query: sameProject ? state.query : '',
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
  const current = building();

  setContext({
    projectId: p.id,
    projectName: p.number || p.name || 'Project',
    building: multi && current ? current.label : (current ? current.name : ''),
    schedules: (current ? current.schedules : []).map((s) => ({
      id: s.id, code: s.code, title: s.title,
    })),
  });

  const page = el('div', { class: 'page page-wide' }, [
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/projects', text: 'Projects' }), ' / ', p.number || p.name || 'Project',
    ]),
    pageHead(
      p.name || p.number || 'Untitled project',
      `${p.number || 'no number'} · ${p.client || 'no client'} · ` +
        `${p.schedule_count} schedule${p.schedule_count === 1 ? '' : 's'}` +
        (multi ? ` across ${p.buildings.length} buildings` : ''),
      [
        button('MAINPROJECTINFO', {
          title: 'The master setup and read document, as a workbook',
          on: { click: () => download(`/api/projects/${p.id}/projectinfo.xlsx`) },
        }),
        store.pdfAvailable
          ? button('Export all (PDF)', {
              on: { click: () => download(`/api/projects/${p.id}/export.zip?fmt=pdf`) },
            })
          : null,
        button('Export all (.xlsx)', {
          title: 'Every schedule as an issued document, in one zip',
          on: { click: () => download(`/api/projects/${p.id}/export.zip?fmt=xlsx`) },
        }),
        // The one thing this screen is for once a project is under way.
        button('Issue a revision…', {
          class: 'btn btn-primary',
          title: 'Append the next revision across several schedules at once',
          on: { click: () => bulkRevision() },
        }),
      ]
    ),
    tabs(),
  ]);

  const body = el('div');
  page.appendChild(body);
  mount(page);

  if (state.tab === 'schedules') drawSchedules(body);
  else if (state.tab === 'rooms') drawRooms(body);
  else if (state.tab === 'setup') drawSetup(body);
  else if (state.tab === 'numbering') drawNumbering(body);
  else drawHealth(body);
}

function tabs() {
  const items = [
    ['schedules', 'Schedules'],
    ['rooms', 'Rooms'],
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

  // Search here for the same reason the register has it: a job with twenty
  // schedules across four blocks is a list nobody reads, they scan it.
  const matches = (s) => {
    if (!state.query) return true;
    const haystack = [
      s.code, s.title, s.docnum, s.filename, s.revision, s.status,
      s.status_description, String(s.number),
    ].join(' ').toLowerCase();
    return state.query.toLowerCase().split(/\s+/).filter(Boolean)
      .every((term) => haystack.includes(term));
  };
  const visible = b.schedules.filter(matches);

  const rows = visible.map((s) => {
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
        b.schedules.length > 3 ? scheduleSearch() : null,
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
      !b.schedules.length
        ? empty(
            'No schedules in this building yet',
            'Pick a type above. It gets the next number in this building and is ready to fill in.'
          )
        : visible.length
          ? table(
              [
                { text: 'No.', class: 'num' }, 'Type', 'Document number',
                { text: 'Rows', class: 'num' }, 'Rev', 'Issued', 'Status', '', '',
              ],
              rows
            )
          : empty(
              'Nothing matches',
              `None of the ${b.schedules.length} schedules here match “${state.query}”.`
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

/** The project page's filter. Redraws the list without rebuilding the page. */
function scheduleSearch() {
  const box = input(state.query, {
    placeholder: 'Filter schedules…',
    style: 'min-width:200px',
  });
  box.addEventListener('input', () => {
    state.query = box.value.trim();
    clearTimeout(box._timer);
    box._timer = setTimeout(() => {
      draw();
      const fresh = document.querySelector('.sheet-search input, .card-head input[type=text]');
      if (fresh) { fresh.focus(); fresh.setSelectionRange(fresh.value.length, fresh.value.length); }
    }, 200);
  });
  return box;
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

/* ------------------------------------------------------- bulk revision --- */

// Bumping every schedule on a job by hand means opening each one. Each schedule
// still continues its own series, because two schedules are rarely at the same
// revision and forcing them level would misstate history.
async function bulkRevision() {
  const p = state.project;
  const all = p.buildings.flatMap((b) =>
    b.schedules.map((s) => ({ ...s, building: b.ref, buildings: p.buildings.length }))
  );
  if (!all.length) {
    fail(new Error('This project has no schedules yet.'));
    return;
  }

  const chosen = new Set(all.map((s) => s.id));
  const status = select(
    (state.statuses || ['S2 - Suitable for Information']),
    'S2 - Suitable for Information'
  );
  const date = el('input', { type: 'date', value: new Date().toISOString().slice(0, 10) });
  const description = input('', { placeholder: 'Stage 4 issue' });
  const published = el('input', { type: 'checkbox' });
  const freeze = el('input', { type: 'checkbox', checked: true });
  const planBox = el('div', { style: 'margin-top:14px' });

  const payload = () => ({
    schedule_ids: [...chosen],
    status: status.value,
    issue_date: date.value || null,
    description: description.value,
    published: published.checked,
    issue: freeze.checked,
  });

  const preview = async () => {
    clear(planBox);
    if (!chosen.size) return;
    try {
      const result = await api.projects.bulkRevision(p.id, payload());
      planBox.appendChild(table(
        ['Schedule', 'Building', 'From', 'To'],
        result.changes.map((c) => el('tr', {}, [
          el('td', {}, [el('strong', { text: c.code }),
            el('div', { class: 'muted tiny', text: c.title })]),
          el('td', { class: 'tiny', text: c.building }),
          el('td', { class: 'tiny muted', text: c.from }),
          el('td', {}, [pill(c.to, 'blue')]),
        ]))
      ));
    } catch (error) { fail(error); }
  };

  const toggle = (schedule, node) => {
    if (chosen.has(schedule.id)) chosen.delete(schedule.id);
    else chosen.add(schedule.id);
    node.classList.toggle('on', chosen.has(schedule.id));
    preview();
  };

  // See showDiff: the preview must be requested before awaiting the dialog.
  const closed = modal({
    title: 'Issue a revision across the project',
    wide: true,
    render: () => el('div', {}, [
      el('p', { class: 'muted' }, [
        'Each schedule continues its own series, so one already at P02 goes to P03 while ' +
        'one at P01 goes to P02.',
      ]),
      el('div', { class: 'chips', style: 'margin-bottom:14px' }, all.map((s) => {
        const chip = el('button', { class: 'chip on' }, [
          s.buildings > 1 ? `${s.building} · ${s.code}` : s.code,
        ]);
        chip.addEventListener('click', () => toggle(s, chip));
        return chip;
      })),
      el('div', { class: 'grid-2' }, [
        field('Suitability status', status),
        field('Date', date),
      ]),
      el('div', { style: 'margin-top:12px' }, [field('Description', description)]),
      el('label', { class: 'tiny', style: 'display:flex;gap:6px;margin-top:12px;align-items:center' }, [
        published, 'Published revision (C) rather than preliminary (P)',
      ]),
      el('label', { class: 'tiny', style: 'display:flex;gap:6px;margin-top:6px;align-items:center' }, [
        freeze, 'Issue them straight away, freezing what each schedule says now',
      ]),
      planBox,
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(false) } }),
      button('Apply', { class: 'btn btn-primary', on: { click: () => close(true) } }),
    ],
  });

  preview();
  const ok = await closed;
  if (!ok || !chosen.size) return;
  try {
    const result = await api.projects.bulkRevision(p.id, { ...payload(), apply: true });
    toast(`${result.applied} schedule(s) revised`, 'ok');
    await reload();
  } catch (error) { fail(error); }
}

/* ---------------------------------------------------------------- rooms --- */

/**
 * What equipment is in each room on this job.
 *
 * The schedules hold the answer already; what they cannot do is be asked it one
 * file at a time. It lived only behind a button on the register, which is not
 * where somebody working on a project is.
 */
async function drawRooms(root) {
  const p = state.project;
  const box = el('div', { class: 'muted', text: 'Gathering…' });
  root.appendChild(box);

  let data;
  try {
    data = await api.get(`/api/projects/${p.id}/rooms`);
  } catch (error) { clear(box).appendChild(notice(error.message, 'error')); return; }
  clear(box);

  if (!data.rooms.length) {
    box.appendChild(card('Rooms', empty(
      'No rooms recorded yet',
      'Rooms come from the room or space column on each schedule. Fill some in and they ' +
      'will be grouped here.'
    )));
    return;
  }

  const search = input('', { placeholder: 'Find a room…', style: 'min-width:220px' });
  const list = el('div');

  const render = () => {
    clear(list);
    const needle = search.value.trim().toLowerCase();
    const rooms = needle
      ? data.rooms.filter((r) => r.room.toLowerCase().includes(needle))
      : data.rooms;

    if (!rooms.length) {
      list.appendChild(empty('Nothing matches', `No room here is called “${search.value}”.`));
      return;
    }

    for (const room of rooms) {
      list.appendChild(el('div', { class: 'room-block' }, [
        el('div', { class: 'room-head' }, [
          el('strong', { text: room.room }),
          el('span', { class: 'muted tiny' }, [
            Object.entries(room.by_type).map(([c, n]) => `${n}× ${c}`).join(', '),
          ]),
        ]),
        table(
          ['Type', 'Reference', 'Model', 'Building', ''],
          room.items.map((i) => el('tr', {}, [
            el('td', { class: 'tiny', text: i.code }),
            el('td', { class: 'tiny', text: i.reference || '—' }),
            el('td', { class: 'tiny mono', text: i.model_reference || '—' }),
            el('td', { class: 'tiny muted', text: i.building }),
            el('td', { class: 'cell-actions' }, [
              button('Open', {
                class: 'btn btn-sm',
                on: { click: () => go(`/schedules/${i.schedule_id}`) },
              }),
            ]),
          ]))
        ),
      ]));
    }
  };
  search.addEventListener('input', render);
  render();

  const columns = Object.entries(data.room_columns);
  box.appendChild(card(
    `${data.rooms.length} room(s)`,
    el('div', {}, [
      data.unassigned
        ? notice(
            `${data.unassigned} row(s) have no room recorded, so they are not listed here.`,
            'warn'
          )
        : null,
      list,
      columns.length
        ? el('p', { class: 'muted tiny' }, [
            'Rooms were read from: ',
            columns.map(([code, column]) => `${code} → ${column}`).join(', '),
            '. Which column names a room varies by type, so the first one that looks like ' +
            'a room is used — a wrong guess should be visible rather than hidden.',
          ])
        : null,
    ]),
    [search]
  ));
}

/* ---------------------------------------------------------------- setup --- */

/** The project's own fields, for a save that is only changing one other thing. */
function projectFields(p) {
  return {
    number: p.number, name: p.name, client: p.client,
    site_address: p.site_address, architect: p.architect,
    main_contractor: p.main_contractor, riba_stage: p.riba_stage,
    prepared_by: p.prepared_by, checked_by: p.checked_by,
    approved_by: p.approved_by,
    naming_overrides: p.naming_overrides,
    design_constants: p.design_constants,
  };
}

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
        ...projectFields(p),
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

  // The middle notes layer: under the practice's, above the equipment type's.
  const notesArea = el('textarea', {
    rows: 6,
    value: (p.notes || []).join('\n'),
    placeholder: 'One per line. Left blank, this job adds nothing of its own.',
  });

  root.appendChild(card(
    'Project notes',
    el('div', {}, [
      el('p', { class: 'muted tiny' }, [
        'These print on every schedule in this project, under the practice-wide notes and ' +
        'above anything specific to the equipment type. A schedule can still take its own ' +
        'notes over if it has to say something different.',
      ]),
      (p.organisation_notes || []).length
        ? el('details', { class: 'inherited-notes' }, [
            el('summary', { class: 'tiny muted', text: `${p.organisation_notes.length} house standard note(s) print above these` }),
            el('ol', { class: 'note-list tiny muted' },
              p.organisation_notes.map((n) => el('li', { text: n }))),
          ])
        : null,
      notesArea,
    ]),
    [button('Save notes', {
      class: 'btn btn-primary',
      on: {
        click: async () => {
          try {
            await api.projects.update(p.id, {
              ...projectFields(p),
              notes: notesArea.value.split('\n').map((l) => l.trim()).filter(Boolean),
            });
            toast('Project notes saved', 'ok');
            await reload();
          } catch (error) { fail(error); }
        },
      },
    })]
  ));

  drawDocumentFields(root, p);

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

/**
 * Which fields this job's covers and revision pages carry.
 *
 * The same list the house standard has, answered for one job. A job with no
 * blocks does not want a Building row and a job with three does; that is a
 * decision about a job, and settling it once for the whole practice was the
 * thing that made the house standard's own note a lie.
 *
 * Three states per field, not two: follow the practice, always show, always
 * hide. "Follow" is the default and it matters — a project that silently froze
 * its answer at whatever the house standard said the day it was created would
 * stop tracking the practice without anybody asking it to.
 */
async function drawDocumentFields(root, project) {
  const host = el('div');
  root.appendChild(host);

  let data;
  try {
    data = await api.projects.branding(project.id);
  } catch (error) { host.appendChild(notice(error.message, 'error')); return; }

  const draft = {
    cover_fields: { ...(data.overrides.cover_fields || {}) },
    revision_fields: { ...(data.overrides.revision_fields || {}) },
  };

  const chooser = (group, fields) => el('div', { class: 'field-chooser' },
    fields.map((f) => {
      const value = draft[group][f.key] === undefined
        ? ''
        : (draft[group][f.key] ? 'show' : 'hide');
      const control = select(
        [
          ['', `Follow the practice (${f.house ? 'shown' : 'hidden'})`],
          ['show', 'Always show'],
          ['hide', 'Always hide'],
        ],
        value,
        {
          disabled: !f.optional,
          on: {
            change: (e) => {
              if (e.target.value === '') delete draft[group][f.key];
              else draft[group][f.key] = e.target.value === 'show';
            },
          },
        }
      );
      return el('div', { class: 'chooser-row' }, [
        el('div', {}, [
          el('div', { text: f.label }),
          el('div', { class: 'muted tiny', text: f.optional ? f.hint : 'The workbook reads this row, so it always shows.' }),
        ]),
        control,
      ]);
    })
  );

  const save = async () => {
    try {
      await api.projects.setBranding(project.id, {
        cover_fields: draft.cover_fields,
        revision_fields: draft.revision_fields,
      });
      toast('This project’s document fields saved', 'ok');
      clear(host);
      await drawDocumentFields(host, project);
    } catch (error) { fail(error); }
  };

  const changed = Object.keys(draft.cover_fields).length
    + Object.keys(draft.revision_fields).length;

  host.appendChild(card(
    'What this project’s documents show',
    el('div', {}, [
      el('p', { class: 'muted tiny' }, [
        'The practice-wide setting is the default; this is where one job differs from it. ',
        'Fonts, colours and the logo stay house standard — every document that leaves the ',
        'office should look like it came from the same place.',
      ]),
      changed
        ? notice(`${changed} field(s) on this job differ from the house standard.`, 'info')
        : null,
      el('div', { class: 'grid-2', style: 'margin-top:12px' }, [
        el('div', {}, [
          el('strong', { class: 'tiny', text: 'Front cover' }),
          chooser('cover_fields', data.cover_fields),
        ]),
        el('div', {}, [
          el('strong', { class: 'tiny', text: 'Revision page' }),
          chooser('revision_fields', data.revision_fields),
        ]),
      ]),
      el('p', { class: 'muted tiny', style: 'margin-top:12px' }, [
        'As it stands, the cover carries: ',
        data.preview.cover.map((f) => f.label).join(', ') || 'nothing',
        '.',
      ]),
    ]),
    [
      button('Follow the practice for everything', {
        disabled: !changed,
        on: {
          click: async () => {
            draft.cover_fields = {};
            draft.revision_fields = {};
            await save();
          },
        },
      }),
      button('Save document fields', { class: 'btn btn-primary', on: { click: save } }),
    ],
    'Set in Settings for the practice as a whole; overridden here for this job only.'
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
