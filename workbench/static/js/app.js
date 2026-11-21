import { loadSession } from './api.js';
import { h, replaceContent, link, loadError, loading } from './dom.js';
import * as dashboard from './views/dashboard.js';
import * as leads from './views/leads.js';
import * as lead from './views/lead.js';
import * as settings from './views/settings.js';
import * as discover from './views/discover.js';

const APP_NAME = 'Dutch Freelance Workbench';
const main = document.getElementById('main');

const ROUTES = [
  { re: /^\/$/, nav: 'dashboard', render: dashboard.render },
  { re: /^\/leads$/, nav: 'leads', render: leads.renderList },
  { re: /^\/leads\/new$/, nav: 'leads', render: leads.renderCreate },
  { re: /^\/leads\/(\d+)$/, nav: 'leads', render: lead.render, params: (m) => ({ id: m[1] }) },
  { re: /^\/settings$/, nav: 'settings', render: settings.render },
  { re: /^\/discover$/, nav: 'discover', render: discover.render },
];

let navId = 0;
let pendingFlash = null;
let firstRender = true;
let booted = false;

function matchRoute(pathname) {
  for (const route of ROUTES) {
    const m = route.re.exec(pathname);
    if (m) return { route, params: route.params ? route.params(m) : {} };
  }
  return null;
}

function updateNav(active) {
  for (const a of document.querySelectorAll('[data-nav]')) {
    if (a.dataset.nav === active) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
}

export function navigate(url, options) {
  const opts = options || {};
  const target = new URL(url, location.origin);
  const same = target.pathname === location.pathname && target.search === location.search;
  if (opts.flash) pendingFlash = opts.flash;
  if (opts.replace || same) history.replaceState(null, '', target.pathname + target.search);
  else history.pushState(null, '', target.pathname + target.search);
  if (!opts.keepScroll) window.scrollTo(0, 0);
  return renderRoute(true);
}

async function renderRoute(isNavigation) {
  navId += 1;
  const mine = navId;
  const matched = matchRoute(location.pathname);
  const flash = pendingFlash;
  pendingFlash = null;
  const ctx = {
    params: matched ? matched.params : {},
    navigate,
    current: () => mine === navId,
    setTitle: (t) => { if (mine === navId) document.title = t + ' – ' + APP_NAME; },
    takeFlash: () => flash,
  };
  if (!matched) {
    updateNav(null);
    ctx.setTitle('Page not found');
    replaceContent(main, h('h1', { tabindex: '-1' }, 'Page not found'),
      h('p', null, 'This page does not exist.'), h('p', null, link('/', 'Go to the dashboard')));
  } else {
    updateNav(matched.route.nav);
    try {
      await matched.route.render(main, ctx);
    } catch (_err) {
      if (!ctx.current()) return;
      ctx.setTitle('Error');
      replaceContent(main, h('h1', { tabindex: '-1' }, 'Something went wrong'),
        loadError('this page', () => renderRoute(false)));
    }
  }
  if (mine !== navId) return;
  if (isNavigation && !firstRender) focusHeading();
  firstRender = false;
}

function focusHeading() {
  const active = document.activeElement;
  if (active && active !== document.body && active !== main && main.contains(active)) return;
  const h1 = main.querySelector('h1');
  if (h1) {
    if (!h1.hasAttribute('tabindex')) h1.setAttribute('tabindex', '-1');
    h1.focus();
  }
}

function isPlainLeftClick(ev) {
  return ev.button === 0 && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && !ev.altKey;
}

document.addEventListener('click', (ev) => {
  if (ev.defaultPrevented || !isPlainLeftClick(ev)) return;
  const a = ev.target instanceof Element ? ev.target.closest('a[href]') : null;
  if (!a) return;
  if (a.target && a.target !== '_self') return;
  if (a.hasAttribute('download')) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin) return;
  if (!matchRoute(url.pathname)) return;
  if (url.hash && url.pathname === location.pathname && url.search === location.search) return;
  ev.preventDefault();
  navigate(url.pathname + url.search);
});

window.addEventListener('popstate', () => { renderRoute(true); });

async function boot() {
  replaceContent(main, loading());
  const result = await loadSession();
  if (result.outcome !== 'ok') {
    document.title = 'Cannot connect – ' + APP_NAME;
    replaceContent(main, h('h1', { tabindex: '-1' }, 'Cannot connect'),
      loadError('the workbench', () => boot(), 'The local server did not respond with a session.'));
    return;
  }
  if (!booted) booted = true;
  await renderRoute(false);
}

boot();
