// The register: every schedule with its filename, revision, issue date and
// status. This is the "read table" the tool is arranged around.
//
// Under v1 this was a Power Query scrape of the Metadata sheet of every workbook
// in a folder, refreshed by hand and stale in between. It is a query now.

import { api } from '../api.js';
import { go, store } from '../app.js';
import {
  button, el, empty, fail, formatDate, input, mount, pill, select, table, toast,
} from '../ui.js';

export async function registerView() {
  const [rows, projects] = await Promise.all([api.register(), api.projects.list()]);
  store.projects = projects;

  const state = { project: '', building: '', status: '', query: '' };

  const projectFilter = select(
    [['', 'All projects'], ...projects.map((p) => [p.id, p.number || p.name])],
    ''
  );
  const statusFilter = select(
    [['', 'Any status'], ...[...new Set(rows.map((r) => r.status).filter(Boolean))].sort().map((s) => [s, s])],
    ''
  );
  const search = input('', { placeholder: 'Filter by number, name or building…' });

  const body = el('tbody');

  const apply = () => {
    state.project = projectFilter.value;
    state.status = statusFilter.value;
    state.query = search.value.trim().toLowerCase();

    const visible = rows.filter((r) => {
      if (state.project && r.project_id !== state.project) return false;
      if (state.status && r.status !== state.status) return false;
      if (state.query) {
        const haystack = [
          r.document_number, r.schedule_name, r.building, r.code,
          r.project_name, r.project_number, r.file_name,
        ].join(' ').toLowerCase();
        if (!haystack.includes(state.query)) return false;
      }
      return true;
    });

    while (body.firstChild) body.removeChild(body.firstChild);

    if (!visible.length) {
      body.appendChild(el('tr', {}, [
        el('td', { colspan: 8 }, [
          empty('Nothing matches', 'Loosen the filters, or add a schedule to a project.'),
        ]),
      ]));
    }

    // Grouped by building, because that is how the documents are issued.
    let lastGroup = null;
    for (const r of visible) {
      const groupKey = `${r.project_id}::${r.building_id}`;
      if (groupKey !== lastGroup) {
        lastGroup = groupKey;
        body.appendChild(el('tr', {}, [
          el('td', {
            colspan: 8,
            style: 'background:var(--grey-100);font-weight:600;font-size:11.5px',
          }, [
            `${r.project_number || r.project_name}`,
            el('span', { class: 'muted', text: ` · ${r.building}` }),
          ]),
        ]));
      }

      body.appendChild(el('tr', {
        class: 'clickable',
        on: { click: () => go(`/schedules/${r.schedule_id}`) },
      }, [
        el('td', {}, [
          el('strong', { text: r.code }),
          el('div', { class: 'muted tiny', text: r.schedule_name }),
        ]),
        el('td', {}, [el('span', { class: 'dn', text: r.document_number })]),
        el('td', {}, [el('span', { class: 'dn muted', text: r.file_name })]),
        el('td', {}, [
          r.revision
            ? pill(r.revision, r.revision.startsWith('C') ? 'green' : 'blue')
            : el('span', { class: 'muted', text: '—' }),
        ]),
        el('td', { class: 'tiny nowrap', text: formatDate(r.issue_date) }),
        el('td', {}, [
          r.status
            ? pill(r.status, r.status === 'S0' ? 'quiet' : 'blue')
            : el('span', { class: 'muted', text: '—' }),
        ]),
        el('td', { class: 'tiny muted', text: r.status_description || '' }),
        el('td', { class: 'num', text: r.row_count ? String(r.row_count) : '—' }),
      ]));
    }
  };

  for (const control of [projectFilter, statusFilter]) {
    control.addEventListener('change', apply);
  }
  search.addEventListener('input', apply);

  const page = el('div', { class: 'page page-wide' }, [
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: 'Register' }),
        el('div', {
          class: 'sub',
          text: 'Every schedule, with its current revision, issue date and status. Always current — nothing to refresh.',
        }),
      ]),
      button('Copy as TSV', {
        on: {
          click: async () => {
            const header = [
              'Project', 'Building', 'Code', 'DocumentNumber', 'ScheduleName',
              'Revision', 'IssueDate', 'Status', 'FileName',
            ].join('\t');
            const lines = rows.map((r) => [
              r.project_number || r.project_name, r.building, r.code, r.document_number,
              r.schedule_name, r.revision, r.issue_date || '', r.status, r.file_name,
            ].join('\t'));
            try {
              await navigator.clipboard.writeText([header, ...lines].join('\n'));
              toast('Register copied — paste straight into Excel', 'ok');
            } catch (error) { fail(error); }
          },
        },
      }),
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
      el('div', { style: 'min-width:180px' }, [projectFilter]),
      el('div', { style: 'min-width:150px' }, [statusFilter]),
      el('div', { style: 'flex:1;min-width:220px' }, [search]),
    ]),
    el('div', { class: 'card-body tight' }, [
      el('div', { class: 'table-wrap' }, [
        el('table', {}, [
          el('thead', {}, [
            el('tr', {}, [
              'Type', 'Document number', 'File name', 'Rev', 'Issued', 'Status',
              'Suitability', { text: 'Rows', class: 'num' },
            ].map((h) =>
              typeof h === 'object' ? el('th', { class: h.class, text: h.text }) : el('th', { text: h })
            )),
          ]),
          body,
        ]),
      ]),
    ]),
  ]));

  mount(page);
  apply();
}
