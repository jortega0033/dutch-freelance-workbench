// DOM helpers. User text only ever goes in through text nodes / textContent.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (name.startsWith('on') && typeof value === 'function') {
        el.addEventListener(name.slice(2), value);
      } else if (name === 'class') {
        el.className = String(value);
      } else if (name === 'data') {
        for (const [k, v] of Object.entries(value)) el.dataset[k] = String(v);
      } else if (name === 'value' || name === 'checked' || name === 'disabled' || name === 'selected') {
        el[name] = value;
        if (name === 'value') el.setAttribute('value', String(value));
      } else if (name === 'style') {
        continue; // inline styles are not used
      } else {
        el.setAttribute(name, value === true ? '' : String(value));
      }
    }
  }
  appendChildren(el, children);
  return el;
}

export function appendChildren(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) appendChildren(el, child);
    else if (child instanceof Node) el.appendChild(child);
    else el.appendChild(document.createTextNode(String(child)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replaceContent(el, ...children) {
  clear(el);
  appendChildren(el, children);
  return el;
}

let statusTimer = 0;
export function announce(message) {
  const region = document.getElementById('status');
  if (!region) return;
  region.textContent = '';
  window.clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => { region.textContent = message; }, 30);
}

export function announceAlert(message) {
  const region = document.getElementById('alert');
  if (!region) return;
  region.textContent = '';
  window.setTimeout(() => { region.textContent = message; }, 30);
}

export function link(href, text, attrs) {
  return h('a', Object.assign({ href }, attrs || {}), text);
}

export function badge(text, extraClass) {
  return h('span', { class: 'badge' + (extraClass ? ' ' + extraClass : '') }, text);
}

export function message(kind, ...children) {
  return h('div', { class: 'message message-' + kind }, ...children);
}

// A failed read: never shows data or zero counts, offers Try again.
export function loadError(what, retry, detail) {
  const button = h('button', { type: 'button', onclick: retry }, 'Try again');
  const box = h('div', { class: 'message message-error', id: 'load-error', tabindex: '-1' },
    h('p', null, 'Could not load ' + what + '.' + (detail ? ' ' + detail : '')),
    h('p', null, button));
  return box;
}

export function loading(text) {
  return h('p', { class: 'skeleton-note', id: 'loading' }, text || 'Loading…');
}

// ---- form fields ----

let uid = 0;
export function nextId(prefix) {
  uid += 1;
  return prefix + '-' + uid;
}

function describedBy(ids) {
  return ids.filter(Boolean).join(' ');
}

export function textField({ form, name, label, value, hint, type, required, maxlength, rows, autocomplete }) {
  const id = form + '-' + name;
  const hintId = hint ? id + '-hint' : null;
  const input = rows
    ? h('textarea', { id, name, rows, 'aria-describedby': hintId, 'aria-required': required ? 'true' : null }, '')
    : h('input', { id, name, type: type || 'text', 'aria-describedby': hintId, 'aria-required': required ? 'true' : null, autocomplete: autocomplete || 'off', maxlength: maxlength || null });
  input.value = value || '';
  const wrap = h('div', { class: 'field', data: { field: name } },
    h('label', { for: id }, label),
    hint ? h('p', { class: 'hint', id: hintId }, hint) : null,
    input);
  return { wrap, input, id, hintId, label };
}

export function selectField({ form, name, label, value, options, hint, anyLabel }) {
  const id = form + '-' + name;
  const hintId = hint ? id + '-hint' : null;
  const select = h('select', { id, name, 'aria-describedby': hintId });
  if (anyLabel !== undefined) select.appendChild(h('option', { value: '' }, anyLabel));
  for (const [val, text] of options) select.appendChild(h('option', { value: val }, text));
  select.value = value === undefined || value === null ? (anyLabel !== undefined ? '' : options[0][0]) : value;
  if (select.selectedIndex < 0) select.selectedIndex = 0;
  const wrap = h('div', { class: 'field', data: { field: name } },
    h('label', { for: id }, label),
    hint ? h('p', { class: 'hint', id: hintId }, hint) : null,
    select);
  return { wrap, input: select, id, hintId, label };
}

export function radioField({ form, name, legend, value, options, hint }) {
  const hintId = hint ? form + '-' + name + '-hint' : null;
  const fieldset = h('fieldset', { id: form + '-' + name, 'aria-describedby': hintId, data: { field: name } },
    h('legend', null, legend),
    hint ? h('p', { class: 'hint', id: hintId }, hint) : null);
  const inputs = [];
  for (const [val, text] of options) {
    const id = form + '-' + name + '-' + val;
    const input = h('input', { type: 'radio', id, name, value: val });
    input.checked = val === value;
    inputs.push(input);
    fieldset.appendChild(h('div', { class: 'choice' }, input, h('label', { for: id }, text)));
  }
  return { wrap: fieldset, input: inputs[0], inputs, id: form + "-" + name, hintId, isGroup: true, label: legend };
}

// ---- errors ----

// fields: Map-like object name -> {el, wrap, input, hintId, isGroup}
// Shows inline messages + summary and focuses the first invalid control.
// `errors` is a list of {name|null, text}. Unmapped (name null) go to summary only.
export function showErrors(form, controls, errors, heading) {
  clearErrors(form);
  const summaryItems = [];
  let firstInput = null;
  const seen = new Set();
  for (const err of errors) {
    const ctl = err.name ? controls[err.name] : null;
    if (ctl && !seen.has(err.name)) {
      seen.add(err.name);
      const errId = ctl.id + '-error';
      const p = h('p', { class: 'field-error', id: errId }, err.text);
      ctl.wrap.appendChild(p);
      const target = ctl.input;
      target.setAttribute('aria-invalid', 'true');
      const holder = ctl.isGroup ? ctl.wrap : target;
      holder.setAttribute('aria-describedby', describedBy([ctl.hintId, errId]));
      if (!firstInput) firstInput = target;
      summaryItems.push({ text: err.text, target, label: ctl.label });
    } else {
      summaryItems.push({ text: err.text, target: null, label: ctl ? ctl.label : (err.name || null) });
    }
  }
  const list = h('ul', null);
  for (const item of summaryItems) {
    const text = item.label && !item.target ? item.label + ': ' + item.text : item.text;
    if (item.target) {
      const a = h('a', { href: '#' + item.target.id, onclick: (ev) => { ev.preventDefault(); item.target.focus(); } },
        (item.label ? item.label + ': ' : '') + item.text);
      list.appendChild(h('li', null, a));
    } else {
      list.appendChild(h('li', null, text));
    }
  }
  const summary = h('div', { class: 'message message-error error-summary', id: form.id + '-error-summary', tabindex: '-1', role: 'group', 'aria-labelledby': form.id + '-error-heading' },
    h('h3', { id: form.id + '-error-heading' }, heading || 'There is a problem'),
    list);
  form.insertBefore(summary, form.firstChild);
  if (firstInput) firstInput.focus();
  else summary.focus();
  return summary;
}

export function clearErrors(form) {
  const summary = form.querySelector('.error-summary');
  if (summary) summary.remove();
  for (const p of form.querySelectorAll('.field-error')) p.remove();
  for (const el of form.querySelectorAll('[aria-invalid]')) el.removeAttribute('aria-invalid');
  for (const el of form.querySelectorAll('[aria-describedby]')) {
    const keep = el.getAttribute('aria-describedby').split(/\s+/).filter((id) => id && !id.endsWith('-error'));
    if (keep.length) el.setAttribute('aria-describedby', keep.join(' '));
    else el.removeAttribute('aria-describedby');
  }
}

// Maps server field keys (possibly dotted or parent names) to control names.
export function mapServerFields(fields, controls, aliases) {
  const out = [];
  if (!fields) return out;
  for (const [key, text] of Object.entries(fields)) {
    const msg = String(text);
    let name = null;
    if (controls[key]) name = key;
    else if (aliases && aliases[key] && controls[aliases[key]]) name = aliases[key];
    else {
      const parent = key.split('.')[0];
      if (aliases && aliases[parent] && controls[aliases[parent]]) name = aliases[parent];
      else if (controls[parent]) name = parent;
    }
    out.push({ name, text: name ? msg : key + ': ' + msg });
  }
  return out;
}
