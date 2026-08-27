// The change log: why a schedule says something different from last week.
//
// Almost everything here is shared on purpose. A library value is read rather
// than copied, so correcting a product corrects every schedule using it. A
// type's columns are the type's, so widening one widens it everywhere. That
// sharing is the feature, and it is also why somebody opens a schedule they
// have not touched and finds it changed. This is the page that answers it.

import { api } from '../api.js';
import { go } from '../app.js';
import {
  button, card, clear, el, empty, fail, formatDate, mount, notice, pill, table,
} from '../ui.js';

const AREAS = [
  ['', 'Everything'],
  ['type', 'Schedule types'],
  ['library', 'Equipment library'],
  ['notes', 'Notes'],
  ['branding', 'Branding'],
  ['numbering', 'Numbering'],
];

const TONE = { warn: 'amber', info: 'quiet' };

const state = { area: '' };

export async function changesView() {
  const log = await api.impact(state.area);

  const page = el('div', { class: 'page page-wide' }, [
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: 'Changes' }),
        el('div', {
          class: 'sub',
          text: 'What has moved across the practice, and what it lands on. ' +
                'A schedule can change without anybody opening it.',
        }),
      ]),
    ]),
    el('div', { class: 'seg' }, AREAS.map(([key, label]) =>
      el('button', {
        class: state.area === key ? 'active' : '',
        on: { click: () => { state.area = key; changesView(); } },
      }, [label])
    )),
  ]);

  const body = el('div');
  page.appendChild(body);
  mount(page);

  if (log.counts.warnings) {
    body.appendChild(notice(
      `${log.counts.warnings} change(s) may have moved what a schedule says. ` +
      'Everything below is listed newest first.',
      'warn'
    ));
  }

  if (log.stale_schedules.length && !state.area) {
    body.appendChild(staleCard(log.stale_schedules));
  }

  if (!log.entries.length) {
    body.appendChild(el('section', { class: 'card' }, [
      empty(
        'Nothing has changed yet',
        'Edits to a schedule type, a library product, the house notes or the branding ' +
        'are recorded here as they happen.'
      ),
    ]));
    return;
  }

  body.appendChild(el('section', { class: 'card' }, [
    el('div', { class: 'card-body tight' }, [
      table(
        ['When', 'Area', 'What changed', 'Lands on', ''],
        log.entries.map(entryRow)
      ),
    ]),
  ]));
}

function entryRow(entry) {
  const detail = entry.detail || {};
  const affected = detail.affected_count;

  return el('tr', {}, [
    el('td', { class: 'tiny muted nowrap', text: formatDate(entry.at) }),
    el('td', {}, [pill(entry.area, TONE[entry.severity] || 'quiet')]),
    el('td', {}, [
      el('div', { text: entry.summary }),
      ...(detail.diff && detail.diff.warnings || []).map((w) =>
        el('div', { class: 'tiny', style: 'color:var(--amber-text,#8a6100)', text: w })
      ),
    ]),
    el('td', { class: 'tiny' }, [
      affected === undefined
        ? el('span', { class: 'muted', text: '—' })
        : el('span', {}, [
            `${affected} schedule${affected === 1 ? '' : 's'}`,
            detail.rows_at_risk
              ? el('div', { class: 'tiny', text: `${detail.rows_at_risk} filled row(s) affected` })
              : null,
          ]),
    ]),
    el('td', { class: 'cell-actions' }, [
      detail.affected && detail.affected.length
        ? button('Show', {
            class: 'btn btn-sm',
            on: { click: (e) => toggleAffected(e.currentTarget, detail.affected) },
          })
        : null,
    ]),
  ]);
}

function toggleAffected(node, affected) {
  const row = node.closest('tr');
  const next = row.nextElementSibling;
  if (next && next.classList.contains('detail-row')) { next.remove(); return; }

  const detail = el('tr', { class: 'detail-row' }, [
    el('td', { colSpan: 5 }, [
      table(
        ['Project', 'Building', 'Schedule', { text: 'Filled rows', class: 'num' }, ''],
        affected.map((a) => el('tr', {}, [
          el('td', { class: 'tiny', text: a.project }),
          el('td', { class: 'tiny', text: a.building }),
          el('td', { class: 'tiny' }, [el('strong', { text: a.code })]),
          el('td', { class: 'num tiny', text: String(a.rows) }),
          el('td', {}, [
            button('Open', {
              class: 'btn btn-sm',
              on: { click: () => go(`/schedules/${a.schedule_id}`) },
            }),
          ]),
        ]))
      ),
    ]),
  ]);
  row.after(detail);
}

/**
 * Schedules built against an older version of their type.
 *
 * Not an error, and not a queue of work: a schedule always shows its type's
 * current columns. It is the list of documents that were set up before a change,
 * which is what somebody wants when a duty has moved and they are working out
 * why.
 */
function staleCard(stale) {
  return card(
    'Built before the latest type change',
    table(
      ['Project', 'Building', 'Schedule', 'Built against', 'Now', ''],
      stale.map((s) => el('tr', {}, [
        el('td', { class: 'tiny', text: s.project }),
        el('td', { class: 'tiny', text: s.building }),
        el('td', {}, [el('strong', { text: s.code })]),
        el('td', {}, [pill(`v${s.built_against}`, 'amber')]),
        el('td', {}, [pill(`v${s.current}`, 'quiet')]),
        el('td', { class: 'cell-actions' }, [
          button('Open', {
            class: 'btn btn-sm',
            on: { click: () => go(`/schedules/${s.schedule_id}`) },
          }),
        ]),
      ]))
    ),
    [],
    'These show their type’s current columns, so nothing needs migrating. This is ' +
    'the record of which documents predate a change.'
  );
}
