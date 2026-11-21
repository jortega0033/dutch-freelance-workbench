// Discover: browse the bundled fictional catalog and save an item as a lead.
// Browsing only reads. Each "Save as lead" is one pending save (see forms.js);
// the server keeps one lead per catalog item, so a retry or a second tab gets
// the existing lead back instead of a duplicate.

import { apiGet, getSession, loadSession, setHasProfile } from '../api.js';
import { h, replaceContent, link, badge, loading, loadError, message, selectField, announce } from '../dom.js';
import { attachSave } from '../forms.js';
import { OPTIONS, optionLabel } from './leads.js';

const FILTER_KEYS = ['skill', 'engagement', 'country', 'mode'];
const ENGAGEMENT = [['hourly-contract', 'Hourly contract'], ['fixed-scope', 'Fixed scope'], ['retainer', 'Retainer']];

function engagementLabel(value) {
  const found = ENGAGEMENT.find((o) => o[0] === value);
  return found ? found[1] : String(value);
}

function currentFilters() {
  const params = new URLSearchParams(location.search);
  const filters = {};
  for (const k of FILTER_KEYS) filters[k] = params.get(k) || '';
  return filters;
}

function queryString(filters) {
  const qs = new URLSearchParams();
  for (const k of FILTER_KEYS) if (filters[k]) qs.set(k, filters[k]);
  const s = qs.toString();
  return s ? '?' + s : '';
}

export async function render(main, ctx) {
  ctx.setTitle('Discover');
  const filters = currentFilters();
  const body = h('div', null, loading());
  replaceContent(main,
    h('h1', { tabindex: '-1' }, 'Discover'),
    h('p', { class: 'muted' }, 'Browse a small bundled catalog of fictional opportunities. Browsing never adds leads; only "Save as lead" does.'),
    body);
  await load(body, ctx, filters, null);
}

async function load(body, ctx, filters, flash) {
  const [result, session] = await Promise.all([apiGet('/api/v1/catalog' + queryString(filters)), loadSession()]);
  if (!ctx.current()) return;
  if (session.outcome === 'ok') setHasProfile(getSession().hasProfile);
  const retry = () => { replaceContent(body, loading()); load(body, ctx, filters, flash); };

  if (result.error && result.error.code === 'CATALOG_UNAVAILABLE') {
    replaceContent(body, message('warn',
      h('p', { id: 'catalog-unavailable' }, 'The fictional catalog could not be loaded right now.'),
      h('p', null, 'You can still add a lead by hand.'),
      h('p', { class: 'actions' },
        link('/leads/new', 'Add a lead manually', { class: 'button', id: 'manual-capture-link' }),
        h('button', { type: 'button', class: 'secondary', name: 'retry-load', onclick: retry }, 'Try again'))));
    return;
  }
  if (result.outcome === 'validation') {
    replaceContent(body, message('error',
      h('p', { id: 'invalid-filters' }, 'These catalog filters are not valid.'),
      h('p', null, link('/discover', 'Clear filters'))));
    return;
  }
  if (result.outcome !== 'ok') {
    replaceContent(body, loadError('the catalog', retry, result.network ? 'The server could not be reached.' : ''));
    return;
  }

  const data = result.data;
  const parts = [filterForm(ctx, filters, data.facets)];
  if (!getSession().hasProfile) {
    parts.push(message('info',
      h('p', { id: 'profile-required' }, 'Save your display label in Settings before saving catalog items as leads.'),
      h('p', null, link('/settings', 'Go to Settings'))));
  }
  const results = h('div', { id: 'catalog-results' });
  parts.push(results);
  replaceContent(body, ...parts);

  const items = data.items || [];
  if (!items.length) {
    replaceContent(results, message('info', h('p', { id: 'no-matches' }, 'No catalog items match these filters.'),
      h('p', null, link('/discover', 'Clear filters'))));
    return;
  }
  replaceContent(results,
    h('p', { id: 'result-count', role: 'status' },
      items.length + ' of ' + data.catalog_total + ' fictional ' + (data.catalog_total === 1 ? 'opportunity' : 'opportunities')),
    h('ul', { class: 'row-list', id: 'catalog-list' }, items.map((item) => itemView(item, ctx, () => load(body, ctx, filters, null)))));
  if (flash) {
    const target = document.getElementById(flash.focusId);
    announce(flash.text);
    if (target) target.focus();
  }
}

function filterForm(ctx, filters, facets) {
  const f = 'catalog-filter-form';
  const form = h('form', { id: f, class: 'form-card', method: 'get', action: '/discover', novalidate: true, 'aria-labelledby': 'catalog-filter-heading' });
  const skills = (facets && facets.skills ? facets.skills : []).map((s) => [s, s]);
  const skill = selectField({ form: f, name: 'skill', label: 'Skill', value: filters.skill, options: skills, anyLabel: 'Any skill' });
  const engagement = selectField({ form: f, name: 'engagement', label: 'Engagement type', value: filters.engagement, options: ENGAGEMENT, anyLabel: 'Any engagement type' });
  const country = selectField({ form: f, name: 'country', label: 'Counterparty country', value: filters.country, options: OPTIONS.country, anyLabel: 'Any country' });
  const mode = selectField({ form: f, name: 'mode', label: 'Work mode', value: filters.mode, options: OPTIONS.mode, anyLabel: 'Any work mode' });
  form.append(
    h('h2', { id: 'catalog-filter-heading' }, 'Filter the catalog'),
    h('div', { class: 'filter-grid' }, skill.wrap, engagement.wrap, country.wrap, mode.wrap),
    h('div', { class: 'actions' },
      h('button', { type: 'submit' }, 'Apply filters'),
      link('/discover', 'Clear filters', { class: 'button secondary', id: 'clear-catalog-filters' })));
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    ctx.navigate('/discover' + queryString({
      skill: skill.input.value, engagement: engagement.input.value, country: country.input.value, mode: mode.input.value,
    }));
  });
  return form;
}

function openLeadLink(item, lead) {
  return link('/leads/' + lead.id, 'Open lead', { class: 'button', id: 'open-' + item.key, 'aria-label': 'Open lead: ' + item.title });
}

function itemView(item, ctx, reload) {
  const headingId = 'title-' + item.key;
  const li = h('li', { class: 'row-item catalog-item', data: { catalogKey: item.key }, 'aria-labelledby': headingId },
    h('h3', { id: headingId, class: 'row-title' }, item.title),
    h('div', null, item.organisation),
    h('div', { class: 'row-meta' },
      badge(engagementLabel(item.engagement_type)),
      badge('Country: ' + optionLabel('country', item.country_tag)),
      badge('Work mode: ' + optionLabel('mode', item.work_mode))),
    h('div', { class: 'muted' }, 'Skills: ' + item.skills.join(', ')),
    h('p', { class: 'catalog-summary' }, item.summary));

  if (item.saved_lead) {
    li.appendChild(h('p', { class: 'actions' }, h('span', { class: 'badge' }, 'Saved as a lead'), ' ', openLeadLink(item, item.saved_lead)));
    return li;
  }
  if (!getSession().hasProfile) {
    li.appendChild(h('p', { class: 'muted' }, 'Set up your profile to save this item.'));
    return li;
  }
  const formId = 'save-' + item.key;
  const form = h('form', { id: formId, class: 'catalog-save', novalidate: true, 'aria-label': 'Save ' + item.title + ' as a lead' },
    h('div', { class: 'actions' }, h('button', { type: 'submit', name: 'save', 'aria-describedby': headingId }, 'Save as lead')));
  li.appendChild(form);
  attachSave({
    form, controls: {}, aliases: {}, method: 'POST',
    path: () => '/api/v1/catalog/items/' + encodeURIComponent(item.key) + '/lead',
    build: () => ({ body: {} }),
    conflictMessage: 'This item could not be saved.',
    currentPath: () => '/api/v1/catalog',
    adoptVersion() {},
    describeCurrent() { return h('p', null, ''); },
    onDiscard: reload,
    async onSuccess(result) {
      const data = result.data || {};
      let text;
      if (result.replayed) text = 'Saved "' + item.title + '" as a lead. This save had already been recorded; nothing was duplicated.';
      else if (data.created === true) text = 'Saved "' + item.title + '" as a lead.';
      else text = '"' + item.title + '" was already saved as a lead; no duplicate was created.';
      announce(text);
      // show the current state from the server, with focus on this item's Open lead link
      const body = document.getElementById('catalog-results')?.parentElement;
      if (body) await load(body, ctx, currentFilters(), { text, focusId: 'open-' + item.key });
    },
  });
  return li;
}
