// The project list, and creating one.

import { api } from '../api.js';
import { go, store } from '../app.js';
import { button, el, empty, fail, field, formatDate, input, modal, mount, table } from '../ui.js';

export async function projectsView() {
  const projects = await api.projects.list();
  store.projects = projects;

  const newButton = button('New project', {
    class: 'btn btn-primary',
    on: { click: () => createProject() },
  });

  const page = el('div', { class: 'page' }, [
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: 'Projects' }),
        el('div', {
          class: 'sub',
          text: 'Each project holds its buildings, their schedules, and the setup they all read from.',
        }),
      ]),
      newButton,
    ]),
  ]);

  if (!projects.length) {
    page.appendChild(el('section', { class: 'card' }, [
      empty(
        'No projects yet',
        'A project carries the client, project number and design constants that every schedule under it reads.',
        button('Create the first project', {
          class: 'btn btn-primary',
          on: { click: () => createProject() },
        })
      ),
    ]));
    mount(page);
    return;
  }

  const rows = projects.map((p) =>
    el('tr', {
      class: 'clickable',
      on: { click: () => go(`/projects/${p.id}`) },
    }, [
      el('td', {}, [
        el('strong', { text: p.number || '—' }),
        el('div', { class: 'muted tiny', text: p.name || 'Untitled project' }),
      ]),
      el('td', { text: p.client || '—' }),
      el('td', {}, [
        // Which blocks, not just how many: a job is remembered by its buildings.
        p.buildings && p.buildings.length
          ? el('span', { class: 'tiny', text: p.buildings.slice(0, 3).join(', ') })
          : el('span', { class: 'muted', text: '—' }),
        p.buildings && p.buildings.length > 3
          ? el('span', { class: 'muted tiny', text: ` +${p.buildings.length - 3}` })
          : null,
      ]),
      el('td', { class: 'num', text: String(p.schedule_count) }),
      el('td', { class: 'muted tiny nowrap', text: formatDate(p.updated_at) }),
    ])
  );

  page.appendChild(el('section', { class: 'card' }, [
    el('div', { class: 'card-body tight' }, [
      table(
        ['Project', 'Client', 'Buildings', { text: 'Schedules', class: 'num' }, 'Updated'],
        rows
      ),
    ]),
  ]));

  mount(page);
}

export async function createProject() {
  const fields = {
    number: input('', { placeholder: 'CM4220' }),
    name: input('', { placeholder: 'Head Office Refurbishment' }),
    client: input('', { placeholder: 'Client name' }),
    prepared_by: input('', { placeholder: 'AG' }),
    checked_by: input('', { placeholder: 'LJ' }),
    approved_by: input('', { placeholder: 'RS' }),
  };

  const result = await modal({
    title: 'New project',
    render: () => el('div', {}, [
      el('div', { class: 'grid-2' }, [
        field('Project number', fields.number, 'Becomes the first token of every document number.'),
        field('Project name', fields.name),
      ]),
      el('div', { class: 'grid-2', style: 'margin-top:12px' }, [
        field('Client', fields.client),
        el('div'),
      ]),
      el('div', { class: 'grid-3', style: 'margin-top:12px' }, [
        field('Prepared by', fields.prepared_by),
        field('Checked by', fields.checked_by),
        field('Approved by', fields.approved_by),
      ]),
      el('p', { class: 'help muted', style: 'margin-top:14px' }, [
        'One building is created for you. The building layer stays hidden until a ' +
        'second one is added, so small jobs never see it.',
      ]),
    ]),
    actions: (close) => [
      button('Cancel', { on: { click: () => close(null) } }),
      button('Create project', {
        class: 'btn btn-primary',
        on: {
          click: async () => {
            const payload = Object.fromEntries(
              Object.entries(fields).map(([key, node]) => [key, node.value.trim()])
            );
            if (!payload.number && !payload.name) {
              fail(new Error('Give the project a number or a name.'));
              return;
            }
            try {
              close(await api.projects.create(payload));
            } catch (error) {
              fail(error);
            }
          },
        },
      }),
    ],
  });

  if (result) {
    store.projects = await api.projects.list();
    go(`/projects/${result.id}`);
  }
}
