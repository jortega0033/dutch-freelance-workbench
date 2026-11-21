import { apiGet, loadSession, getSession } from '../api.js';
import { h, replaceContent, textField, message, loading, loadError, announce } from '../dom.js';
import { attachSave, chars } from '../forms.js';

export async function render(main, ctx) {
  ctx.setTitle('Settings');
  const body = h('div', null, loading());
  replaceContent(main, h('h1', { tabindex: '-1' }, 'Settings'), body);
  await load(main, body, ctx, null);
}

async function load(main, body, ctx, flash) {
  const result = await apiGet('/api/v1/profile');
  if (!ctx.current()) return;
  if (result.outcome !== 'ok') {
    replaceContent(body, loadError('your settings', () => { replaceContent(body, loading()); load(main, body, ctx, flash); }, result.network ? 'The server could not be reached.' : ''));
    return;
  }
  showForm(main, body, ctx, result.data.profile, flash);
}

function showForm(main, body, ctx, profile, flash) {
  const state = { version: profile ? profile.version : 0 };
  const form = h('form', { id: 'settings-form', novalidate: true, class: 'form-card', 'aria-labelledby': 'settings-heading' });
  const label = textField({
    form: 'settings-form', name: 'display_label', label: 'Display label',
    value: profile ? profile.display_label : '', hint: 'Required, 1–120 characters. This is the name shown for your workbench.',
    required: true,
  });
  form.appendChild(h('h2', { id: 'settings-heading' }, profile ? 'Your profile' : 'Set up your profile'));
  form.appendChild(label.wrap);
  form.appendChild(h('div', { class: 'actions' }, h('button', { type: 'submit', name: 'save' }, 'Save settings')));
  const controls = { display_label: label };

  const ctl = attachSave({
    form, controls, aliases: {}, method: 'PUT',
    path: () => '/api/v1/profile',
    conflictMessage: 'Your settings changed in another tab or window. Your input is kept. Review the current state below, then save again.',
    currentPath: () => '/api/v1/profile',
    build() {
      const value = label.input.value.trim();
      if (chars(value) < 1 || chars(value) > 120) {
        return { errors: [{ name: 'display_label', text: 'Enter 1–120 characters.' }] };
      }
      return { body: { version: state.version, display_label: value } };
    },
    adoptVersion(data) {
      if (data && data.profile) state.version = data.profile.version;
    },
    describeCurrent(data) {
      const p = data && data.profile;
      return h('dl', { class: 'details' },
        h('dt', null, 'Display label'), h('dd', null, p ? p.display_label : '(none saved)'),
        h('dt', null, 'Version'), h('dd', null, p ? String(p.version) : '0'));
    },
    onDiscard() {
      replaceContent(body, loading());
      load(main, body, ctx, null);
    },
    async onSuccess() {
      const fresh = await apiGet('/api/v1/profile');
      if (!ctx.current()) return;
      if (fresh.outcome !== 'ok') {
        replaceContent(body, message('warn', h('p', null, 'Your settings were saved, but we could not reload them.')),
          loadError('your settings', () => { replaceContent(body, loading()); load(main, body, ctx, 'Settings saved.'); }));
        announce('Settings saved. Could not reload them.');
        return;
      }
      await loadSession();
      announce('Settings saved.');
      showForm(main, body, ctx, fresh.data.profile, 'Settings saved.');
    },
  });
  void ctl;

  const session = getSession();
  const parts = [];
  if (flash) {
    parts.push(h('div', { class: 'message message-ok', id: 'save-result', tabindex: '-1', role: 'group' }, h('p', null, flash)));
  }
  if (!profile) {
    parts.push(message('info', h('p', { id: 'first-use' }, 'First use: no profile has been saved yet. Save a display label to start adding leads.')));
  }
  parts.push(form);
  parts.push(h('div', { class: 'panel' },
    h('h2', null, 'Fixed settings'),
    h('dl', { class: 'details' },
      h('dt', null, 'Timezone'), h('dd', { id: 'settings-timezone' }, (session.timezone || 'Europe/Amsterdam') + ' (fixed)'),
      h('dt', null, 'Language'), h('dd', { id: 'settings-language' }, 'English'))));
  replaceContent(body, ...parts);
  if (flash) {
    const box = document.getElementById('save-result');
    if (box) box.focus();
  }
}
