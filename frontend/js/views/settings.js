// The house standard: everything that varies between practices.
//
// Keeping it in one place is what makes a second practice a profile rather than
// a fork, and it is why nothing company-specific is hardcoded anywhere else.

import { api } from '../api.js';
import {
  button, card, clear, el, fail, field, input, mount, notice, pill, select, table, toast,
} from '../ui.js';

export async function settingsView() {
  const settings = await api.settings.read();
  const house = settings.house_standard;

  const page = el('div', { class: 'page' }, [
    el('header', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: 'House standard' }),
        el('div', {
          class: 'sub',
          text: `${settings.organisation.name} · ISO 19650 field structure, with your token values and wording.`,
        }),
      ]),
    ]),
  ]);

  /* -------------------------------------------------------- numbering --- */

  const pattern = input(house.naming.pattern, { class: 'mono' });
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

  if (settings.naming_problems.length) {
    problemBox.appendChild(notice('The pattern has problems:', 'error', settings.naming_problems));
  }

  const saveNaming = async () => {
    try {
      const result = await api.settings.update({
        naming: { ...house.naming, pattern: pattern.value, tokens },
      });
      clear(problemBox);
      if (result.naming_problems.length) {
        problemBox.appendChild(notice('The pattern has problems:', 'error', result.naming_problems));
      } else {
        toast('Numbering saved', 'ok');
      }
    } catch (error) { fail(error); }
  };

  page.appendChild(card(
    'Document numbering',
    el('div', {}, [
      problemBox,
      field('Pattern', pattern, 'Every {token} must be defined below.'),
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

  const notesArea = el('textarea', {
    rows: 9,
    value: house.general_notes.join('\n'),
  });

  page.appendChild(card(
    'General notes',
    el('div', {}, [
      el('p', { class: 'muted tiny' }, [
        'One per line. These print at the top of every schedule, before any notes specific ' +
        'to the equipment type.',
      ]),
      notesArea,
    ]),
    [button('Save notes', {
      class: 'btn btn-primary',
      on: {
        click: async () => {
          try {
            await api.settings.update({
              general_notes: notesArea.value.split('\n').map((l) => l.trim()).filter(Boolean),
            });
            toast('Notes saved', 'ok');
          } catch (error) { fail(error); }
        },
      },
    })]
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

  page.appendChild(card(
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

  /* ---------------------------------------------------------- volumes --- */

  page.appendChild(card(
    'Volume codes',
    table(
      ['Code', 'Description'],
      Object.entries(house.volume_lookup).map(([code, label]) =>
        el('tr', {}, [el('td', { class: 'mono', text: code }), el('td', { text: label })])
      )
    ),
    [],
    'A schedule type picks its volume from this list, so an AHU is always ventilation.'
  ));

  page.appendChild(card(
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
