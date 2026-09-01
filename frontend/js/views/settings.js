// The house standard: everything that varies between practices.
//
// Keeping it in one place is what makes a second practice a profile rather than
// a fork, and it is why nothing company-specific is hardcoded anywhere else.

import { api } from '../api.js';
import {
  button, card, clear, confirmDialog, download, el, fail, field, input, mount,
  notice, pageHead, pill, select, table, textarea, toast,
} from '../ui.js';
import { brandingSection } from './branding.js';

const state = { tab: 'numbering' };

export async function settingsView() {
  const settings = await api.settings.read();
  const house = settings.house_standard;

  const page = el('div', { class: 'page' }, [
    pageHead(
      'House standard',
      `${settings.organisation.name} · ISO 19650 field structure, with your token values and wording.`
    ),
    el('div', { class: 'tabs' }, [
      ['numbering', 'Numbering'],
      ['content', 'Notes and constants'],
      ['branding', 'Branding'],
      ['data', 'Your data'],
    ].map(([key, label]) =>
      el('button', {
        class: `tab${state.tab === key ? ' active' : ''}`,
        on: { click: () => { state.tab = key; settingsView(); } },
      }, [label])
    )),
  ]);

  // Branding is its own screen's worth of settings, and it is the one that
  // decides what a document looks like rather than what it is called.
  if (state.tab === 'branding') {
    const host = el('div');
    page.appendChild(host);
    mount(page);
    await brandingSection(host);
    return;
  }

  if (state.tab === 'data') {
    const host = el('div');
    page.appendChild(host);
    mount(page);
    await dataSection(host);
    return;
  }

  /** Append a card only when its tab is the one being shown. */
  const add = (tab, node) => { if (state.tab === tab) page.appendChild(node); };

  /* -------------------------------------------------------- numbering --- */

  const pattern = input(house.naming.pattern, { class: 'mono' });
  // The descriptor on the end of a filename. The v1 files carried the full
  // schedule title, which makes an issued filename long enough to be awkward in
  // a document management system; a practice can shorten or drop it.
  const suffix = input(house.naming.suffix ?? '_-_{title_slug}', { class: 'mono' });
  const tokenBox = el('div');
  const problemBox = el('div');

  const tokens = JSON.parse(JSON.stringify(house.naming.tokens));

  const renderTokens = () => {
    clear(tokenBox).appendChild(table(
      ['Token', 'Scope', 'Value', 'In filenames', 'Width', 'Starts at'],
      Object.entries(tokens).map(([name, spec]) => el('tr', {}, [
        el('td', { class: 'mono', text: name }),
        el('td', {}, [pill(spec.scope, spec.scope === 'type' ? 'green' : 'quiet')]),
        el('td', { style: 'width:150px' }, [
          spec.scope === 'schedule'
            ? el('span', { class: 'muted tiny', text: 'per schedule' })
            : input(spec.value ?? '', {
                on: { input: (e) => { spec.value = e.target.value; } },
              }),
        ]),
        el('td', { style: 'width:130px' }, [
          spec.filename_value !== undefined
            ? input(spec.filename_value ?? '', {
                on: { input: (e) => { spec.filename_value = e.target.value; } },
              })
            : el('span', { class: 'muted tiny', text: 'same' }),
        ]),
        el('td', { style: 'width:80px' }, [
          spec.width !== undefined
            ? input(String(spec.width), {
                type: 'number',
                on: { input: (e) => { spec.width = parseInt(e.target.value, 10) || 8; } },
              })
            : el('span', { class: 'muted', text: '—' }),
        ]),
        el('td', { style: 'width:80px' }, [
          spec.start !== undefined
            ? input(String(spec.start), {
                type: 'number',
                on: { input: (e) => { spec.start = parseInt(e.target.value, 10) || 10; } },
              })
            : el('span', { class: 'muted', text: '—' }),
        ]),
      ]))
    ));
  };
  renderTokens();

  const showFilename = () => {
    const node = document.getElementById('filename-preview');
    if (!node) return;
    const ending = suffix.value.replace('{title_slug}', 'Fan_Coil_Unit_Schedule');
    node.textContent =
      `A schedule would be filed as: CM4220-BOV-5_6-HQ049-SC-M-00000010-G00300-XX-XX${ending}.xlsx`;
  };
  suffix.addEventListener('input', showFilename);
  setTimeout(showFilename, 0);

  if (settings.naming_problems.length) {
    problemBox.appendChild(notice('The pattern has problems:', 'error', settings.naming_problems));
  }

  const saveNaming = async () => {
    try {
      const result = await api.settings.update({
        naming: {
          ...house.naming,
          pattern: pattern.value,
          suffix: suffix.value,
          tokens,
        },
      });
      clear(problemBox);
      if (result.naming_problems.length) {
        problemBox.appendChild(notice('The pattern has problems:', 'error', result.naming_problems));
      } else {
        toast('Numbering saved', 'ok');
      }
    } catch (error) { fail(error); }
  };

  add('numbering', card(
    'Document numbering',
    el('div', {}, [
      problemBox,
      field('Pattern', pattern, 'Every {token} must be defined below.'),
      el('div', { style: 'margin-top:12px' }, [
        field(
          'Filename ending',
          suffix,
          '{title_slug} becomes the schedule title. Leave it blank for a filename that ' +
          'is just the document number — the title is on the cover and in the register ' +
          'either way.'
        ),
        el('div', { class: 'muted tiny', id: 'filename-preview' }),
      ]),
      el('div', { style: 'margin-top:14px' }, [tokenBox]),
      el('p', { class: 'help muted', style: 'margin-top:10px' }, [
        'Scope decides where a value can be overridden: company is fixed for the practice, ' +
        'type follows the equipment, building varies per block, schedule is per document. ' +
        'The most specific scope with a value wins.',
      ]),
    ]),
    [button('Save numbering', { class: 'btn btn-primary', on: { click: saveNaming } })]
  ));

  /* ------------------------------------------------------------ notes --- */

  const notesArea = textarea(house.general_notes.join('\n'), { rows: 9 });

  const saveNotes = async () => {
    const lines = notesArea.value.split('\n').map((l) => l.trim()).filter(Boolean);
    // Saving away every note is a real answer — a practice may print none — but
    // it is also what an empty box does by accident, so it is confirmed rather
    // than assumed.
    if (!lines.length && house.general_notes.length) {
      const ok = await confirmDialog({
        title: 'Print no notes at all?',
        message:
          `The ${house.general_notes.length} note(s) this practice prints on every schedule ` +
          'would be removed. Schedules would carry only what a project adds and what the ' +
          'equipment type says.',
        confirmLabel: 'Remove every note',
        danger: true,
      });
      if (!ok) return;
    }
    try {
      await api.settings.update({ general_notes: lines });
      toast('Notes saved', 'ok');
      settingsView();
    } catch (error) { fail(error); }
  };

  const defaults = settings.default_general_notes || [];

  add('content', card(
    'General notes',
    el('div', {}, [
      el('p', { class: 'muted tiny' }, [
        'One per line. These print at the top of every schedule in the practice, before ' +
        'anything a project adds and before anything specific to the equipment type. A ' +
        'single schedule can still take its notes over if it has to say something else.',
      ]),
      house.general_notes.length
        ? null
        : notice(
            'This practice currently prints no general notes, so schedules carry only what ' +
            'a project adds and what the equipment type says.',
            'warn'
          ),
      notesArea,
    ]),
    [
      defaults.length
        ? button('Restore the built-in notes', {
            title: 'Put the wording a fresh practice starts with back in the box',
            on: {
              click: () => {
                notesArea.value = defaults.join('\n');
                toast('Built-in notes put back in the box — save to keep them', 'ok');
              },
            },
          })
        : null,
      button('Save notes', { class: 'btn btn-primary', on: { click: saveNotes } }),
    ]
  ));

  /* ------------------------------------------------------- constants --- */

  const constantInputs = {};
  const constantRows = Object.entries(house.design_constants).map(([name, value]) => {
    const node = input(String(value), { type: 'number', step: 'any' });
    constantInputs[name] = node;
    return el('tr', {}, [
      el('td', { text: name }),
      el('td', { style: 'width:140px' }, [node]),
    ]);
  });

  add('content', card(
    'Design constants',
    table(['Constant', 'Default'], constantRows),
    [button('Save constants', {
      class: 'btn btn-primary',
      on: {
        click: async () => {
          const values = {};
          for (const [name, node] of Object.entries(constantInputs)) {
            const parsed = parseFloat(node.value);
            if (!Number.isNaN(parsed)) values[name] = parsed;
          }
          try {
            await api.settings.update({ design_constants: values });
            toast('Constants saved', 'ok');
          } catch (error) { fail(error); }
        },
      },
    })],
    'Projects inherit these and may override them individually.'
  ));

  /* -------------------------------------------------- numbering scope --- */

  const scope = select(
    [
      ['building', 'One sequence per building'],
      ['building_volume', 'A separate sequence per volume within each building'],
    ],
    house.numbering_scope || 'building'
  );

  add('numbering', card(
    'How numbers are allocated',
    el('div', {}, [
      field('Scope', scope,
        'Per volume means 5.2-00001 and 5.3-00001 can both exist in the same building. ' +
        'The volume sits earlier in the pattern, so the document numbers stay distinct.'),
      el('p', { class: 'help muted', style: 'margin-top:10px' }, [
        'Changing this affects numbers allocated from now on. Schedules already ' +
        'numbered keep the numbers they have.',
      ]),
    ]),
    [button('Save', {
      class: 'btn btn-primary',
      on: {
        click: async () => {
          try {
            await api.settings.update({ numbering_scope: scope.value });
            toast('Numbering scope saved', 'ok');
          } catch (error) { fail(error); }
        },
      },
    })]
  ));

  /* ---------------------------------------------------------- volumes --- */

  // Volume decides the discipline, because an AHU is always ventilation and
  // ventilation is always mechanical. Editable, since a practice's own
  // convention is the one that has to win.
  const disciplines = { ...(house.volume_discipline || {}) };
  const volumeRows = Object.entries(house.volume_lookup).map(([code, label]) => {
    const box = input(disciplines[code] || '', {
      placeholder: '—',
      on: { input: (e) => { disciplines[code] = e.target.value.trim().toUpperCase(); } },
    });
    return el('tr', {}, [
      el('td', { class: 'mono', text: code }),
      el('td', { text: label }),
      el('td', { style: 'width:90px' }, [box]),
    ]);
  });

  add('numbering', card(
    'Volumes and discipline',
    el('div', {}, [
      table(['Code', 'Description', 'Discipline'], volumeRows),
      el('p', { class: 'help muted', style: 'margin-top:10px' }, [
        'A schedule type picks its volume from this list, and the discipline follows ' +
        'from it. Leave a discipline blank to fall back to the project’s own setting. ' +
        'A project that sets a discipline explicitly always wins.',
      ]),
    ]),
    [button('Save disciplines', {
      class: 'btn btn-primary',
      on: {
        click: async () => {
          const cleaned = Object.fromEntries(
            Object.entries(disciplines).filter(([, v]) => v)
          );
          try {
            await api.settings.update({ volume_discipline: cleaned });
            toast('Disciplines saved', 'ok');
          } catch (error) { fail(error); }
        },
      },
    })]
  ));

  add('numbering', card(
    'Suitability codes',
    table(
      ['Code', 'Description'],
      house.status_codes.map(([code, label]) =>
        el('tr', {}, [el('td', { class: 'mono', text: code }), el('td', { text: label })])
      )
    ),
    [],
    'Offered on the revision log and shown on the cover.'
  ));

  mount(page);
}


/* ------------------------------------------------------------ your data --- */

/**
 * Where the record is kept, and how to keep a copy of it.
 *
 * This exists because "I downloaded the update and everything was gone" is the
 * worst thing this tool can do to somebody, and until the database moved out of
 * the source folder it did exactly that to anyone who updated by downloading a
 * fresh copy into a new folder. Nothing had been lost — it was still sitting in
 * the old folder — but there was nowhere to go and look, which is the same
 * thing from where the user is standing.
 */
async function dataSection(host) {
  let storage;
  try {
    storage = await api.settings.storage();
  } catch (error) { host.appendChild(notice(error.message, 'error')); return; }

  const size = storage.size_bytes
    ? `${(storage.size_bytes / 1024 / 1024).toFixed(1)} MB`
    : '—';

  host.appendChild(card(
    'Where your data lives',
    el('div', {}, [
      el('p', { class: 'muted' }, [
        'Your projects, equipment library, branding and every schedule are in one file, ',
        'and it is deliberately not inside the folder you downloaded. Updating the tool — ',
        'by pulling, or by unpacking a new copy somewhere else — leaves it exactly where ',
        'it is.',
      ]),
      el('dl', { class: 'kv' }, [
        el('dt', { text: 'Database' }),
        el('dd', { class: 'mono', text: storage.external ? storage.database_url : storage.database }),
        el('dt', { text: 'Size' }),
        el('dd', { text: size }),
        ...(storage.legacy_copy ? [
          el('dt', { text: 'Older copy' }),
          el('dd', { class: 'mono', text: storage.legacy_copy }),
        ] : []),
      ]),
      storage.legacy_copy
        ? notice(
            'A database from an earlier version is still in the folder you downloaded. It ' +
            'was copied to the location above the first time this version started, so the ' +
            'old one is a spare rather than the live record — check the projects here look ' +
            'right before you delete the folder it is in.',
            'info'
          )
        : null,
      el('p', { class: 'muted tiny' }, [
        `Set ${storage.override_env} to keep it somewhere else — a synced drive, for `,
        'instance, which is also how two machines can share one record.',
      ]),
    ]),
    [
      button('Download a backup', {
        class: 'btn btn-primary',
        title: 'A consistent copy of the whole database, taken safely while it is in use',
        on: { click: () => download('/api/settings/backup.db') },
      }),
    ],
    'To restore one, stop the server and put the file back at the path above as schedul.db.'
  ));
}
