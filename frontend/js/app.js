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
  //: The project the current screen belongs to, so the sidebar can carry its
  //: context rather than making somebody navigate back to find it.
  context: null,
};

/** Tell the sidebar which project the current screen is about. */
export function setContext(context) {
  const same =
    (store.context && store.context.projectId) === (context && context.projectId) &&
    (store.context && store.context.scheduleId) === (context && context.scheduleId);
  store.context = context;
  if (!same) renderNav();
}

const routes = [
  { pattern: /^\/?$/, view: projectsView, nav: 'projects' },
  { pattern: /^\/projects$/, view: projectsView, nav: 'projects' },
  { pattern: /^\/projects\/([^/]+)$/, view: projectView, nav: 'projects' },
  { pattern: /^\/schedules\/([^/]+)$/, view: scheduleView, nav: 'projects' },
  // The register can be opened already narrowed to one project, which is how
  // the sidebar links to it from inside a job.
  { pattern: /^\/register(?:\?(.*))?$/, view: registerView, nav: 'register' },
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
  // A screen that is about a project sets the context itself; anything else
  // clears it, so the sidebar never carries a stale project around.
  if (!/^\/(projects\/|schedules\/)/.test(path)) setContext(null);
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

/**
 * The sidebar.
 *
 * Where you are comes first: while a project is open, its schedules and the
 * tools that act on it sit at the top, because that is what somebody in a
 * project reaches for. Everything else — the register, the shared library, the
 * house standard — is the same wherever you are, and stays below.
 */
function renderNav(section) {
  const nav = clear(document.getElementById('nav'));

  if (store.context && store.context.projectId) nav.appendChild(contextGroup());

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

  const recent = store.projects.filter((p) => p.id !== (store.context || {}).projectId);
  if (recent.length) {
    nav.appendChild(el('div', { class: 'nav-group' }, [
      el('div', { class: 'nav-label', text: 'Recent projects' }),
      ...recent.slice(0, 6).map((p) =>
        el('a', {
          class: 'nav-item nav-project',
          href: `#/projects/${p.id}`,
        }, [
          el('span', { text: p.number || p.name || 'Untitled' }),
          // The building is what somebody remembers about a job of several, and
          // a project number on its own does not say which block they were in.
          buildingLine(p) ? el('span', { class: 'nav-sub', text: buildingLine(p) }) : null,
        ])
      ),
    ]));
  }
}

/** How to describe a project's buildings in one line, or nothing at all. */
function buildingLine(project) {
  const buildings = project.buildings || [];
  if (!buildings.length) return '';
  if (buildings.length === 1) {
    // A single building auto-named after the project number says nothing.
    const only = buildings[0];
    return only === project.number || only === project.name ? '' : only;
  }
  return `${buildings.length} buildings · ${buildings.slice(0, 2).join(', ')}`;
}

/** The open project, its schedules, and the tools that act on it. */
function contextGroup() {
  const { projectId, projectName, building, schedules = [], scheduleId } = store.context;
  const path = currentPath();

  return el('div', { class: 'nav-group nav-context' }, [
    el('div', { class: 'nav-label', text: 'This project' }),
    el('a', {
      class: `nav-item nav-project${path === `/projects/${projectId}` ? ' active' : ''}`,
      href: `#/projects/${projectId}`,
    }, [
      el('span', { text: projectName || 'Project' }),
      building ? el('span', { class: 'nav-sub', text: building }) : null,
    ]),
    schedules.length
      ? el('div', { class: 'nav-schedules' }, schedules.map((s) =>
          el('a', {
            class: `nav-item nav-schedule${s.id === scheduleId ? ' active' : ''}`,
            href: `#/schedules/${s.id}`,
            title: s.title || s.code,
          }, [s.code])
        ))
      : null,
    el('a', {
      class: 'nav-item nav-sub-item',
      href: `#/register?project=${projectId}`,
    }, ['Register for this project']),
  ]);
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
