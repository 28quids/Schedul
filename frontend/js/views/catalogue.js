// The catalogue: every reusable schedule type this organisation has.

import { api } from '../api.js';
import { go, store } from '../app.js';
import { button, el, empty, fail, mount, pill, table, toast, confirmDialog } from '../ui.js';

export async function catalogueView() {
  const [types, meta] = await Promise.all([api.catalogue.list(), api.catalogue.meta()]);
  store.catalogue = types;

  const page = el('div', { class: 'page' }, [
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: 'Schedule types' }),
        el('div', {
          class: 'sub',
          text: 'The reusable shape of each kind of schedule: its columns, its formulas and its own notes.',
        }),
      ]),
      button('New type', { class: 'btn btn-primary', on: { click: () => go('/catalogue/new') } }),
    ]),
  ]);

  if (!types.length) {
    page.appendChild(el('section', { class: 'card' }, [
      empty('The catalogue is empty', 'Create a type to start scheduling.',
        button('New type', { class: 'btn btn-primary', on: { click: () => go('/catalogue/new') } })),
    ]));
    mount(page);
    return;
  }

  const rows = types.map((t) =>
    el('tr', { class: 'clickable', on: { click: () => go(`/catalogue/${t.id}`) } }, [
      el('td', {}, [el('strong', { text: t.code })]),
      el('td', {}, [
        t.title,
        t.short ? el('div', { class: 'muted tiny', text: t.short }) : null,
      ]),
      el('td', {}, [
        t.volume
          ? el('span', {}, [
              el('span', { class: 'mono', text: t.volume }),
              el('span', { class: 'muted tiny', text: ` ${t.volume_label || ''}` }),
            ])
          : el('span', { class: 'muted', text: 'not set' }),
      ]),
      el('td', { class: 'num', text: String(t.column_count) }),
      el('td', {}, [pill(`v${t.version}`, t.version > 1 ? 'blue' : 'quiet')]),
      el('td', { class: 'cell-actions', on: { click: (e) => e.stopPropagation() } }, [
        button('Retire', {
          class: 'btn btn-sm btn-danger',
          on: {
            click: async () => {
              const ok = await confirmDialog({
                title: `Retire ${t.code}?`,
                message:
                  'It disappears from the list of types you can add. Schedules already built ' +
                  'from it are untouched.',
                confirmLabel: 'Retire type',
                danger: true,
              });
              if (!ok) return;
              try {
                await api.catalogue.archive(t.id);
                toast(`${t.code} retired`, 'ok');
                catalogueView();
              } catch (error) { fail(error); }
            },
          },
        }),
      ]),
    ])
  );

  page.appendChild(el('section', { class: 'card' }, [
    el('div', { class: 'card-body tight' }, [
      table(
        ['Code', 'Title', 'Volume', { text: 'Columns', class: 'num' }, 'Version', ''],
        rows
      ),
    ]),
  ]));

  mount(page);
}
