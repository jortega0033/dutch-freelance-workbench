import { apiGet, getSession, setHasProfile } from '../api.js';
import { h, replaceContent, link, badge, loading, loadError, message } from '../dom.js';

const GROUPS = [
  ['overdue', 'Overdue', true],
  ['today', 'Today', true],
  ['upcoming', 'Upcoming', false],
  ['no_action', 'No action', false],
];

export function bucketLabel(bucket) {
  const found = GROUPS.find((g) => g[0] === bucket);
  return found ? found[1] : String(bucket);
}

export function stageLabel(stage) {
  const labels = { new: 'New', researching: 'Researching', contacted: 'Contacted' };
  return labels[stage] || String(stage);
}

function row(item, group) {
  const meta = h('div', { class: 'row-meta' }, badge('Stage: ' + stageLabel(item.stage)));
  const body = [h('div', { class: 'row-title' }, link('/leads/' + item.id, item.label)), meta];
  if (item.next_action) {
    body.push(h('div', null, 'Action: ' + item.next_action.text));
    body.push(h('div', null, 'Due: ' + item.next_action.due_date));
  } else {
    body.push(h('div', { class: 'muted' }, 'No next action set.'));
  }
  return h('li', { class: 'row-item', data: { leadId: item.id } }, ...body);
}

function groupSection(key, title, prominent, data) {
  const rows = (data.groups && data.groups[key]) || [];
  const count = data.counts && typeof data.counts[key] === 'number' ? data.counts[key] : rows.length;
  const headingId = 'group-' + key + '-heading';
  const section = h('section', {
    id: 'group-' + key,
    class: 'group group-' + key + (prominent ? ' group-primary' : ''),
    'aria-labelledby': headingId,
    data: { count },
  },
  h('div', { class: 'group-header' },
    h('h2', { id: headingId }, title + ' (' + count + ')'),
    link('/leads?bucket=' + key, 'View ' + title.toLowerCase() + ' in Leads')));
  if (rows.length) {
    section.appendChild(h('ul', { class: 'row-list' }, rows.map((r) => row(r, key))));
  } else {
    section.appendChild(h('p', { class: 'muted' }, 'None'));
  }
  return section;
}

export async function render(main, ctx) {
  ctx.setTitle('Dashboard');
  const h1 = h('h1', { tabindex: '-1' }, 'Dashboard');
  const body = h('div', null, loading());
  replaceContent(main, h1, h('p', { class: 'muted' }, 'Which freelance opportunity needs your attention next?'), body);

  const result = await apiGet('/api/v1/dashboard');
  if (!ctx.current()) return;
  if (result.outcome !== 'ok') {
    replaceContent(body, loadError('the dashboard', () => render(main, ctx), result.network ? 'The server could not be reached.' : ''));
    return;
  }
  const data = result.data;
  setHasProfile(data.has_profile);
  const session = getSession();
  const parts = [
    h('p', { id: 'business-date' }, 'Business date: ' + data.business_date + ' (' + (data.timezone || session.timezone) + ')'),
  ];
  if (!data.has_profile) {
    parts.push(message('info',
      h('h2', null, 'Welcome — set up your workbench'),
      h('p', null, 'Start by saving a display label. After that you can add freelance leads and see which one needs attention next.'),
      h('p', null, link('/settings', 'Set up in Settings', { class: 'button' }))));
    replaceContent(body, ...parts);
    return;
  }
  const counts = data.counts || {};
  if (counts.total === 0) {
    parts.push(message('info', h('p', { id: 'no-leads' }, 'No leads yet.'),
      h('p', null, link('/leads/new', 'Add your first lead', { class: 'button' }))));
  }
  const dueNow = (counts.overdue || 0) + (counts.today || 0);
  if (dueNow === 0) parts.push(message('ok', h('p', { id: 'nothing-due' }, 'Nothing is due today.')));
  for (const [key, title, prominent] of GROUPS) parts.push(groupSection(key, title, prominent, data));
  replaceContent(body, ...parts);
}
