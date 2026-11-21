import { apiGet, getSession } from '../api.js';
import { h, replaceContent, link, badge, loading, loadError, message, textField, selectField, radioField, announce } from '../dom.js';
import { attachSave, isDate, chars, DATE_MESSAGE } from '../forms.js';
import { stageLabel, bucketLabel } from './dashboard.js';
import { OPTIONS, optionLabel, lastListUrl } from './leads.js';

function bucketBadge(bucket) {
  return badge('Follow-up: ' + bucketLabel(bucket), bucket === 'overdue' ? 'badge-overdue' : (bucket === 'today' ? 'badge-today' : ''));
}

function actionNodes(lead) {
  if (!lead.next_action) return [h('span', null, 'No next action set.')];
  return [
    h('span', { id: 'lead-next-action-text' }, lead.next_action.text),
    ' — due ',
    h('span', { id: 'lead-next-action-due' }, lead.next_action.due_date),
    ' ',
    bucketBadge(lead.bucket),
  ];
}

function describeLead(data) {
  const lead = data.lead;
  return h('dl', { class: 'details', id: 'conflict-current' },
    h('dt', null, 'Label'), h('dd', null, lead.label),
    h('dt', null, 'Stage'), h('dd', null, stageLabel(lead.stage)),
    h('dt', null, 'Version'), h('dd', null, String(lead.version)),
    h('dt', null, 'Next action'), h('dd', null, ...actionNodes(lead)),
    h('dt', null, 'Country'), h('dd', null, optionLabel('country', lead.country_tag)),
    h('dt', null, 'Work mode'), h('dd', null, optionLabel('mode', lead.work_mode)),
    h('dt', null, 'Source channel'), h('dd', null, optionLabel('source', lead.source_channel)),
    h('dt', null, 'Source label'), h('dd', null, lead.source_label || 'None'));
}

export async function render(main, ctx) {
  const id = ctx.params.id;
  ctx.setTitle('Lead');
  const flash = ctx.takeFlash();
  replaceContent(main,
    h('p', null, link(lastListUrl(), 'Back to leads', { id: 'back-to-leads' })),
    h('h1', { tabindex: '-1' }, 'Lead'),
    loading());
  await load(main, ctx, id, flash, false);
}

async function load(main, ctx, id, flash, afterSave) {
  const result = await apiGet('/api/v1/leads/' + encodeURIComponent(id));
  if (!ctx.current()) return;
  const back = h('p', null, link(lastListUrl(), 'Back to leads', { id: 'back-to-leads' }));
  if (result.outcome === 'notfound') {
    ctx.setTitle('Lead not found');
    replaceContent(main, back, h('h1', { tabindex: '-1' }, 'Lead not found'),
      message('error', h('p', { id: 'not-found' }, 'This lead does not exist or was removed.'), h('p', null, link('/leads', 'Go to all leads'))));
    return;
  }
  if (result.outcome !== 'ok') {
    const retry = () => { replaceContent(main, back, h('h1', { tabindex: '-1' }, 'Lead'), loading()); load(main, ctx, id, flash, afterSave); };
    replaceContent(main, back, h('h1', { tabindex: '-1' }, 'Lead'),
      afterSave ? message('warn', h('p', null, 'Your save was recorded, but the current lead could not be reloaded.')) : null,
      loadError('this lead', retry, result.network ? 'The server could not be reached.' : ''));
    return;
  }
  renderLead(main, ctx, id, result.data, flash, back);
}

function renderLead(main, ctx, id, data, flash, back) {
  const lead = data.lead;
  const bd = data.business_date || getSession().businessDate;
  // state.lead is the baseline the forms compare against. After a conflict it
  // becomes the reviewed current lead, and fields the user had not touched are
  // updated to the current values so the form shows what a new Save will send.
  const state = { version: lead.version, lead };
  const tracked = [];
  const track = (input, valueOf) => { tracked.push({ input, valueOf }); };
  const adopt = (d) => {
    if (!d || !d.lead) return;
    for (const t of tracked) {
      if (t.input.value === t.valueOf(state.lead)) t.input.value = t.valueOf(d.lead);
    }
    state.version = d.lead.version;
    state.lead = d.lead;
    syncRequired();
  };
  const actionText = (l) => (l.next_action ? l.next_action.text : '');
  const actionDue = (l) => (l.next_action ? l.next_action.due_date : '');
  const base = '/api/v1/leads/' + encodeURIComponent(id);
  ctx.setTitle(lead.label);

  const reload = (text) => {
    replaceContent(main, back.cloneNode(true), h('h1', { tabindex: '-1' }, lead.label), loading());
    return load(main, ctx, id, text, true);
  };
  const common = (formEl, controls, aliases, method, pathFn, build, successText) => attachSave({
    form: formEl, controls, aliases, method, path: pathFn, build,
    conflictMessage: 'This lead changed in another tab or window. Your input is kept. Review the current state below, then save again.',
    currentPath: () => base,
    adoptVersion: adopt,
    describeCurrent: describeLead,
    onDiscard: () => reload(null).then(() => announce('Reloaded the saved lead.')),
    async onSuccess(result) {
      const text = typeof successText === 'function' ? successText() : successText;
      const full = text + (result.replayed ? ' This save had already been recorded; nothing was duplicated.' : '');
      announce(full);
      await reload(full);
    },
  });

  // ---- details ----
  const details = h('section', { class: 'panel', 'aria-labelledby': 'details-heading' },
    h('h2', { id: 'details-heading' }, 'Details'),
    h('dl', { class: 'details' },
      h('dt', null, 'Stage'), h('dd', null, h('span', { id: 'lead-stage', class: 'badge', data: { stage: lead.stage } }, stageLabel(lead.stage))),
      h('dt', null, 'Version'), h('dd', { id: 'lead-version' }, String(lead.version)),
      h('dt', null, 'Next action'), h('dd', { id: 'lead-next-action', data: { bucket: lead.bucket } }, ...actionNodes(lead)),
      h('dt', null, 'Country'), h('dd', { id: 'lead-country' }, optionLabel('country', lead.country_tag)),
      h('dt', null, 'Work mode'), h('dd', { id: 'lead-work-mode' }, optionLabel('mode', lead.work_mode)),
      h('dt', null, 'Source channel'), h('dd', { id: 'lead-source-channel' }, optionLabel('source', lead.source_channel)),
      h('dt', null, 'Source label'), h('dd', { id: 'lead-source-label' }, lead.source_label || 'None'),
      ...(lead.catalog_key ? [h('dt', null, 'Catalog item'), h('dd', { id: 'lead-catalog-key' }, lead.catalog_key)] : []),
      h('dt', null, 'Created (UTC)'), h('dd', null, lead.created_at_utc),
      h('dt', null, 'Updated (UTC)'), h('dd', null, lead.updated_at_utc)));

  // ---- history ----
  const activities = data.activities || [];
  const history = h('section', { class: 'panel', 'aria-labelledby': 'history-heading' }, h('h2', { id: 'history-heading' }, 'Activity history'));
  if (!activities.length) {
    history.appendChild(h('p', { id: 'activity-empty' }, 'No activity recorded yet.'));
  } else {
    history.appendChild(h('ul', { class: 'activity-list', id: 'activity-list' }, activities.map((a) =>
      h('li', { data: { activityId: a.id } },
        h('div', null, badge(a.kind === 'outreach' ? 'Outreach' : 'Note'), ' ', h('strong', null, a.business_date)),
        h('p', { class: 'activity-summary' }, a.summary),
        h('div', { class: 'muted' }, 'Recorded ' + a.recorded_at_utc)))));
  }

  // ---- next action field pair ----
  const actionPair = (formId, withHint) => {
    const text = textField({ form: formId, name: 'next_action_text', label: 'Next action', value: lead.next_action ? lead.next_action.text : '', hint: withHint || null });
    const due = textField({ form: formId, name: 'next_action_due_date', label: 'Due date', value: lead.next_action ? lead.next_action.due_date : '', type: 'date', hint: 'Format YYYY-MM-DD. Past dates are allowed.' });
    due.input.min = '2000-01-01';
    due.input.max = '2099-12-31';
    track(text.input, actionText);
    track(due.input, actionDue);
    return { text, due };
  };
  const readAction = (pair, required, errors) => {
    const t = pair.text.input.value.trim();
    const d = pair.due.input.value.trim();
    let ok = true;
    if (required || t || d) {
      if (!t) { errors.push({ name: 'next_action_text', text: 'Enter the next action (1–240 characters).' }); ok = false; }
      else if (chars(t) > 240) { errors.push({ name: 'next_action_text', text: 'Enter 1–240 characters.' }); ok = false; }
      if (!d) { errors.push({ name: 'next_action_due_date', text: 'Enter a due date as YYYY-MM-DD.' }); ok = false; }
      else if (!isDate(d)) { errors.push({ name: 'next_action_due_date', text: DATE_MESSAGE }); ok = false; }
    }
    return { t, d, ok, present: Boolean(t || d) };
  };
  const naAliases = { next_action: 'next_action_text', 'next_action.text': 'next_action_text', 'next_action.due_date': 'next_action_due_date', due_date: 'next_action_due_date', text: 'next_action_text' };

  // ---- activity form ----
  const fa = 'activity-form';
  const activityForm = h('form', { id: fa, novalidate: true, class: 'form-card', 'aria-labelledby': 'activity-heading' });
  const kind = radioField({ form: fa, name: 'kind', legend: 'Type', value: 'outreach', options: [['outreach', 'Outreach'], ['note', 'Note']] });
  const actDate = textField({ form: fa, name: 'business_date', label: 'Date', value: bd, type: 'date', hint: 'Date of the activity, not later than ' + bd + ' (Amsterdam).' });
  actDate.input.max = bd;
  actDate.input.min = '2000-01-01';
  const summary = textField({ form: fa, name: 'summary', label: 'Summary', rows: 4, hint: 'Required, 1–1000 characters.', required: true });
  const actPair = actionPair(fa, null);
  const naHint = h('p', { class: 'hint', id: 'activity-next-hint' }, '');
  const naFieldset = h('fieldset', { id: 'activity-next-action', 'aria-describedby': 'activity-next-hint' }, h('legend', null, 'Next action'), naHint, actPair.text.wrap, actPair.due.wrap);
  const isRequired = () => state.lead.stage !== 'contacted' && kind.inputs.find((i) => i.checked).value === 'outreach';
  const syncRequired = () => {
    const req = isRequired();
    naHint.textContent = req
      ? 'Required: the first outreach needs a next action with a due date.'
      : 'Optional. Fill in both fields to replace the current next action.';
    for (const c of [actPair.text.input, actPair.due.input]) {
      if (req) c.setAttribute('aria-required', 'true'); else c.removeAttribute('aria-required');
    }
  };
  for (const r of kind.inputs) r.addEventListener('change', syncRequired);
  syncRequired();
  activityForm.append(h('h2', { id: 'activity-heading' }, 'Record activity'), kind.wrap, actDate.wrap, summary.wrap, naFieldset,
    h('div', { class: 'actions' }, h('button', { type: 'submit', name: 'save' }, 'Save activity')));
  common(activityForm,
    { kind, business_date: actDate, summary, next_action_text: actPair.text, next_action_due_date: actPair.due },
    naAliases, 'POST', () => base + '/activities',
    () => {
      const errors = [];
      const k = kind.inputs.find((i) => i.checked).value;
      const date = actDate.input.value.trim();
      const text = summary.input.value.trim();
      if (!isDate(date)) errors.push({ name: 'business_date', text: DATE_MESSAGE });
      else if (date > bd) errors.push({ name: 'business_date', text: 'The activity date cannot be later than today (' + bd + ' in Amsterdam).' });
      if (chars(text) < 1 || chars(text) > 1000) errors.push({ name: 'summary', text: 'Enter 1–1000 characters.' });
      const req = isRequired();
      const na = readAction(actPair, req, errors);
      if (errors.length) return { errors };
      const body = { version: state.version, kind: k, business_date: date, summary: text };
      const cur = state.lead.next_action;
      const changed = !cur || cur.text !== na.t || cur.due_date !== na.d;
      if (na.present && (req || changed)) body.next_action = { text: na.t, due_date: na.d };
      return { body };
    },
    'Activity recorded.');

  // ---- follow-up form ----
  const fn = 'next-action-form';
  const naForm = h('form', { id: fn, novalidate: true, class: 'form-card', 'aria-labelledby': 'follow-up-heading' });
  const naPair = actionPair(fn, 'Required, 1–240 characters.');
  const naActions = h('div', { class: 'actions' }, h('button', { type: 'submit', name: 'save' }, 'Save follow-up'));
  naForm.append(h('h2', { id: 'follow-up-heading' }, 'Change follow-up'), naPair.text.wrap, naPair.due.wrap, naActions);
  const naCtl = common(naForm, { next_action_text: naPair.text, next_action_due_date: naPair.due }, naAliases, 'PUT', () => base + '/next-action',
    () => {
      const errors = [];
      const na = readAction(naPair, true, errors);
      if (errors.length) return { errors };
      return { body: { version: state.version, next_action: { text: na.t, due_date: na.d } } };
    },
    () => (naForm.dataset.cleared === '1' ? 'Follow-up cleared.' : 'Follow-up saved.'));
  if (lead.stage === 'new' && lead.next_action) {
    naActions.appendChild(h('button', {
      type: 'button', class: 'secondary', name: 'clear-follow-up', id: 'clear-follow-up',
      onclick: () => { naForm.dataset.cleared = '1'; naCtl.start(() => ({ body: { version: state.version, next_action: null } })); },
    }, 'Clear follow-up'));
    naForm.addEventListener('submit', () => { naForm.dataset.cleared = '0'; }, true);
  }

  // ---- stage form ----
  let stageForm = null;
  if (lead.stage === 'new') {
    const fs = 'stage-form';
    stageForm = h('form', { id: fs, novalidate: true, class: 'form-card', 'aria-labelledby': 'stage-heading' });
    const sp = actionPair(fs, 'Required to start researching, 1–240 characters.');
    stageForm.append(h('h2', { id: 'stage-heading' }, 'Start researching'),
      h('p', null, 'Moves this lead from New to Researching. A next action with a due date is required.'),
      sp.text.wrap, sp.due.wrap,
      h('div', { class: 'actions' }, h('button', { type: 'submit', name: 'save' }, 'Start researching')));
    common(stageForm, { next_action_text: sp.text, next_action_due_date: sp.due }, naAliases, 'POST', () => base + '/stage',
      () => {
        const errors = [];
        const na = readAction(sp, true, errors);
        if (errors.length) return { errors };
        return { body: { version: state.version, stage: 'researching', next_action: { text: na.t, due_date: na.d } } };
      },
      'Lead is now researching.');
  }

  // ---- edit form ----
  const fe = 'lead-edit-form';
  const editForm = h('form', { id: fe, novalidate: true, class: 'form-card', 'aria-labelledby': 'edit-heading' });
  const eLabel = textField({ form: fe, name: 'label', label: 'Opportunity label', value: lead.label, hint: 'Required, 1–120 characters.', required: true });
  const eCountry = selectField({ form: fe, name: 'country_tag', label: 'Country', value: lead.country_tag, options: OPTIONS.country });
  const eMode = selectField({ form: fe, name: 'work_mode', label: 'Work mode', value: lead.work_mode, options: OPTIONS.mode });
  const eChannel = selectField({ form: fe, name: 'source_channel', label: 'Source channel', value: lead.source_channel, options: OPTIONS.source });
  const eSource = textField({ form: fe, name: 'source_label', label: 'Source label (optional)', value: lead.source_label || '', hint: '1–120 characters, or leave empty for none.' });
  track(eLabel.input, (l) => l.label);
  track(eCountry.input, (l) => l.country_tag);
  track(eMode.input, (l) => l.work_mode);
  track(eChannel.input, (l) => l.source_channel);
  track(eSource.input, (l) => l.source_label || '');
  editForm.append(h('h2', { id: 'edit-heading' }, 'Edit details'), eLabel.wrap, eCountry.wrap, eMode.wrap, eChannel.wrap, eSource.wrap,
    h('div', { class: 'actions' }, h('button', { type: 'submit', name: 'save' }, 'Save details')));
  common(editForm, { label: eLabel, country_tag: eCountry, work_mode: eMode, source_channel: eChannel, source_label: eSource }, {}, 'PATCH', () => base,
    () => {
      const errors = [];
      const patch = {};
      const l = eLabel.input.value.trim();
      if (chars(l) < 1 || chars(l) > 120) errors.push({ name: 'label', text: 'Enter 1–120 characters.' });
      else if (l !== state.lead.label) patch.label = l;
      if (eCountry.input.value !== state.lead.country_tag) patch.country_tag = eCountry.input.value;
      if (eMode.input.value !== state.lead.work_mode) patch.work_mode = eMode.input.value;
      if (eChannel.input.value !== state.lead.source_channel) patch.source_channel = eChannel.input.value;
      const sl = eSource.input.value.trim();
      if (chars(sl) > 120) errors.push({ name: 'source_label', text: 'Enter 1–120 characters, or leave it empty.' });
      else if (sl !== (state.lead.source_label || '')) patch.source_label = sl === '' ? null : sl;
      if (!errors.length && !Object.keys(patch).length) errors.push({ name: 'label', text: 'Change at least one field before saving.' });
      if (errors.length) return { errors };
      return { body: Object.assign({ version: state.version }, patch) };
    },
    'Details saved.');

  const parts = [back.cloneNode(true)];
  parts.push(h('h1', { tabindex: '-1' }, lead.label));
  if (flash) parts.push(h('div', { class: 'message message-ok', id: 'save-result', tabindex: '-1', role: 'group' }, h('p', null, flash)));
  parts.push(details, history);
  parts.push(h('h2', null, 'Update this lead'));
  parts.push(activityForm, naForm);
  if (stageForm) parts.push(stageForm);
  parts.push(editForm);
  replaceContent(main, ...parts);
  if (flash) {
    const box = document.getElementById('save-result');
    if (box) box.focus();
  }
}
