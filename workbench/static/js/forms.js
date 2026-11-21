// Shared save flow for all forms: validation display, pending saves,
// conflict / busy / uncertain panels. Each Save click makes ONE new pending
// save; "Retry the same save" resends that exact pending save.

import { createPending, apiMutate, apiGet } from './api.js';
import { h, showErrors, clearErrors, mapServerFields, announceAlert, link, replaceContent } from './dom.js';

function safeReload(path) {
  if (typeof path !== 'string') return null;
  if (!path.startsWith('/api/v1/') || path.includes('//') || path.includes('..') || path.includes('\\')) return null;
  return path;
}

export function attachSave(opts) {
  const { form, controls, aliases, method } = opts;
  const host = h('div', { class: 'save-panels' });
  form.insertBefore(host, form.firstChild);
  let pending = null;
  let running = false;
  // true while a save may or may not have been stored (uncertain result):
  // the only ways forward are "Retry the same save" or "Discard and reload",
  // so a fresh Save click can never create a duplicate under a new key.
  let unresolved = false;
  // panel ids are per form, so one form never removes another form's panel
  const panelId = (kind) => form.id + '-' + kind;

  function setDisabled(value) {
    for (const b of form.querySelectorAll('button')) b.disabled = value;
  }

  function clearPanels() {
    replaceContent(host);
  }

  function showMessage(kind, text, extra) {
    clearErrors(form);
    const box = h('div', { class: 'message message-' + kind, tabindex: '-1', role: 'group' }, h('p', null, text), extra || null);
    replaceContent(host, box);
    announceAlert(text);
    box.focus();
    return box;
  }

  function retryButtons(extraDiscard) {
    return h('p', { class: 'actions' },
      h('button', { type: 'button', name: 'retry-same-save', onclick: () => { clearPanels(); unresolved = false; run(); } }, 'Retry the same save'),
      h('button', { type: 'button', class: 'secondary', name: 'discard-reload', onclick: () => opts.onDiscard() }, extraDiscard || 'Discard and reload'));
  }

  function showPanel(name, kind, title, text) {
    clearErrors(form);
    const panel = h('div', { id: panelId(name), class: name + ' message message-' + kind, tabindex: '-1', role: 'group', 'aria-label': title },
      h('h3', null, title),
      h('p', null, text),
      retryButtons());
    replaceContent(host, panel);
    announceAlert(text);
    panel.focus();
  }

  async function showConflict(error) {
    pending = null;
    clearErrors(form);
    const text = opts.conflictMessage;
    const reloadPath = safeReload(error && error.reload) || opts.currentPath();
    const panel = h('div', { id: panelId('conflict-panel'), class: 'conflict-panel message message-warn', tabindex: '-1', role: 'group', 'aria-label': 'Conflict' },
      h('h3', null, 'Changed elsewhere'),
      h('p', null, text),
      h('p', { class: 'skeleton-note' }, 'Loading current saved state…'));
    replaceContent(host, panel);
    announceAlert(text);
    panel.focus();
    const result = await apiGet(reloadPath);
    if (!panel.isConnected) return;
    if (result.outcome === 'ok') {
      opts.adoptVersion(result.data);
      replaceContent(panel,
        h('h3', null, 'Changed elsewhere'),
        h('p', null, text),
        h('h4', null, 'Current saved state'),
        opts.describeCurrent(result.data),
        h('p', { class: 'hint' }, 'Your input is still in the form. Fields you had not changed now show the current saved values. Press Save again to save the form as shown on top of this version.'),
        h('p', { class: 'actions' },
          h('button', { type: 'button', class: 'secondary', name: 'discard-reload', onclick: () => opts.onDiscard() }, 'Discard my input and reload')));
      panel.focus();
    } else {
      replaceContent(panel,
        h('h3', null, 'Changed elsewhere'),
        h('p', null, text),
        h('p', null, 'Could not load the current saved state.'),
        h('p', { class: 'actions' },
          h('button', { type: 'button', name: 'retry-load', onclick: () => showConflict(error) }, 'Try again'),
          h('button', { type: 'button', class: 'secondary', name: 'discard-reload', onclick: () => opts.onDiscard() }, 'Discard my input and reload')));
      panel.focus();
    }
  }

  async function handle(result) {
    switch (result.outcome) {
      case 'ok': {
        pending = null;
        clearPanels();
        await opts.onSuccess(result);
        return;
      }
      case 'validation': {
        pending = null;
        clearPanels();
        const err = result.error;
        let list = mapServerFields(err.fields, controls, aliases);
        if (!list.length) list = [{ name: null, text: err.message || 'The server rejected the input.' }];
        else if (err.code !== 'VALIDATION_FAILED' && err.message) list.unshift({ name: null, text: err.message });
        showErrors(form, controls, list, 'Please fix the following');
        announceAlert('The form has errors. ' + list.length + ' to fix.');
        return;
      }
      case 'conflict': {
        const err = result.error;
        if (err.code === 'STALE_VERSION') { await showConflict(err); return; }
        pending = null;
        if (err.code === 'KEY_REUSED') {
          showMessage('error', 'This save could not be processed because its request key was already used for different data. Nothing was changed. Press Save again to send it with a new key.');
        } else if (err.code === 'PROFILE_REQUIRED') {
          showMessage('error', 'Save your display label in Settings before adding leads.', h('p', null, link('/settings', 'Go to Settings')));
        } else {
          showMessage('error', err.message || 'The save conflicted with the saved state.');
        }
        return;
      }
      case 'notfound': {
        pending = null;
        showMessage('error', 'This item could not be found. It may no longer exist.', h('p', null, link('/leads', 'Back to leads')));
        return;
      }
      case 'busy':
        if (result.error && result.error.code === 'CATALOG_UNAVAILABLE') {
          pending = null;
          showMessage('error', 'The fictional catalog could not be loaded, so nothing was saved. You can still add a lead manually.',
            h('p', null, link('/leads/new', 'Add a lead manually')));
          return;
        }
        // 503 means nothing was stored, so a fresh Save is safe as well
        showPanel('busy-panel', 'warn', 'Storage is busy', 'The server was busy and nothing was saved. Retry the same save, or discard and reload.');
        return;
      case 'uncertain':
        unresolved = true;
        showPanel('uncertain-panel', 'warn', 'Save not confirmed', 'We could not confirm whether this save was stored. Your input is kept. Retry the same save (it will not create a duplicate), or discard and reload.');
        return;
      case 'forbidden':
        pending = null;
        showMessage('error', 'The server refused this request (access denied). Reload the page and try again.');
        return;
      default:
        pending = null;
        showMessage('error', (result.error && result.error.message) || 'The server rejected this request.');
    }
  }

  async function run() {
    if (running || !pending) return;
    running = true;
    setDisabled(true);
    let result;
    try {
      result = await apiMutate(pending);
    } finally {
      running = false;
      if (form.isConnected) setDisabled(false);
    }
    if (!form.isConnected) return;
    await handle(result);
  }

  async function start(buildFn) {
    if (running) return;
    if (unresolved && pending) {
      const panel = host.firstChild;
      announceAlert('The previous save is not confirmed yet. Use "Retry the same save" or "Discard and reload" first.');
      if (panel) panel.focus();
      return;
    }
    clearErrors(form);
    clearPanels();
    const built = (buildFn || opts.build)();
    if (built.errors) {
      showErrors(form, controls, built.errors, 'Please fix the following');
      announceAlert('The form has errors. ' + built.errors.length + ' to fix.');
      return;
    }
    pending = createPending(method, opts.path(), built.body);
    await run();
  }

  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    start();
  });

  return { start, host, clearPanels };
}

export function isDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = value.split('-').map(Number);
  if (y < 2000 || y > 2099) return false;
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

// Text length in Unicode code points, as the server counts it (not UTF-16 units).
export function chars(value) {
  return [...value].length;
}

export const DATE_MESSAGE = 'Enter a real date as YYYY-MM-DD between 2000-01-01 and 2099-12-31.';
