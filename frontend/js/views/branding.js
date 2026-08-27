// The organisation's branding: what its documents look like, and what they say.
//
// A configuration screen rather than a document designer, and deliberately so.
// The hand-made branded originals carry drawing objects that cannot be
// round-tripped through a generator, so anything pretending to be a canvas would
// either lose them or lie about what it produced. What is on offer here is the
// set of decisions the renderer can carry out honestly: a logo, a font, a
// palette, and which fields the cover and revision page show and in what order.
//
// The preview beside it is the renderer's own resolution of those decisions, not
// a mock-up — so what it lists is what would be produced.

import { api } from '../api.js';
import {
  button, card, clear, debounce, el, fail, field, input, notice, pill, select,
  toast,
} from '../ui.js';

let b = null;

/** Render the branding section into a host element, for the settings page. */
export async function brandingSection(host) {
  const data = await api.settings.branding();
  b = {
    branding: data.branding,
    fonts: data.fonts,
    coverFields: data.cover_fields,
    revisionFields: data.revision_fields,
    preview: data.preview,
    problems: [],
  };
  render(host);
}

const refresh = debounce(async (host) => {
  try {
    const result = await api.settings.previewBranding(b.branding);
    b.preview = result.preview;
    b.problems = result.problems;
    renderPreview();
    renderProblems();
  } catch (error) { fail(error); }
}, 300);

function set(key, value, host) {
  b.branding[key] = value;
  refresh(host);
}

function render(host) {
  clear(host);

  host.appendChild(el('div', { id: 'branding-problems' }));

  host.appendChild(card(
    'Logo and type',
    el('div', {}, [
      el('div', { class: 'grid-3' }, [
        field(
          'Cover font',
          select(b.fonts, b.branding.cover_font, {
            on: { change: (e) => { set('cover_font', e.target.value, host); } },
          }),
          'The cover and the revision page.'
        ),
        field(
          'Schedule font',
          select(b.fonts, b.branding.schedule_font, {
            on: { change: (e) => { set('schedule_font', e.target.value, host); } },
          }),
          'The schedule grid itself.'
        ),
        field(
          'Title size',
          input(String(b.branding.title_size), {
            type: 'number', min: '8', max: '60',
            on: { input: (e) => { set('title_size', parseInt(e.target.value, 10) || 30, host); } },
          }),
          'Points, for the project name on the cover.'
        ),
      ]),
      el('p', { class: 'help muted', style: 'margin-top:10px' }, [
        'Only fonts that ship with Windows, macOS and LibreOffice are offered. A font the ' +
        'recipient does not have is substituted by whatever their machine decides, which is ' +
        'how a careful layout becomes a ragged one on somebody else’s screen.',
      ]),
      logoRow(host),
    ]),
    [saveButton(host)]
  ));

  host.appendChild(card(
    'Colours',
    el('div', {}, [
      el('div', { class: 'grid-4' }, [
        colourField('title', 'Title', 'The project name on the cover.', host),
        colourField('accent', 'Accent', 'The schedule title under it.', host),
        colourField('header', 'Header shading', 'Column headers on an issued sheet.', host),
        colourField('rule', 'Rules', 'Cell borders and lines.', host),
      ]),
      el('p', { class: 'help muted', style: 'margin-top:10px' }, [
        'These apply to the front cover, the revision page and the issued schedule. The ' +
        'editor keeps its own working colours — blue on yellow you type, green from the ' +
        'library, black calculated — because those carry meaning while a schedule is ' +
        'being filled in.',
      ]),
    ]),
    [saveButton(host)]
  ));

  host.appendChild(card(
    'What the cover shows',
    fieldChooser('cover', b.coverFields, host),
    [saveButton(host)],
    'Hide what a job does not need — Building on a single-building project, for instance. ' +
    'Drag to reorder.'
  ));

  host.appendChild(card(
    'What the revision page shows',
    fieldChooser('revision', b.revisionFields, host),
    [saveButton(host)],
    'Some rows are read by the cover and the Metadata sheet, so they cannot be hidden: ' +
    'a document with a broken reference in it is worse than a longer page.'
  ));

  host.appendChild(card(
    'Cover slots',
    el('div', { class: 'grid-2' }, [
      field('Subtitle', input(b.branding.cover_subtitle, {
        placeholder: 'Mechanical Services',
        on: { input: (e) => { set('cover_subtitle', e.target.value, host); } },
      }), 'Printed under the title block.'),
      field('Footer', input(b.branding.cover_footer, {
        placeholder: 'Practice name · Registered in England 000000',
        on: { input: (e) => { set('cover_footer', e.target.value, host); } },
      }), 'A single line at the foot of the cover.'),
    ]),
    [saveButton(host)]
  ));

  host.appendChild(card('Preview', el('div', { id: 'branding-preview' }), [], (
    'The renderer’s own resolution of these settings, so what it lists is what would be ' +
    'produced.'
  )));

  renderPreview();
  renderProblems();
}

function saveButton(host) {
  return button('Save branding', {
    class: 'btn btn-primary',
    on: {
      click: async () => {
        try {
          await api.settings.update({ branding: b.branding });
          toast('Branding saved — it applies to every document from now on', 'ok');
          brandingSection(host);
        } catch (error) { fail(error); }
      },
    },
  });
}

function colourField(key, label, hint, host) {
  const hex = b.branding.palette[key] || '000000';
  const swatch = el('input', {
    type: 'color',
    value: `#${hex}`,
    on: {
      input: (e) => {
        b.branding.palette = { ...b.branding.palette, [key]: e.target.value.slice(1).toUpperCase() };
        text.value = e.target.value.slice(1).toUpperCase();
        refresh(host);
      },
    },
  });
  const text = input(hex, {
    class: 'mono',
    on: {
      input: (e) => {
        const value = e.target.value.replace(/[^0-9a-fA-F]/g, '').toUpperCase();
        b.branding.palette = { ...b.branding.palette, [key]: value };
        if (value.length === 6) swatch.value = `#${value}`;
        refresh(host);
      },
    },
  });
  return field(label, el('div', { class: 'colour-field' }, [swatch, text]), hint);
}

function logoRow(host) {
  const file = el('input', {
    type: 'file',
    accept: 'image/png,image/jpeg',
    on: {
      change: async (e) => {
        const chosen = e.target.files && e.target.files[0];
        if (!chosen) return;
        if (chosen.size > 1024 * 1024) {
          fail(new Error('That logo is over 1MB. A cover logo needs far less than that.'));
          return;
        }
        const reader = new FileReader();
        reader.onload = () => { set('logo', String(reader.result), host); renderLogo(); };
        reader.readAsDataURL(chosen);
      },
    },
  });

  const preview = el('div', { id: 'logo-preview' });
  const renderLogo = () => {
    clear(preview);
    if (b.branding.logo) {
      preview.appendChild(el('img', { src: b.branding.logo, class: 'logo-thumb' }));
      preview.appendChild(button('Remove', {
        class: 'btn btn-sm btn-danger',
        on: { click: () => { set('logo', '', host); renderLogo(); } },
      }));
    } else {
      preview.appendChild(el('span', { class: 'muted tiny', text: 'No logo set.' }));
    }
  };
  renderLogo();

  return el('div', { style: 'margin-top:14px' }, [
    el('div', { class: 'grid-3' }, [
      field('Logo', file, 'PNG or JPEG. It is written onto the cover as a real image.'),
      field('Size', input(String(b.branding.logo_scale), {
        type: 'number', step: '0.1', min: '0.1', max: '4',
        on: { input: (e) => { set('logo_scale', parseFloat(e.target.value) || 1, host); } },
      }), '1 is its natural size.'),
      field('Placed at', input(b.branding.logo_anchor, {
        class: 'mono',
        on: { input: (e) => { set('logo_anchor', e.target.value.toUpperCase(), host); } },
      }), 'A cell on the cover, e.g. A1 or G2.'),
    ]),
    preview,
  ]);
}

/**
 * Show, hide and reorder one page's fields.
 *
 * A field the workbook reads by formula is listed but fixed. Offering a switch
 * that produces a broken document would be worse than not offering it.
 */
function fieldChooser(page, fields, host) {
  const shownKey = `${page}_fields`;
  const orderKey = `${page}_order`;
  const list = el('div', { class: 'field-chooser' });

  const currentOrder = () => {
    const stored = b.branding[orderKey] || [];
    const known = fields.map((f) => f.key);
    return [...stored.filter((k) => known.includes(k)),
            ...known.filter((k) => !stored.includes(k))];
  };

  const render = () => {
    clear(list);
    const order = currentOrder();
    for (const key of order) {
      const spec = fields.find((f) => f.key === key);
      if (!spec) continue;
      const shown = (b.branding[shownKey] || {})[key] !== false;

      const row = el('div', {
        class: `chooser-row${spec.optional ? '' : ' fixed'}`,
        draggable: spec.optional,
        dataset: { key },
      }, [
        el('span', { class: 'col-grip', text: spec.optional ? '⠿' : ' ' }),
        spec.optional
          ? el('input', {
              type: 'checkbox',
              checked: shown,
              on: {
                change: (e) => {
                  b.branding[shownKey] = { ...(b.branding[shownKey] || {}) };
                  if (e.target.checked) delete b.branding[shownKey][key];
                  else b.branding[shownKey][key] = false;
                  refresh(host);
                },
              },
            })
          : el('span', { class: 'fixed-mark', title: 'The workbook reads this row' }, ['●']),
        el('span', { text: spec.label }),
        spec.hint ? el('span', { class: 'muted tiny', text: spec.hint }) : null,
        spec.optional ? null : pill('always shown', 'quiet'),
      ]);

      if (spec.optional) wireReorder(row, key, order, orderKey, render, host);
      list.appendChild(row);
    }
  };
  render();
  return list;
}

let draggingKey = null;

function wireReorder(row, key, order, orderKey, render, host) {
  row.addEventListener('dragstart', (event) => {
    draggingKey = key;
    row.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', key);
  });
  row.addEventListener('dragend', () => {
    draggingKey = null;
    row.classList.remove('dragging');
  });
  row.addEventListener('dragover', (event) => {
    if (draggingKey && draggingKey !== key) event.preventDefault();
  });
  row.addEventListener('drop', (event) => {
    event.preventDefault();
    if (!draggingKey || draggingKey === key) return;
    const next = order.filter((k) => k !== draggingKey);
    next.splice(next.indexOf(key), 0, draggingKey);
    b.branding[orderKey] = next;
    render();
    refresh(host);
  });
}

/* -------------------------------------------------------------- preview --- */

function renderProblems() {
  const box = document.getElementById('branding-problems');
  if (!box) return;
  clear(box);
  if (b.problems.length) {
    box.appendChild(notice('This branding cannot be saved yet:', 'error', b.problems));
  }
}

function renderPreview() {
  const box = document.getElementById('branding-preview');
  if (!box) return;
  clear(box);

  const p = b.preview;
  const titleColour = `#${(p.palette || {}).title || '4D4D4D'}`;
  const accent = `#${(p.palette || {}).accent || '009DF0'}`;

  const cover = el('div', { class: 'doc-preview' }, [
    p.has_logo
      ? el('img', { src: b.branding.logo, class: 'doc-logo' })
      : null,
    el('div', { class: 'doc-fields' }, p.cover.map((f) =>
      el('div', {}, [
        el('div', { class: 'doc-label', text: f.label }),
        el('div', { class: 'doc-value', text: '—' }),
      ])
    )),
    el('div', {
      class: 'doc-title',
      style: `color:${titleColour};font-family:${p.cover_font};font-size:${Math.min(p.title_size, 28)}px`,
      text: 'PROJECT NAME',
    }),
    el('div', {
      class: 'doc-title',
      style: `color:${accent};font-family:${p.cover_font};font-size:${Math.min(p.title_size, 28)}px`,
      text: 'SCHEDULE TITLE',
    }),
    p.cover_subtitle
      ? el('div', { class: 'doc-sub', style: `font-family:${p.cover_font}`, text: p.cover_subtitle })
      : null,
    p.cover_footer
      ? el('div', { class: 'doc-footer', style: `font-family:${p.cover_font}`, text: p.cover_footer })
      : null,
  ]);

  const revision = el('div', { class: 'doc-preview' }, [
    el('div', {
      class: 'doc-title',
      style: `color:${titleColour};font-family:${p.cover_font};font-size:18px`,
      text: 'REVISION PAGE',
    }),
    el('table', { class: 'doc-table' }, p.revision.map((f) =>
      el('tr', {}, [
        el('td', { class: 'doc-label', text: f.label }),
        el('td', { class: 'doc-value', text: '—' }),
      ])
    )),
  ]);

  box.appendChild(el('div', { class: 'preview-pair' }, [
    el('div', {}, [el('div', { class: 'muted tiny', text: 'Front cover' }), cover]),
    el('div', {}, [el('div', { class: 'muted tiny', text: 'Revision page' }), revision]),
  ]));
}
