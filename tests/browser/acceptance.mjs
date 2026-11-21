// Optional real-browser acceptance run for M1 (T12 plus browser parts of T03,
// T09, T10, T11 and T15) and the Discover screen (issue #1, phase C). Test
// tooling only: the app itself has no Node or Playwright dependency.
//
// Requirements: Node 18+, a globally installed `playwright` package and a local
// Google Chrome. Run from the repository root:
//
//   NODE_PATH="$(npm root -g)" WORKBENCH_PYTHON=python3.12 node tests/browser/acceptance.mjs
//
// It uses fresh temporary data directories and fresh browser profiles, blocks
// all non-loopback traffic at the browser level, and writes a JSON evidence
// file to a temporary directory (printed at the end).

import { createRequire } from 'node:module';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const PY = process.env.WORKBENCH_PYTHON || 'python';
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), 'dfw-acceptance-'));
const PORT_A = 18781;
const PORT_B = 18782;
const PORT_C = 18783;
const BLOCK_ARGS = [
  // Every non-loopback request goes to a dead proxy; Chrome always connects to
  // loopback directly, so the app keeps working.
  '--proxy-server=http://127.0.0.1:9',
  // Belt and braces: no host name except loopback resolves.
  '--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1',
  '--no-first-run', '--no-default-browser-check', '--disable-sync',
];

const evidence = { started: new Date().toISOString(), workDir: WORK, checks: [], requests: {}, isolation: {}, servers: [] };
let failures = 0;

function check(name, ok, detail) {
  evidence.checks.push({ name, ok: Boolean(ok), detail: detail === undefined ? null : detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail !== undefined ? '  ' + JSON.stringify(detail) : ''}`);
  if (!ok) failures += 1;
}

// ---------------------------------------------------------------- servers

function startServer({ dataDir, port, now, catalog }) {
  const args = now
    ? ['tests/support/serve_fixed_clock.py', '--data-dir', dataDir, '--port', String(port), '--now', now,
      ...(catalog ? ['--catalog', catalog] : [])]
    : ['-m', 'workbench', '--data-dir', dataDir, '--port', String(port)];
  const proc = spawn(PY, args, { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  let err = '';
  proc.stdout.on('data', (d) => { out += d; });
  proc.stderr.on('data', (d) => { err += d; });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('server did not start: ' + err)), 15000);
    const poll = setInterval(() => {
      if (out.includes('URL:')) {
        clearInterval(poll); clearTimeout(timer);
        const info = { pid: proc.pid, port, now: now || 'system clock', banner: out.trim() };
        evidence.servers.push(info);
        resolve({
          proc, info, base: `http://127.0.0.1:${port}`,
          stderr: () => err,
          stop: () => new Promise((res) => { proc.once('exit', res); proc.kill(); }),
        });
      }
    }, 50);
    proc.once('exit', (code) => { clearInterval(poll); clearTimeout(timer); reject(new Error(`server exited ${code}: ${err}`)); });
  });
}

function sql(dataDir, query) {
  const code = 'import sqlite3,sys,json,pathlib;' +
    'p=pathlib.Path(sys.argv[1],"workbench.sqlite3").resolve().as_uri()+"?mode=ro";' +
    'c=sqlite3.connect(p,uri=True);print(json.dumps(c.execute(sys.argv[2]).fetchall()))';
  const r = spawnSync(PY, ['-I', '-c', code, dataDir, query], { encoding: 'utf8' });
  if (r.status !== 0) throw new Error(r.stderr);
  return JSON.parse(r.stdout);
}

// ---------------------------------------------------------------- browser helpers

async function launch(profileName, timezoneId) {
  const profile = path.join(WORK, profileName);
  const ctx = await chromium.launchPersistentContext(profile, {
    channel: 'chrome', headless: true, timezoneId, locale: 'en-US',
    viewport: { width: 1280, height: 900 }, args: BLOCK_ARGS,
  });
  await learnDateOrder(ctx);
  return { ctx, profile };
}

function watchRequests(ctx, label) {
  const list = [];
  evidence.requests[label] = list;
  ctx.on('request', (r) => list.push({ page: r.frame()?.page()?._label || 'other', method: r.method(), url: r.url() }));
  ctx.on('requestfailed', (r) => list.push({ page: r.frame()?.page()?._label || 'other', failed: r.failure()?.errorText, url: r.url() }));
  return list;
}

async function appPage(ctx, label) {
  const page = await ctx.newPage();
  page._label = label;
  page.on('dialog', async (d) => { check(`no script dialog on ${label}`, false, d.message()); await d.dismiss(); });
  page.on('pageerror', (e) => check(`no page error on ${label}`, false, String(e)));
  return page;
}

async function tabTo(page, selector, max = 120) {
  for (let i = 0; i < max; i += 1) {
    await page.keyboard.press('Tab');
    const hit = await page.evaluate((s) => !!document.activeElement && document.activeElement.matches(s), selector);
    if (hit) return;
  }
  throw new Error(`Could not reach ${selector} with the Tab key`);
}

async function focusedIs(page, selector) {
  return page.evaluate((s) => !!document.activeElement && document.activeElement.matches(s), selector);
}

// Chrome's date field segment order follows the operating-system locale, so
// learn it once per browser and then type the segments in that order.
let dateOrder = null;
async function learnDateOrder(ctx) {
  const p = await ctx.newPage();
  await p.setContent('<input id=a><input id=d type=date>');
  await p.focus('#a');
  await p.keyboard.press('Tab');
  await p.keyboard.type('04112030');
  dateOrder = (await p.inputValue('#d')) === '2030-04-11' ? 'mdy' : 'dmy';
  evidence.dateFieldOrder = dateOrder;
  await p.close();
}

async function typeDate(page, iso) {
  const [y, m, d] = iso.split('-');
  await page.keyboard.type(dateOrder === 'mdy' ? m + d + y : d + m + y);
}

async function waitText(page, selector, text) {
  await page.waitForFunction(([s, t]) => {
    const el = document.querySelector(s);
    return el && el.textContent.includes(t);
  }, [selector, text], { timeout: 10000 });
}

async function api(page, p) {
  return page.evaluate(async (u) => (await fetch(u, { cache: 'no-store' })).json(), p);
}

function nextIsoDay(iso, days) {
  const [y, m, d] = iso.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + days));
  return t.toISOString().slice(0, 10);
}

async function layoutCheck(page, base, routes, width, height, tag) {
  await page.setViewportSize({ width, height });
  for (const r of routes) {
    await page.goto(base + r);
    await page.waitForSelector('main h1');
    await page.waitForFunction(() => !document.querySelector('main')?.textContent.includes('Loading…'));
    const m = await page.evaluate(() => {
      const doc = document.documentElement;
      const buttons = [...document.querySelectorAll('main button, main input, main select, main textarea, main a')]
        .filter((el) => el.offsetParent !== null)
        .map((el) => el.getBoundingClientRect())
        .filter((b) => b.right > window.innerWidth + 1 || b.left < -1).length;
      return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth, offscreenControls: buttons };
    });
    check(`${tag}: ${r} has no horizontal overflow and all controls reachable`, m.scrollWidth <= m.clientWidth + 1 && m.offscreenControls === 0, m);
  }
}

// ---------------------------------------------------------------- isolation proof

async function proveIsolation(ctx, base, tag) {
  const probe = await ctx.newPage();
  probe._label = 'probe';
  const attempts = {};
  for (const url of ['https://example.com/', 'http://93.184.215.14/', 'https://fonts.googleapis.com/css2?family=Inter']) {
    try {
      await probe.goto(url, { timeout: 10000 });
      attempts[url] = 'LOADED';
    } catch (e) {
      attempts[url] = String(e.message).split('\n')[0];
    }
  }
  // closing a tab that sits on Chrome's network-error page can hang; leave it first
  await probe.goto('about:blank').catch(() => {});
  await probe.close();
  const loop = await ctx.newPage();
  let loopback;
  try {
    const resp = await loop.goto(base + '/api/v1/health');
    loopback = resp.status();
  } catch (e) {
    loopback = String(e.message).split('\n')[0];
  }
  await loop.close();
  evidence.isolation[tag] = { attempts, loopback };
  check(`${tag}: external requests fail while loopback works`,
    Object.values(attempts).every((v) => v !== 'LOADED') && loopback === 200, { attempts, loopback });
}

// ---------------------------------------------------------------- phase A

async function phaseA() {
  const dataA = path.join(WORK, 'data-a');
  let server = await startServer({ dataDir: dataA, port: PORT_A, now: '2030-04-10T10:00:00Z' });
  const base = server.base;
  const { ctx } = await launch('profile-a', 'America/Los_Angeles');
  const reqs = watchRequests(ctx, 'phaseA');
  await proveIsolation(ctx, base, 'phaseA');
  const A = await appPage(ctx, 'tabA');
  evidence.browser = await A.evaluate(() => navigator.userAgent);

  // ---- T01 first use and settings, keyboard only
  await A.goto(base + '/');
  await waitText(A, 'main', 'set up your workbench');
  check('T01 home offers setup on an empty data directory', true);
  const browserTime = await A.evaluate(() => ({
    local: new Date('2030-04-10T10:00:00Z').toString(),
    offsetMinutes: new Date('2030-04-10T10:00:00Z').getTimezoneOffset(),
  }));
  check('T11 browser timezone differs from Amsterdam (UTC+2 on that date)', browserTime.offsetMinutes !== -120, browserTime);
  await waitText(A, '#business-date', '2030-04-10');
  await tabTo(A, 'main a[href="/settings"]');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#settings-form');
  await tabTo(A, '#settings-form button[type=submit]');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#settings-form [aria-invalid="true"]');
  check('T01 blank settings rejected, focus moves to the invalid field',
    await focusedIs(A, '#settings-form-display_label'));
  await A.keyboard.type('Fictional Studio Workspace');
  await A.keyboard.press('Enter');
  await waitText(A, '#save-result', 'Settings saved');
  check('T01 settings saved by keyboard', true);

  // ---- T02/T03 create lead by keyboard
  await tabTo(A, 'nav a[href="/leads"]');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#add-lead-link');
  await tabTo(A, '#add-lead-link');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#lead-create-form');
  await tabTo(A, '#lead-create-form [name=label]');
  await A.keyboard.type('Fictional interface review');
  await tabTo(A, '#lead-create-form [name=country_tag]');
  await A.keyboard.type('Neth');
  await tabTo(A, '#lead-create-form [name=work_mode]');
  await A.keyboard.type('Rem');
  const createVals = await A.evaluate(() => ({
    c: document.querySelector('#lead-create-form [name=country_tag]').value,
    m: document.querySelector('#lead-create-form [name=work_mode]').value,
  }));
  check('keyboard type-ahead selected NL / remote', createVals.c === 'NL' && createVals.m === 'remote', createVals);
  await tabTo(A, '#lead-create-form button[type=submit]');
  await A.keyboard.press('Enter');
  await A.waitForURL(/\/leads\/\d+$/);
  await waitText(A, '#lead-stage', 'New');
  const leadUrl = A.url();
  const leadId = leadUrl.split('/').pop();
  check('T03 new lead at stage new, version 1', (await A.textContent('#lead-version')).trim() === '1');

  // ---- first outreach without an action is refused, input kept
  await tabTo(A, '#activity-form-summary');
  await A.keyboard.type('Recorded a fictional introduction about an interface review.');
  await tabTo(A, '#activity-form button[type=submit]');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#activity-form [aria-invalid="true"]');
  check('T04 first outreach without dated action refused in UI; focus on first invalid field',
    await focusedIs(A, '#activity-form-next_action_text'));
  check('input kept after validation failure',
    (await A.inputValue('#activity-form-summary')).startsWith('Recorded a fictional introduction'));

  // ---- first outreach with a dated follow-up (T03)
  await A.keyboard.type('Review the fictional reply');
  await tabTo(A, '#activity-form-next_action_due_date');
  await typeDate(A, '2030-04-11');
  check('due date typed via keyboard', (await A.inputValue('#activity-form-next_action_due_date')) === '2030-04-11');
  await tabTo(A, '#activity-form button[type=submit]');
  await A.keyboard.press('Enter');
  await waitText(A, '#save-result', 'Activity recorded');
  await waitText(A, '#lead-stage', 'Contacted');
  check('T03 contacted, version 2, one outreach in history',
    (await A.textContent('#lead-version')).trim() === '2' && (await A.locator('#activity-list li').count()) === 1);
  const audit = sql(dataA, `SELECT command_type, resulting_version, from_stage, to_stage FROM audit WHERE entity_type='lead' AND entity_id=${leadId} ORDER BY id`);
  check('T03 one success audit for the outreach command', JSON.stringify(audit[1]) === JSON.stringify(['activity.outreach', 2, 'new', 'contacted']) && audit.length === 2, audit);

  // ---- dashboard by keyboard, then open the row
  await tabTo(A, 'nav a[href="/"]');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#group-upcoming');
  const counts = await A.evaluate(() => Object.fromEntries(['overdue', 'today', 'upcoming', 'no_action']
    .map((k) => [k, document.querySelector('#group-' + k).dataset.count])));
  check('T03 dashboard: one upcoming, zero today, zero overdue', counts.upcoming === '1' && counts.today === '0' && counts.overdue === '0', counts);
  check('dashboard says nothing is due today', await A.isVisible('#nothing-due'));
  await tabTo(A, `#group-upcoming a[href="/leads/${leadId}"]`);
  await A.keyboard.press('Enter');
  await waitText(A, '#lead-next-action', 'Review the fictional reply');
  check('dashboard row opens the lead with the same action and date',
    (await A.textContent('#lead-next-action-due')).trim() === '2030-04-11');

  // ---- T15 markup-like input is literal text
  const markup = '<img src=x onerror=alert(1)><b>bold</b>';
  await A.goto(base + '/leads/new');
  await A.fill('#lead-create-form [name=label]', markup);
  await A.click('#lead-create-form button[type=submit]');
  await A.waitForURL(/\/leads\/\d+$/);
  await A.waitForSelector('#lead-stage');
  const lit = await A.evaluate(() => ({ h1: document.querySelector('main h1').textContent, imgs: document.querySelectorAll('main img, main b').length }));
  check('T15 markup-like label shown as literal text, no elements created', lit.h1 === markup && lit.imgs === 0, lit);
  await A.goto(base + '/leads?q=' + encodeURIComponent('<img'));
  await A.waitForSelector('#lead-list');
  check('T15 markup-like label literal in list too', (await A.locator('#lead-list').textContent()).includes(markup));

  // ---- unconfirmed create: a second Save click cannot create a duplicate (review D1)
  await A.goto(base + '/leads/new');
  await A.fill('#lead-create-form [name=label]', 'Fictional unconfirmed create');
  await A.route('**/api/v1/leads', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fetch();
    await route.abort('connectionreset');
  }, { times: 1 });
  await A.click('#lead-create-form button[type=submit]');
  await A.waitForSelector('#lead-create-form-uncertain-panel');
  await A.click('#lead-create-form button[type=submit]');
  const countCreate = async () => (await api(A, '/api/v1/leads?q=' + encodeURIComponent('unconfirmed create'))).total;
  check('unconfirmed create: pressing Save again sends nothing new', (await countCreate()) === 1 &&
    (await A.isVisible('#lead-create-form-uncertain-panel')));
  await A.click('#lead-create-form-uncertain-panel button[name=retry-same-save]');
  await A.waitForURL(/\/leads\/\d+$/);
  check('unconfirmed create: "Retry the same save" replays and opens the one lead', (await countCreate()) === 1);

  // ---- Back preserves filters
  await A.goto(base + '/leads?country=NL&mode=remote');
  await A.waitForSelector('#lead-list');
  await A.click(`#lead-list a[href="/leads/${leadId}"]`);
  await A.waitForSelector('#lead-stage');
  await A.goBack();
  await A.waitForSelector('#lead-list');
  check('Back restores the filtered list', A.url().endsWith('/leads?country=NL&mode=remote') &&
    (await A.inputValue('#lead-filter-form-country')) === 'NL');
  await A.click(`#lead-list a[href="/leads/${leadId}"]`);
  await A.click('#back-to-leads');
  await A.waitForSelector('#lead-list');
  check('"Back to leads" link restores the filters', A.url().endsWith('/leads?country=NL&mode=remote'));

  // ---- T10/T12 two-tab conflict
  await A.goto(leadUrl);
  const B = await appPage(ctx, 'tabB');
  await B.goto(leadUrl);
  await A.waitForSelector('#next-action-form');
  await B.waitForSelector('#next-action-form');
  await A.fill('#next-action-form-next_action_text', 'Tab A follow-up');
  await A.fill('#next-action-form-next_action_due_date', '2030-04-12');
  await A.click('#next-action-form button[type=submit]');
  await waitText(A, '#save-result', 'Follow-up saved');
  await B.fill('#next-action-form-next_action_text', 'Tab B different follow-up');
  await B.click('#next-action-form button[type=submit]');
  await B.waitForSelector('#next-action-form-conflict-panel');
  await waitText(B, '#next-action-form-conflict-panel', 'Tab A follow-up');
  check('T10 tab B gets a conflict, keeps its input and sees the current state',
    (await B.inputValue('#next-action-form-next_action_text')) === 'Tab B different follow-up');
  check('after the conflict, fields tab B had not touched show the current values (review D6)',
    (await B.inputValue('#next-action-form-next_action_due_date')) === '2030-04-12' &&
    (await B.inputValue('#activity-form-next_action_text')) === 'Tab A follow-up');
  let cur = await api(A, `/api/v1/leads/${leadId}`);
  check("T10 tab A's change survives", cur.lead.next_action.text === 'Tab A follow-up' && cur.lead.version === 3, cur.lead.next_action);
  // a deliberate second save from B applies its reviewed input on top of version 3
  await B.click('#next-action-form button[type=submit]');
  await waitText(B, '#save-result', 'Follow-up saved');
  cur = await api(A, `/api/v1/leads/${leadId}`);
  check('T10 a deliberate new save from tab B applies after review', cur.lead.next_action.text === 'Tab B different follow-up' &&
    cur.lead.next_action.due_date === '2030-04-12' && cur.lead.version === 4, cur.lead.next_action);

  // ---- T10 lost response then a newer edit, then replay
  await A.goto(leadUrl);
  await A.waitForSelector('#activity-form');
  const before = sql(dataA, `SELECT (SELECT count(*) FROM activity WHERE lead_id=${leadId}), (SELECT count(*) FROM audit), (SELECT version FROM lead WHERE id=${leadId})`)[0];
  let lost = null;
  await A.route('**/api/v1/leads/*/activities', async (route) => {
    const resp = await route.fetch();
    lost = resp.status();
    await route.abort('connectionreset');
  }, { times: 1 });
  await A.check('#activity-form-kind-note');
  await A.fill('#activity-form-summary', 'A fictional note whose response is lost.');
  await A.click('#activity-form button[type=submit]');
  await A.waitForSelector('#activity-form-uncertain-panel');
  const mid = sql(dataA, `SELECT (SELECT count(*) FROM activity WHERE lead_id=${leadId}), (SELECT count(*) FROM audit), (SELECT version FROM lead WHERE id=${leadId})`)[0];
  check('T10 save committed but response lost; UI says result unconfirmed and keeps input',
    lost === 201 && mid[0] === before[0] + 1 && (await A.inputValue('#activity-form-summary')) === 'A fictional note whose response is lost.', { lost, before, mid });
  // another form's validation does not remove this panel (review D2), and
  // pressing "Save activity" again sends nothing new (review D1)
  await A.fill('#next-action-form-next_action_text', '');
  await A.click('#next-action-form button[type=submit]');
  await A.waitForSelector('#next-action-form [aria-invalid="true"]');
  await A.click('#activity-form button[type=submit]');
  const mid2 = sql(dataA, `SELECT count(*) FROM activity WHERE lead_id=${leadId}`)[0][0];
  check('unconfirmed activity: panel survives other forms and a second Save sends nothing',
    (await A.isVisible('#activity-form-uncertain-panel')) && mid2 === mid[0], { mid2 });
  // a newer change from tab B
  await B.goto(leadUrl);
  await B.waitForSelector('#next-action-form');
  await B.fill('#next-action-form-next_action_text', 'Newer follow-up from tab B');
  await B.fill('#next-action-form-next_action_due_date', '2030-04-11');
  await B.click('#next-action-form button[type=submit]');
  await waitText(B, '#save-result', 'Follow-up saved');
  await A.click('#activity-form-uncertain-panel button[name=retry-same-save]');
  await waitText(A, '#save-result', 'already been recorded');
  const after = sql(dataA, `SELECT (SELECT count(*) FROM activity WHERE lead_id=${leadId}), (SELECT count(*) FROM audit), (SELECT version FROM lead WHERE id=${leadId}), (SELECT next_action_text FROM lead WHERE id=${leadId})`)[0];
  check('T10 retry replays: no duplicate activity, audit or version bump; newer action survives in SQLite',
    after[0] === mid[0] && after[1] === mid[1] + 1 && after[2] === mid[2] + 1 && after[3] === 'Newer follow-up from tab B', { mid, after });
  check('T10 newer action survives on screen after the replay',
    (await A.textContent('#lead-next-action-text')).trim() === 'Newer follow-up from tab B');

  // ---- narrow layout and 200% zoom equivalent
  const routes = ['/', '/leads', '/leads/new', `/leads/${leadId}`, '/settings'];
  await layoutCheck(A, base, routes, 320, 800, '320px');
  await layoutCheck(A, base, routes, 640, 450, '200% zoom (1280x900 at 2x)');
  await A.setViewportSize({ width: 1280, height: 900 });

  // ---- T09 actual restart; T11 date moves without writes
  const snapshot = sql(dataA, `SELECT version, stage, next_action_text, next_action_due FROM lead WHERE id=${leadId}`)[0];
  const actCount = sql(dataA, `SELECT count(*) FROM activity WHERE lead_id=${leadId}`)[0][0];
  const auditCount = sql(dataA, 'SELECT count(*) FROM audit')[0][0];
  const oldPid = server.info.pid;
  await server.stop();
  server = await startServer({ dataDir: dataA, port: PORT_A, now: '2030-04-11T10:00:00Z' });
  check('T09 a new server process was started', server.info.pid !== oldPid, { oldPid, newPid: server.info.pid });
  await A.goto(leadUrl);
  await waitText(A, '#lead-next-action', 'Newer follow-up from tab B');
  check('T09 lead, version, journal and action intact after restart',
    (await A.textContent('#lead-version')).trim() === String(snapshot[0]) && (await A.locator('#activity-list li').count()) === actCount);
  await A.goto(base + '/');
  await A.waitForSelector('#group-today');
  check('T11 at 2030-04-11 the unchanged action is in Today', (await A.getAttribute('#group-today', 'data-count')) === '1' &&
    (await A.locator(`#group-today a[href="/leads/${leadId}"]`).count()) === 1);
  await server.stop();
  server = await startServer({ dataDir: dataA, port: PORT_A, now: '2030-04-12T10:00:00Z' });
  await A.goto(base + '/');
  await A.waitForSelector('#group-overdue');
  check('T11 at 2030-04-12 the same action is Overdue', (await A.locator(`#group-overdue a[href="/leads/${leadId}"]`).count()) === 1);
  for (const [now, expect] of [['2030-04-09T21:59:00Z', '2030-04-09'], ['2030-04-09T22:00:00Z', '2030-04-10']]) {
    await server.stop();
    server = await startServer({ dataDir: dataA, port: PORT_A, now });
    await A.goto(base + '/');
    await A.waitForSelector('#business-date');
    const shown = await A.textContent('#business-date');
    check(`T11 ${now} shows Amsterdam business date ${expect} in a browser set to America/Los_Angeles`, shown.includes(expect), shown);
  }
  const snapshot2 = sql(dataA, `SELECT version, stage, next_action_text, next_action_due FROM lead WHERE id=${leadId}`)[0];
  check('T11 date reclassification caused no writes', JSON.stringify(snapshot2) === JSON.stringify(snapshot) &&
    sql(dataA, 'SELECT count(*) FROM audit')[0][0] === auditCount, { snapshot, snapshot2 });
  // tab B still holds the token of a server process that no longer exists:
  // its save renews the session and commits once
  await B.fill('#next-action-form-next_action_text', 'Saved after a server restart');
  await B.click('#next-action-form button[type=submit]');
  await waitText(B, '#save-result', 'Follow-up saved');
  check('an old tab renews its session token after restart and saves once',
    sql(dataA, `SELECT version FROM lead WHERE id=${leadId}`)[0][0] === snapshot[0] + 1);
  await server.stop();

  // ---- network inspection
  const appReqs = reqs.filter((r) => r.page === 'tabA' || r.page === 'tabB');
  const external = appReqs.filter((r) => !r.url.startsWith(base + '/'));
  check('T12 app tabs made only loopback requests (success and error paths)', external.length === 0 && appReqs.length > 0,
    { appRequests: appReqs.length, external });
  check('error paths were exercised (409 conflict, aborted response)', appReqs.some((r) => r.failed));
  await ctx.close();
  return { dataA };
}

// ---------------------------------------------------------------- phase B: cold load, production launcher

async function phaseB() {
  const dataB = path.join(WORK, 'data-b');
  const server = await startServer({ dataDir: dataB, port: PORT_B });
  const base = server.base;
  const { ctx } = await launch('profile-b-cold', 'Asia/Tokyo');
  const reqs = watchRequests(ctx, 'phaseB');
  await proveIsolation(ctx, base, 'phaseB');
  const P = await appPage(ctx, 'cold');
  await P.setViewportSize({ width: 320, height: 720 });
  await P.goto(base + '/');
  await waitText(P, 'main', 'set up your workbench');
  const bd = (await P.textContent('#business-date')).match(/\d{4}-\d{2}-\d{2}/)[0];
  await tabTo(P, 'main a[href="/settings"]');
  await P.keyboard.press('Enter');
  await P.waitForSelector('#settings-form');
  await tabTo(P, '#settings-form-display_label');
  await P.keyboard.type('Fictional demo workspace');
  await P.keyboard.press('Enter');
  await waitText(P, '#save-result', 'Settings saved');
  await tabTo(P, 'nav a[href="/leads"]');
  await P.keyboard.press('Enter');
  await P.waitForSelector('#add-lead-link');
  await tabTo(P, '#add-lead-link');
  await P.keyboard.press('Enter');
  await tabTo(P, '#lead-create-form [name=label]');
  await P.keyboard.type('Fictional accessibility audit');
  await P.keyboard.press('Enter');
  await P.waitForURL(/\/leads\/\d+$/);
  const leadId = P.url().split('/').pop();
  await tabTo(P, '#activity-form-summary');
  await P.keyboard.type('Sent a fictional introduction.');
  await tabTo(P, '#activity-form-next_action_text');
  await P.keyboard.type('Check for a fictional reply');
  await tabTo(P, '#activity-form-next_action_due_date');
  const due = nextIsoDay(bd, 1);
  await typeDate(P, due);
  await tabTo(P, '#activity-form button[type=submit]');
  await P.keyboard.press('Enter');
  await waitText(P, '#lead-stage', 'Contacted');
  await tabTo(P, 'nav a[href="/"]');
  await P.keyboard.press('Enter');
  await P.waitForSelector('#group-upcoming');
  check('cold load at 320px with the production launcher: setup -> lead -> outreach -> dashboard by keyboard',
    (await P.getAttribute('#group-upcoming', 'data-count')) === '1', { businessDate: bd, due });
  await tabTo(P, `#group-upcoming a[href="/leads/${leadId}"]`);
  await P.keyboard.press('Enter');
  await waitText(P, '#lead-next-action', 'Check for a fictional reply');
  check('cold path: dashboard row opens the lead detail', true);
  const ext = reqs.filter((r) => r.page === 'cold' && !r.url.startsWith(base + '/'));
  check('cold path: app tab made only loopback requests', ext.length === 0, { requests: reqs.filter((r) => r.page === 'cold').length, ext });
  check('production launch printed URL and database path', /URL:\s+http:\/\/127\.0\.0\.1:18782\//.test(server.info.banner) && server.info.banner.includes('workbench.sqlite3'));
  await ctx.close();
  await server.stop();
  check('demo/test directories are independent (phase B data has one lead)', sql(dataB, 'SELECT count(*) FROM lead')[0][0] === 1);
}

// ---------------------------------------------------------------- phase C: Discover (issue #1)

const CATALOG = JSON.parse(fs.readFileSync(path.join(ROOT, 'workbench', 'catalog', 'fictional-opportunities.json'), 'utf8')).items;

async function waitLoaded(page) {
  await page.waitForSelector('main h1');
  await page.waitForFunction(() => !document.querySelector('main')?.textContent.includes('Loading…'));
}

async function phaseC() {
  const dataC = path.join(WORK, 'data-c');
  const NOW = '2030-04-10T10:00:00Z';
  let server = await startServer({ dataDir: dataC, port: PORT_C, now: NOW });
  const base = server.base;
  const { ctx } = await launch('profile-c-discover', 'Europe/Amsterdam');
  const reqs = watchRequests(ctx, 'phaseC');
  await proveIsolation(ctx, base, 'phaseC');
  const leadCount = () => sql(dataC, 'SELECT count(*) FROM lead')[0][0];
  const createAudits = () => sql(dataC, "SELECT count(*) FROM audit WHERE command_type = 'lead.create'")[0][0];
  const A = await appPage(ctx, 'discoverA');

  // ---- browsing before setup: items visible, nothing to save, nothing written
  await A.goto(base + '/discover');
  await waitLoaded(A);
  check('Discover lists the whole bundled catalog', (await A.locator('li.catalog-item').count()) === CATALOG.length);
  check('without a profile Discover explains why it cannot save yet',
    (await A.isVisible('#profile-required')) && (await A.locator('#catalog-list form').count()) === 0);

  // ---- setup, then reach Discover from the navigation by keyboard
  await A.goto(base + '/settings');
  await tabTo(A, '#settings-form-display_label');
  await A.keyboard.type('Fictional Discover Studio');
  await A.keyboard.press('Enter');
  await waitText(A, '#save-result', 'Settings saved');
  await tabTo(A, 'nav a[href="/discover"]');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#catalog-filter-form');
  await waitLoaded(A);

  // ---- filter by all four filters, reaching each control with the keyboard
  const filters = { skill: 'Python', engagement: 'fixed-scope', country: 'NL', mode: 'remote' };
  const expected = CATALOG.filter((i) => i.skills.includes(filters.skill) && i.engagement_type === filters.engagement &&
    i.country_tag === filters.country && i.work_mode === filters.mode).map((i) => i.key);
  for (const [name, value] of Object.entries(filters)) {
    await tabTo(A, `#catalog-filter-form-${name}`);
    await A.selectOption(`#catalog-filter-form-${name}`, value);
  }
  await tabTo(A, '#catalog-filter-form button[type=submit]');
  await A.keyboard.press('Enter');
  await A.waitForURL(/\/discover\?/);
  await waitLoaded(A);
  const shownKeys = await A.locator('li.catalog-item').evaluateAll((els) => els.map((e) => e.dataset.catalogKey));
  check('filters by skill, engagement type, country and work mode combine with AND',
    JSON.stringify(shownKeys) === JSON.stringify(expected) && expected.length === 2, { url: A.url(), shownKeys, expected });
  check('the filters live in the URL', A.url().endsWith('/discover?skill=Python&engagement=fixed-scope&country=NL&mode=remote'), A.url());
  const filtered = A.url();
  await A.selectOption('#catalog-filter-form-country', 'other');
  await A.click('#catalog-filter-form button[type=submit]');
  await A.waitForURL(/country=other/);
  await waitLoaded(A);
  check('a filter with no matches says so', await A.isVisible('#no-matches'));
  await A.goBack();
  await A.waitForURL(filtered);
  await waitLoaded(A);
  check('Back restores the filtered catalog', A.url() === filtered && (await A.locator('li.catalog-item').count()) === 2);
  check('browsing and filtering created no leads and no audit rows', leadCount() === 0 && createAudits() === 0);

  // ---- two-tab duplicate save: tab A saves, tab B still shows "Save as lead"
  const [X, Y] = expected;
  const titleX = CATALOG.find((i) => i.key === X).title;
  const B = await appPage(ctx, 'discoverB');
  await B.goto(filtered);
  await waitLoaded(B);
  check('tab B shows the unsaved item', (await B.locator(`#save-${X} button`).count()) === 1);
  await tabTo(A, `#save-${X} button[type=submit]`);
  await A.keyboard.press('Enter');
  await A.waitForSelector(`#open-${X}`);
  await A.waitForFunction((id) => document.activeElement && document.activeElement.id === id, `open-${X}`);
  await waitText(A, '#status', 'Saved');
  check('tab A: Save as lead creates the lead, then shows Open lead with focus on it', leadCount() === 1 && createAudits() === 1);
  await B.click(`#save-${X} button[type=submit]`);
  await B.waitForSelector(`#open-${X}`);
  await waitText(B, '#status', 'no duplicate was created');
  const rowsX = sql(dataC, `SELECT id, label, source_channel, stage, version FROM lead WHERE catalog_key = '${X}'`);
  check('tab B (stale) gets the existing lead back: still one lead and one lead.create audit',
    rowsX.length === 1 && leadCount() === 1 && createAudits() === 1, rowsX);
  const hrefA = await A.getAttribute(`#open-${X}`, 'href');
  const hrefB = await B.getAttribute(`#open-${X}`, 'href');
  check('both tabs link to the same lead', hrefA === hrefB && hrefA === `/leads/${rowsX[0][0]}`, { hrefA, hrefB });
  check('the saved lead used the lead-creation rules', rowsX[0][1] === titleX && rowsX[0][2] === 'platform' &&
    rowsX[0][3] === 'new' && rowsX[0][4] === 1, rowsX[0]);

  // ---- both tabs press Save on the same item at the same moment
  await Promise.all([A.locator(`#save-${Y} button[type=submit]`).click(), B.locator(`#save-${Y} button[type=submit]`).click()]);
  await Promise.all([A.waitForSelector(`#open-${Y}`), B.waitForSelector(`#open-${Y}`)]);
  check('simultaneous saves from two tabs create one lead',
    sql(dataC, `SELECT count(*) FROM lead WHERE catalog_key = '${Y}'`)[0][0] === 1 && leadCount() === 2 && createAudits() === 2);
  await B.close();

  // ---- Open lead -> first outreach -> dashboard, by keyboard
  await tabTo(A, `#open-${X}`);
  await A.keyboard.press('Enter');
  await A.waitForURL(/\/leads\/\d+$/);
  await A.waitForSelector('#lead-stage');
  check('Open lead shows the saved lead with its catalog key',
    (await A.textContent('main h1')).includes(titleX) && (await A.textContent('#lead-catalog-key')).includes(X) &&
    (await A.textContent('#lead-stage')).includes('New'));
  await tabTo(A, '#activity-form-summary');
  await A.keyboard.type('Sent a fictional introduction about the API hardening.');
  await tabTo(A, '#activity-form-next_action_text');
  await A.keyboard.type('Check for the fictional reply');
  await tabTo(A, '#activity-form-next_action_due_date');
  await typeDate(A, '2030-04-11');
  await tabTo(A, '#activity-form button[type=submit]');
  await A.keyboard.press('Enter');
  await waitText(A, '#lead-stage', 'Contacted');
  check('first outreach moves the saved lead to Contacted, version 2',
    (await A.textContent('#lead-version')).trim() === '2' && (await A.locator('#activity-list li').count()) === 1);
  await tabTo(A, 'nav a[href="/"]');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#group-upcoming');
  const counts = {};
  for (const g of ['overdue', 'today', 'upcoming', 'no_action']) counts[g] = await A.getAttribute(`#group-${g}`, 'data-count');
  check('dashboard: the contacted lead is Upcoming, the other saved lead has No action',
    counts.upcoming === '1' && counts.no_action === '1' && counts.overdue === '0' && counts.today === '0' &&
    (await A.locator(`#group-upcoming a[href="${hrefA}"]`).count()) === 1, counts);
  await A.goto(base + '/discover');
  await waitLoaded(A);
  check('Discover marks both saved items with Open lead after a reload',
    (await A.locator(`#open-${X}`).count()) === 1 && (await A.locator(`#open-${Y}`).count()) === 1);

  // ---- catalog cannot load: Discover says so, manual capture still works
  await server.stop();
  server = await startServer({ dataDir: dataC, port: PORT_C, now: NOW, catalog: path.join(WORK, 'no-such-catalog.json') });
  await A.goto(base + '/discover');
  await A.waitForSelector('#catalog-unavailable');
  check('unavailable catalog: Discover says so and offers manual capture', await A.isVisible('#manual-capture-link'));
  await tabTo(A, '#manual-capture-link');
  await A.keyboard.press('Enter');
  await A.waitForSelector('#lead-create-form');
  await tabTo(A, '#lead-create-form [name=label]');
  await A.keyboard.type('Fictional lead added by hand');
  await A.keyboard.press('Enter');
  await A.waitForURL(/\/leads\/\d+$/);
  check('unavailable catalog: a lead can still be added manually',
    leadCount() === 3 && sql(dataC, "SELECT catalog_key FROM lead WHERE label = 'Fictional lead added by hand'")[0][0] === null);
  await A.goto(base + '/');
  await A.waitForSelector('#group-no_action');
  check('unavailable catalog: the dashboard still works', (await A.getAttribute('#group-no_action', 'data-count')) === '2');
  await server.stop();
  server = await startServer({ dataDir: dataC, port: PORT_C, now: NOW });

  // ---- layout
  await layoutCheck(A, base, ['/discover', filtered.slice(base.length)], 320, 800, 'Discover 320px');
  await layoutCheck(A, base, ['/discover'], 640, 450, 'Discover 200% zoom');
  await A.setViewportSize({ width: 1280, height: 900 });

  const appReqs = reqs.filter((r) => r.page === 'discoverA' || r.page === 'discoverB');
  const external = appReqs.filter((r) => !r.url.startsWith(base + '/'));
  check('Discover tabs made only loopback requests', external.length === 0 && appReqs.length > 0,
    { appRequests: appReqs.length, external });
  await ctx.close();
  await server.stop();
}

try {
  const { dataA } = await phaseA();
  await phaseB();
  await phaseC();
  check('phase A data unaffected by phase B', sql(dataA, 'SELECT count(*) FROM lead')[0][0] === 3);
} catch (e) {
  check('acceptance run completed without an exception', false, String(e && e.stack || e));
}
evidence.finished = new Date().toISOString();
evidence.failures = failures;
const outFile = path.join(WORK, 'evidence.json');
fs.writeFileSync(outFile, JSON.stringify(evidence, null, 2));
console.log(`\n${evidence.checks.length - failures}/${evidence.checks.length} checks passed. Evidence: ${outFile}`);
process.exit(failures ? 1 : 0);
