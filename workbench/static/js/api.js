// API client: session token, reads, and idempotent mutations.
// A "pending save" is created once per deliberate Save click and holds the
// exact serialized body and Idempotency-Key. Retrying resends that same object.

const session = {
  token: null,
  businessDate: null,
  timezone: 'Europe/Amsterdam',
  hasProfile: false,
};

export function getSession() {
  return session;
}

export function setHasProfile(value) {
  session.hasProfile = Boolean(value);
}

function newKey() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (_err) {
    return null;
  }
}

function errorFrom(body, status) {
  const err = body && typeof body === 'object' && body.error && typeof body.error === 'object' ? body.error : {};
  return {
    code: typeof err.code === 'string' ? err.code : 'HTTP_' + status,
    message: typeof err.message === 'string' ? err.message : '',
    fields: err.fields && typeof err.fields === 'object' ? err.fields : null,
    currentVersion: typeof err.current_version === 'number' ? err.current_version : null,
    reload: typeof err.reload === 'string' ? err.reload : null,
  };
}

function classify(status, error) {
  if (status >= 200 && status < 300) return 'ok';
  if (status === 422) return 'validation';
  if (status === 409) return 'conflict';
  if (status === 404) return 'notfound';
  if (status === 503) return 'busy';
  if (status === 403) return 'forbidden';
  if (status >= 500) return 'uncertain';
  if (status >= 400) return 'rejected';
  return 'uncertain';
}

async function doFetch(method, path, options) {
  const headers = { Accept: 'application/json' };
  const init = { method, headers, cache: 'no-store', credentials: 'same-origin' };
  if (options && options.bodyText !== undefined) {
    headers['Content-Type'] = 'application/json';
    headers['X-Workbench-Session'] = session.token || '';
    headers['Idempotency-Key'] = options.key;
    init.body = options.bodyText;
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (_err) {
    return { outcome: 'uncertain', status: 0, network: true, error: null, data: null, replayed: false };
  }
  const body = await readJson(response);
  const status = response.status;
  const ok = status >= 200 && status < 300;
  const error = ok ? null : errorFrom(body, status);
  let outcome = classify(status, error);
  if (ok && body === null && method !== 'GET') outcome = 'uncertain';
  return {
    outcome,
    status,
    network: false,
    error,
    data: ok ? body : null,
    replayed: response.headers.get('Idempotent-Replayed') === 'true',
  };
}

export async function loadSession() {
  const result = await doFetch('GET', '/api/v1/session');
  if (result.outcome === 'ok' && result.data && typeof result.data.token === 'string') {
    session.token = result.data.token;
    session.businessDate = result.data.business_date;
    session.timezone = result.data.timezone || 'Europe/Amsterdam';
    session.hasProfile = Boolean(result.data.has_profile);
  }
  return result;
}

export async function apiGet(path) {
  return doFetch('GET', path);
}

export function createPending(method, path, body) {
  return { method, path, bodyText: JSON.stringify(body), key: newKey() };
}

// Sends a pending save. On SESSION_INVALID refetches the session and retries
// once with the very same key and body text.
export async function apiMutate(pending) {
  if (!session.token) {
    const s = await loadSession();
    if (s.outcome !== 'ok') return { outcome: 'uncertain', status: 0, network: true, error: null, data: null, replayed: false, notSent: true };
  }
  const send = () => doFetch(pending.method, pending.path, { bodyText: pending.bodyText, key: pending.key });
  let result = await send();
  if (result.outcome === 'forbidden' && result.error && result.error.code === 'SESSION_INVALID') {
    const s = await loadSession();
    if (s.outcome === 'ok') result = await send();
  }
  return result;
}
