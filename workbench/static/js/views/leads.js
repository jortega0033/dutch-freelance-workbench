import { apiGet, loadSession, getSession, setHasProfile } from '../api.js';
import { h, replaceContent, link, badge, loading, loadError, message, textField, selectField, announce, clearErrors, showErrors } from '../dom.js';
import { attachSave, chars } from '../forms.js';
import { stageLabel, bucketLabel } from './dashboard.js';

export const OPTIONS = {
  stage: [['new', 'New'], ['researching', 'Researching'], ['contacted', 'Contacted']],
  country: [['NL', 'Netherlands'], ['other', 'Other'], ['unknown', 'Unknown']],
  mode: [['remote', 'Remote'], ['hybrid', 'Hybrid'], ['onsite', 'On-site'], ['unknown', 'Unknown']],
  source: [['manual', 'Manual'], ['direct', 'Direct'], ['platform', 'Platform'], ['referral', 'Referral'], ['other', 'Other']],
  bucket: [['overdue', 'Overdue'], ['today', 'Today'], ['upcoming', 'Upcoming'], ['no_action', 'No action']],
};

export function optionLabel(group, value) {
  const found = OPTIONS[group].find((o) => o[0] === value);
  return found ? found[1] : String(value);
}

const FILTER_KEYS = ['q', 'stage', 'country', 'mode', 'source', 'bucket'];
const LIST_KEY = 'dfw:last-list';

export function lastListUrl() {
  try {
    const v = sessionStorage.getItem(LIST_KEY);
    if (v && (v === '/leads' || v.startsWith('/leads?'))) return v;
  } catch (_e) { /* storage unavailable */ }
  return '/leads';
}

function rememberList(url) {
  try { sessionStorage.setItem(LIST_KEY, url); } catch (_e) { /* ignore */ }
}

export async function renderList(main, ctx) {
  ctx.setTitle('Leads');
  const params = new URLSearchParams(location.search);
  const filters = {};
  for (const k of FILTER_KEYS) filters[k] = params.get(k) || '';
  rememberList(location.pathname + location.search);

  const form = h('form', { id: 'lead-filter-form', class: 'form-card', method: 'get', action: '/leads', novalidate: true, 'aria-labelledby': 'filter-heading' });
  const q = textField({ form: 'lead-filter-form', name: 'q', label: 'Search label or source', value: filters.q, type: 'search' });
  const stage = selectField({ form: 'lead-filter-form', name: 'stage', label: 'Stage', value: filters.stage, options: OPTIONS.stage, anyLabel: 'Any stage' });
  const country = selectField({ form: 'lead-filter-form', name: 'country', label: 'Country', value: filters.country, options: OPTIONS.country, anyLabel: 'Any country' });
  const mode = selectField({ form: 'lead-filter-form', name: 'mode', label: 'Work mode', value: filters.mode, options: OPTIONS.mode, anyLabel: 'Any work mode' });
  const source = selectField({ form: 'lead-filter-form', name: 'source', label: 'Source channel', value: filters.source, options: OPTIONS.source, anyLabel: 'Any source' });
  const bucket = selectField({ form: 'lead-filter-form', name: 'bucket', label: 'Follow-up', value: filters.bucket, options: OPTIONS.bucket, anyLabel: 'Any follow-up' });
  form.appendChild(h('h2', { id: 'filter-heading' }, 'Filter leads'));
  form.appendChild(h('div', { class: 'filter-grid' }, q.wrap, stage.wrap, country.wrap, mode.wrap, source.wrap, bucket.wrap));
  form.appendChild(h('div', { class: 'actions' },
    h('button', { type: 'submit' }, 'Apply filters'),
    link('/leads', 'Clear filters', { class: 'button secondary', id: 'clear-filters' })));
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const next = new URLSearchParams();
    const values = { q: q.input.value.trim(), stage: stage.input.value, country: country.input.value, mode: mode.input.value, source: source.input.value, bucket: bucket.input.value };
    for (const k of FILTER_KEYS) if (values[k]) next.set(k, values[k]);
    const qs = next.toString();
    ctx.navigate('/leads' + (qs ? '?' + qs : ''));
  });

  const results = h('div', { id: 'lead-results', 'aria-live': 'off' }, loading());
  replaceContent(main,
    h('h1', { tabindex: '-1' }, 'Leads'),
    h('p', null, link('/leads/new', 'Add a lead', { class: 'button primary', id: 'add-lead-link' })),
    form, results);

  await loadResults(results, filters, ctx, form, { q, stage, country, mode, source, bucket });
}

async function loadResults(results, filters, ctx, form, controls) {
  const qs = new URLSearchParams();
  for (const k of FILTER_KEYS) if (filters[k]) qs.set(k, filters[k]);
  const query = qs.toString();
  const result = await apiGet('/api/v1/leads' + (query ? '?' + query : ''));
  if (!ctx.current()) return;
  if (result.outcome === 'validation') {
    const fields = (result.error && result.error.fields) || {};
    const list = Object.entries(fields).map(([k, v]) => ({ name: controls[k] ? k : null, text: String(v) }));
    if (!list.length) list.push({ name: null, text: result.error.message || 'These filters are not valid.' });
    showErrors(form, controls, list, 'These filters are not valid');
    replaceContent(results, message('error', h('p', null, 'The list was not loaded because the filters are not valid.'), h('p', null, link('/leads', 'Clear filters'))));
    return;
  }
  clearErrors(form);
  if (result.outcome !== 'ok') {
    replaceContent(results, loadError('the leads', () => { replaceContent(results, loading()); loadResults(results, filters, ctx, form, controls); }, result.network ? 'The server could not be reached.' : ''));
    return;
  }
  const data = result.data;
  const leads = data.leads || [];
  const active = FILTER_KEYS.some((k) => filters[k]);
  if (!leads.length) {
    if (active) {
      replaceContent(results, message('info', h('p', { id: 'no-matches' }, 'No leads match these filters.'), h('p', null, link('/leads', 'Clear filters'))));
    } else {
      replaceContent(results, message('info', h('p', { id: 'no-leads' }, 'No leads yet.'), h('p', null, link('/leads/new', 'Add your first lead'))));
    }
    return;
  }
  const items = leads.map((lead) => {
    const meta = h('div', { class: 'row-meta' },
      badge('Stage: ' + stageLabel(lead.stage)),
      badge('Follow-up: ' + bucketLabel(lead.bucket), lead.bucket === 'overdue' ? 'badge-overdue' : (lead.bucket === 'today' ? 'badge-today' : '')),
      h('span', { class: 'muted' }, optionLabel('country', lead.country_tag) + ' · ' + optionLabel('mode', lead.work_mode) + ' · ' + optionLabel('source', lead.source_channel) + (lead.source_label ? ' (' + lead.source_label + ')' : '')));
    const action = lead.next_action
      ? h('div', null, 'Action: ' + lead.next_action.text + ' — due ' + lead.next_action.due_date)
      : h('div', { class: 'muted' }, 'No next action set.');
    return h('li', { class: 'row-item', data: { leadId: lead.id } }, h('div', { class: 'row-title' }, link('/leads/' + lead.id, lead.label)), meta, action);
  });
  replaceContent(results,
    h('p', { id: 'result-count', role: 'status' }, leads.length === 1 ? '1 lead' : leads.length + ' leads'),
    h('ul', { class: 'row-list', id: 'lead-list' }, items));
}

// ---- create ----

export async function renderCreate(main, ctx) {
  ctx.setTitle('Add a lead');
  const body = h('div', null, loading());
  replaceContent(main, h('h1', { tabindex: '-1' }, 'Add a lead'), body);
  const s = await loadSession();
  if (!ctx.current()) return;
  if (s.outcome !== 'ok') {
    replaceContent(body, loadError('the form', () => renderCreate(main, ctx), 'The server could not be reached.'));
    return;
  }
  setHasProfile(getSession().hasProfile);
  if (!getSession().hasProfile) {
    replaceContent(body, message('info',
      h('p', { id: 'profile-required' }, 'Save your display label in Settings before adding leads.'),
      h('p', null, link('/settings', 'Go to Settings', { class: 'button' }))));
    return;
  }
  const f = 'lead-create-form';
  const form = h('form', { id: f, novalidate: true, class: 'form-card' });
  const label = textField({ form: f, name: 'label', label: 'Opportunity label', hint: 'Required, 1–120 characters.', required: true });
  const country = selectField({ form: f, name: 'country_tag', label: 'Country', value: 'unknown', options: OPTIONS.country });
  const mode = selectField({ form: f, name: 'work_mode', label: 'Work mode', value: 'unknown', options: OPTIONS.mode });
  const channel = selectField({ form: f, name: 'source_channel', label: 'Source channel', value: 'manual', options: OPTIONS.source });
  const sourceLabel = textField({ form: f, name: 'source_label', label: 'Source label (optional)', hint: 'For example the platform or contact name. 1–120 characters if given.' });
  form.append(label.wrap, country.wrap, mode.wrap, channel.wrap, sourceLabel.wrap,
    h('div', { class: 'actions' }, h('button', { type: 'submit', class: 'primary', name: 'save' }, 'Save lead'), link('/leads', 'Cancel', { class: 'button secondary' })));
  const controls = { label, country_tag: country, work_mode: mode, source_channel: channel, source_label: sourceLabel };
  attachSave({
    form, controls, aliases: {}, method: 'POST',
    path: () => '/api/v1/leads',
    conflictMessage: 'The lead could not be saved.',
    currentPath: () => '/api/v1/leads',
    build() {
      const errors = [];
      const l = label.input.value.trim();
      const sl = sourceLabel.input.value.trim();
      if (chars(l) < 1 || chars(l) > 120) errors.push({ name: 'label', text: 'Enter 1–120 characters.' });
      if (chars(sl) > 120) errors.push({ name: 'source_label', text: 'Enter 1–120 characters, or leave it empty.' });
      if (errors.length) return { errors };
      const payload = { label: l, country_tag: country.input.value, work_mode: mode.input.value, source_channel: channel.input.value };
      if (sl) payload.source_label = sl;
      return { body: payload };
    },
    adoptVersion() {},
    describeCurrent() { return h('p', null, ''); },
    onDiscard() { renderCreate(main, ctx); },
    async onSuccess(result) {
      const id = result.data && result.data.lead && result.data.lead.id;
      announce('Lead created.');
      if (id) ctx.navigate('/leads/' + id, { flash: 'Lead created.' });
      else ctx.navigate('/leads');
    },
  });
  replaceContent(body, form);
}
