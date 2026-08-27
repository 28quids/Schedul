// Entry point: hash routing, the sidebar, and a small shared store.

import { api } from './api.js';
import { clear, el, fail, mount } from './ui.js';

import { projectsView } from './views/projects.js';
import { projectView } from './views/project.js';
import { scheduleView } from './views/schedule.js';
import { registerView } from './views/register.js';
import { catalogueView } from './views/catalogue.js';
import { designerView } from './views/designer.js';
import { libraryView } from './views/library.js';
import { settingsView } from './views/settings.js';
import { changesView } from './views/changes.js';

export const store = {
  projects: [],
  catalogue: [],
  meta: null,
  pdfAvailable: false,
};

const routes = [
  { pattern: /^\/?$/, view: projectsView, nav: 'projects' },
  { pattern: /^\/projects$/, view: projectsView, nav: 'projects' },
  { pattern: /^\/projects\/([^/]+)$/, view: projectView, nav: 'projects' },
  { pattern: /^\/schedules\/([^/]+)$/, view: scheduleView, nav: 'projects' },
  { pattern: /^\/register$/, view: registerView, nav: 'register' },
  { pattern: /^\/changes$/, view: changesView, nav: 'changes' },
  { pattern: /^\/catalogue$/, view: catalogueView, nav: 'catalogue' },
  { pattern: /^\/catalogue\/new$/, view: designerView, nav: 'catalogue' },
  { pattern: /^\/catalogue\/([^/]+)$/, view: designerView, nav: 'catalogue' },
  { pattern: /^\/library$/, view: libraryView, nav: 'library' },
  { pattern: /^\/settings$/, view: settingsView, nav: 'settings' },
];

function currentPath() {
  return (location.hash || '#/').slice(1) || '/';
}

export function go(path) {
  location.hash = `#${path}`;
}

/** Re-render the current route, for after a change that alters navigation. */
export function refresh() {
  render();
}

async function render() {
  const path = currentPath();
  const match = routes.find((r) => r.pattern.test(path));
  renderNav(match ? match.nav : null);

  if (!match) {
    mount(el('div', { class: 'page' }, [
      el('h1', { text: 'Not found' }),
      el('p', { class: 'muted', text: `Nothing lives at ${path}.` }),
    ]));
    return;
  }

  const params = path.match(match.pattern).slice(1).map(decodeURIComponent);
  mount(el('div', { class: 'loading', text: 'Loading…' }));
  try {
    await match.view(...params);
  } catch (error) {
    fail(error);
    mount(el('div', { class: 'page' }, [
      el('h1', { text: 'Something went wrong' }),
      el('p', { class: 'muted', text: error.message || String(error) }),
      el('button', {
        class: 'btn',
        on: { click: () => render() },
      }, ['Try again']),
    ]));
  }
}

function navItem(href, label, extra) {
  const path = currentPath();
  const active = path === href || (href !== '/' && path.startsWith(href));
  return el('a', { class: `nav-item${active ? ' active' : ''}`, href: `#${href}` }, [
    label,
    extra ? el('span', { class: 'muted', text: ` ${extra}` }) : null,
  ]);
}

function renderNav(section) {
  const nav = clear(document.getElementById('nav'));
  nav.appendChild(el('div', { class: 'nav-group' }, [
    navItem('/projects', 'Projects'),
    navItem('/register', 'Register'),
  ]));
  nav.appendChild(el('div', { class: 'nav-group' }, [
    el('div', { class: 'nav-label', text: 'Library' }),
    navItem('/catalogue', 'Schedule types'),
    navItem('/library', 'Equipment'),
  ]));
  nav.appendChild(el('div', { class: 'nav-group' }, [
    el('div', { class: 'nav-label', text: 'Setup' }),
    navItem('/settings', 'House standard'),
    navItem('/changes', 'Changes'),
  ]));

  if (store.projects.length) {
    nav.appendChild(el('div', { class: 'nav-group' }, [
      el('div', { class: 'nav-label', text: 'Recent projects' }),
      ...store.projects.slice(0, 6).map((p) =>
        navItem(`/projects/${p.id}`, p.number || p.name || 'Untitled')
      ),
    ]));
  }
}

async function boot() {
  try {
    const health = await api.health();
    store.pdfAvailable = Boolean(health.pdf_available);
  } catch {
    store.pdfAvailable = false;
  }

  const badge = document.getElementById('pdf-status');
  if (store.pdfAvailable) {
    badge.className = 'pill pill-green';
    badge.textContent = 'PDF ready';
    badge.title = 'LibreOffice was found, so PDF export is available.';
  } else {
    badge.className = 'pill pill-amber';
    badge.textContent = 'Excel only';
    badge.title =
      'LibreOffice was not found, so PDF export is unavailable. Excel export works, ' +
      'and prints to PDF from Excel.';
  }

  try {
    store.projects = await api.projects.list();
  } catch { /* the projects view reports this properly */ }

  window.addEventListener('hashchange', render);
  await render();
}

boot();
