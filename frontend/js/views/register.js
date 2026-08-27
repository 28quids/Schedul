// The register: every schedule with its filename, revision, issue date and
// status. This is the "read table" the tool is arranged around.
//
// Project-first rather than a flat list, because that is how anyone looks for a
// schedule: they know the job, then the block, then the type. Under v1 this was
// a Power Query scrape of every workbook's Metadata sheet, refreshed by hand and
// stale in between. It is a query now.

import { api } from '../api.js';
import { go, store } from '../app.js';
import {
  button, card, clear, el, empty, fail, formatDate, input, modal, mount, notice,
  pill, select, table, toast,
} from '../ui.js';

const state = { query: '', status: '', open: new Set(), mode: 'projects' };

export async function registerView(query = '') {
  // '#/register?project=<id>' opens the register already narrowed to one job,
  // which is what the sidebar links to from inside a project.
  const params = new URLSearchParams(query || '');
  const only = params.get('project') || '';
  if (only) state.open.add(only);

  const [rows, projects] = await Promise.all([api.register(only), api.projects.list()]);
  store.projects = projects;

  const search = input(state.query, {
    placeholder: 'Search project, number, client, building, code, document number or status…',
  });
  const statusFilter = select(
    [
      ['', 'Any status'],
      ...[...new Set(rows.map((r) => r.status).filter(Boolean))].sort().map((s) => [s, s]),
    ],
    state.status
  );
  const modeSwitch = el('div', { class: 'seg' }, [
    el('button', {
      class: state.mode === 'projects' ? 'active' : '',
      on: { click: () => { state.mode = 'projects'; render(); } },
    }, ['By project']),
    el('button', {
      class: state.mode === 'flat' ? 'active' : '',
      on: { click: () => { state.mode = 'flat'; render(); } },
    }, ['Flat list']),
  ]);

  const body = el('div');

  // Client comes from the project list, not the register rows, so searching by
  // client works even though a schedule does not carry one.
  const clientOf = Object.fromEntries(projects.map((p) => [p.id, p.client || '']));

  const matches = (r) => {
    if (state.status && r.status !== state.status) return false;
    if (!state.query) return true;
    const haystack = [
      r.project_name, r.project_number, clientOf[r.project_id], r.building,
      r.code, r.schedule_name, r.document_number, r.file_name,
      r.revision, r.status, r.status_description,
    ].join(' ').toLowerCase();
    return state.query
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean)
      .every((term) => haystack.includes(term));
  };

  const render = () => {
    clear(body);
    const visible = rows.filter(matches);

    if (!visible.length) {
      body.appendChild(el('section', { class: 'card' }, [
        empty('Nothing matches', 'Loosen the filters, or add a schedule to a project.'),
      ]));
      return;
    }

    body.appendChild(
      state.mode === 'flat' ? flatList(visible) : byProject(visible, projects, clientOf)
    );
  };

  search.addEventListener('input', () => {
    state.query = search.value.trim();
    render();
  });
  statusFilter.addEventListener('change', () => {
    state.status = statusFilter.value;
    render();
  });

  const page = el('div', { class: 'page page-wide' }, [
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: 'Register' }),
        el('div', {
          class: 'sub',
          text: only
            ? `${rows.length} schedule(s) on this project. Always current — nothing to refresh.`
            : `${rows.length} schedule(s) across ${projects.length} project(s). ` +
              'Always current — nothing to refresh.',
        }),
      ]),
      el('div', { class: 'btn-row' }, [
        only
          ? button('Show every project', { on: { click: () => go('/register') } })
          : null,
        modeSwitch,
        button('Copy as TSV', { on: { click: () => copyTsv(rows.filter(matches)) } }),
      ]),
    ]),
  ]);

  if (!rows.length) {
    page.appendChild(el('section', { class: 'card' }, [
      empty('Nothing scheduled yet', 'Create a project and add a schedule to it.',
        button('Go to projects', { class: 'btn btn-primary', on: { click: () => go('/projects') } })),
    ]));
    mount(page);
    return;
  }

  page.appendChild(el('section', { class: 'card' }, [
    el('div', { class: 'card-body', style: 'display:flex;gap:10px;flex-wrap:wrap' }, [
      el('div', { style: 'flex:1;min-width:280px' }, [search]),
      el('div', { style: 'min-width:150px' }, [statusFilter]),
    ]),
  ]));
  page.appendChild(body);
  mount(page);
  render();
}

/* ---------------------------------------------------------- by project --- */

function byProject(visible, projects, clientOf) {
  const wrap = el('div');
  const grouped = new Map();

  for (const row of visible) {
    if (!grouped.has(row.project_id)) grouped.set(row.project_id, new Map());
    const buildings = grouped.get(row.project_id);
    if (!buildings.has(row.building_id)) buildings.set(row.building_id, []);
    buildings.get(row.building_id).push(row);
  }

  // A search that narrows to a few projects should show them open; browsing
  // everything should not drown the reader in rows.
  const autoOpen = grouped.size <= 2;

  for (const [projectId, buildings] of grouped) {
    const first = [...buildings.values()][0][0];
    const count = [...buildings.values()].reduce((n, r) => n + r.length, 0);
    const open = state.open.has(projectId) || autoOpen;

    const bodyNode = el('div');
    const header = el('header', {
      class: 'card-head clickable',
      style: 'cursor:pointer',
      on: {
        click: () => {
          if (state.open.has(projectId)) state.open.delete(projectId);
          else state.open.add(projectId);
          bodyNode.style.display = state.open.has(projectId) ? '' : 'none';
          caret.textContent = state.open.has(projectId) ? '▾' : '▸';
        },
      },
    }, []);

    const caret = el('span', { class: 'muted', text: open ? '▾' : '▸' });
    header.appendChild(el('div', { style: 'display:flex;gap:10px;align-items:baseline' }, [
      caret,
      el('h2', { text: first.project_number || first.project_name }),
      el('span', { class: 'hint' }, [
        first.project_name !== first.project_number ? `${first.project_name} · ` : '',
        clientOf[projectId] || 'no client',
        ` · ${count} schedule${count === 1 ? '' : 's'}`,
      ]),
    ]));
    header.appendChild(el('div', { class: 'btn-row' }, [
      button('Rooms', {
        class: 'btn btn-sm',
        title: 'What equipment is in each room on this project',
        on: {
          click: (e) => { e.stopPropagation(); showRooms(projectId, first); },
        },
      }),
      button('Open project', {
        class: 'btn btn-sm',
        on: { click: (e) => { e.stopPropagation(); go(`/projects/${projectId}`); } },
      }),
    ]));

    bodyNode.style.display = open ? '' : 'none';
    const multi = buildings.size > 1;
    for (const [, rowsInBuilding] of buildings) {
      if (multi) {
        bodyNode.appendChild(el('div', {
          class: 'tiny',
          style: 'padding:7px 14px;background:var(--grey-100);font-weight:600',
          text: rowsInBuilding[0].building,
        }));
      }
      bodyNode.appendChild(scheduleTable(rowsInBuilding));
    }

    wrap.appendChild(el('section', { class: 'card' }, [header, bodyNode]));
  }
  return wrap;
}

function scheduleTable(rows) {
  return table(
    ['Type', 'Document number', 'Rev', 'Issued', 'Status', 'Suitability',
     { text: 'Rows', class: 'num' }],
    rows.map((r) => el('tr', {
      class: 'clickable',
      on: { click: () => go(`/schedules/${r.schedule_id}`) },
    }, [
      el('td', {}, [
        el('strong', { text: r.code }),
        el('div', { class: 'muted tiny', text: r.schedule_name }),
      ]),
      el('td', {}, [
        el('div', { class: 'dn', text: r.document_number }),
        el('div', { class: 'dn muted tiny', text: r.file_name }),
      ]),
      el('td', {}, [
        r.revision
          ? pill(r.revision, r.revision.startsWith('C') ? 'green' : 'blue')
          : el('span', { class: 'muted', text: '—' }),
      ]),
      el('td', { class: 'tiny nowrap', text: formatDate(r.issue_date) }),
      el('td', {}, [
        r.status ? pill(r.status, r.status === 'S0' ? 'quiet' : 'blue')
                 : el('span', { class: 'muted', text: '—' }),
      ]),
      el('td', { class: 'tiny muted', text: r.status_description || '' }),
      el('td', { class: 'num', text: r.row_count ? String(r.row_count) : '—' }),
    ]))
  );
}

function flatList(visible) {
  return el('section', { class: 'card' }, [
    el('div', { class: 'card-body tight' }, [
      table(
        ['Project', 'Building', 'Type', 'Document number', 'Rev', 'Issued', 'Status'],
        visible.map((r) => el('tr', {
          class: 'clickable',
          on: { click: () => go(`/schedules/${r.schedule_id}`) },
        }, [
          el('td', { class: 'tiny' }, [
            el('strong', { text: r.project_number || r.project_name }),
          ]),
          el('td', { class: 'tiny', text: r.building }),
          el('td', {}, [el('strong', { text: r.code })]),
          el('td', {}, [el('span', { class: 'dn', text: r.document_number })]),
          el('td', {}, [
            r.revision
              ? pill(r.revision, r.revision.startsWith('C') ? 'green' : 'blue')
              : el('span', { class: 'muted', text: '—' }),
          ]),
          el('td', { class: 'tiny nowrap', text: formatDate(r.issue_date) }),
          el('td', {}, [r.status ? pill(r.status, 'quiet') : '']),
        ]))
      ),
    ]),
  ]);
}

/* -------------------------------------------------------------- rooms --- */

async function showRooms(projectId, first) {
  const body = el('div', { class: 'muted', text: 'Gathering…' });
  const closed = modal({
    title: `Equipment by room — ${first.project_number || first.project_name}`,
    wide: true,
    render: () => body,
  });

  try {
    const data = await api.get(`/api/projects/${projectId}/rooms`);
    clear(body);

    if (!data.rooms.length) {
      body.appendChild(empty(
        'No rooms recorded yet',
        'Rooms come from the room or space column on each schedule. Fill some in and ' +
        'they will be grouped here.'
      ));
    } else {
      body.appendChild(notice(
        `${data.rooms.length} room(s)` +
        (data.unassigned ? `, and ${data.unassigned} row(s) with no room recorded.` : '.'),
        'info'
      ));

      for (const room of data.rooms) {
        body.appendChild(el('div', { style: 'margin-bottom:14px' }, [
          el('div', { style: 'display:flex;gap:8px;align-items:baseline;margin-bottom:4px' }, [
            el('strong', { text: room.room }),
            el('span', { class: 'muted tiny' }, [
              Object.entries(room.by_type).map(([c, n]) => `${n}× ${c}`).join(', '),
            ]),
          ]),
          table(
            ['Type', 'Reference', 'Model', 'Building'],
            room.items.map((i) => el('tr', {}, [
              el('td', { class: 'tiny', text: i.code }),
              el('td', { class: 'tiny', text: i.reference || '—' }),
              el('td', { class: 'tiny mono', text: i.model_reference || '—' }),
              el('td', { class: 'tiny muted', text: i.building }),
            ]))
          ),
        ]));
      }

      const columns = Object.entries(data.room_columns);
      if (columns.length) {
        body.appendChild(el('p', { class: 'muted tiny' }, [
          'Rooms were read from: ',
          columns.map(([code, column]) => `${code} → ${column}`).join(', '),
          '.',
        ]));
      }
    }
  } catch (error) {
    clear(body).appendChild(notice(error.message, 'error'));
  }

  await closed;
}

async function copyTsv(rows) {
  const header = [
    'Project', 'ProjectNumber', 'Building', 'Code', 'DocumentNumber', 'ScheduleName',
    'Revision', 'IssueDate', 'Status', 'StatusDescription', 'FileName',
  ].join('\t');
  const lines = rows.map((r) => [
    r.project_name, r.project_number, r.building, r.code, r.document_number,
    r.schedule_name, r.revision, r.issue_date || '', r.status, r.status_description,
    r.file_name,
  ].join('\t'));
  try {
    await navigator.clipboard.writeText([header, ...lines].join('\n'));
    toast(`${rows.length} row(s) copied — paste straight into Excel`, 'ok');
  } catch (error) { fail(error); }
}
