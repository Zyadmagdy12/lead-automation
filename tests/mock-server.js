#!/usr/bin/env node
/**
 * Mock HubSpot / Google Sheets / Telegram server for testing Sample 1 workflows
 * in real n8n 2.48.7 WITHOUT user credentials.
 *
 * - HubSpot CRM API v3 subset, in-memory, with deterministic failure injection:
 *   - contact search filter value "201000000000" -> 500 (search failure)
 *   - contact create with email containing "fail-contact" -> 500 (persistent,
 *     so a DLQ retry still fails -- T10)
 *   - deal create with dealname containing "FAIL_DEAL" -> 500 ONCE per dealname
 *     (transient failure: the DLQ retry succeeds -- T8; creates are NOT
 *     retried by the workflow, so one failure is enough)
 *   - telegram text containing "FAIL_TELEGRAM" -> 500
 * - Sheets: /sheets/append, /sheets/read (returns rows ARRAY), /sheets/appendOrUpdate
 * - GET /log returns the full call log + state (assertions read this)
 * - POST /reset clears state; POST /seed/deals seeds SLA deals; POST /seed/dlq seeds DLQ rows
 *
 * Run: node mock-server.js  (listens on 127.0.0.1:5999)
 */
const http = require('http');

const state = {
  contacts: [],   // {id, properties}
  deals: [],      // {id, properties}
  calls: [],      // {method, path, body, status, at}
  sheets: { Dead_Letter_Queue: [], Audit_Log: [] },
  telegram: [],   // {chat_id, text, at}
  dealFailedOnce: new Set(),  // dealnames whose first create already failed once
  dealFailCount: {},  // dealname -> number of failed create attempts (transient)
};
let nextContact = 1001;
let nextDeal = 2001;
let nextMsg = 1;

function send(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(body);
}
function logCall(method, path, body, status) {
  state.calls.push({ method, path, body, status, at: new Date().toISOString() });
}
function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; });
    req.on('end', () => {
      try { resolve(data ? JSON.parse(data) : {}); } catch (e) { resolve({}); }
    });
  });
}
function matchesFilters(properties, filterGroups) {
  // OR between groups, AND inside a group. Only EQ is used by the workflows.
  for (const group of filterGroups || []) {
    const ok = (group.filters || []).every((f) => {
      const v = properties[f.propertyName];
      if (f.operator === 'EQ') return String(v) === String(f.value);
      return false;
    });
    if (ok) return true;
  }
  return false;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1:5999');
  const path = url.pathname;
  const method = req.method;
  const body = ['POST', 'PATCH', 'PUT'].includes(method) ? await readBody(req) : {};

  if (method === 'GET' && path === '/log') return send(res, 200, state);
  // Echo endpoint for pairing probes: returns the posted body as {echo_id}.
  if (method === 'POST' && path === '/debug-echo') {
    logCall(method, path, body, 200);
    return send(res, 200, { echo_id: body.id || null, ok: true });
  }
  if (method === 'POST' && path === '/reset') {
    state.contacts = []; state.deals = []; state.calls = [];
    state.sheets = { Dead_Letter_Queue: [], Audit_Log: [] };
    state.telegram = [];
    state.dealFailedOnce = new Set();
    state.dealFailCount = {};
    nextContact = 1001; nextDeal = 2001; nextMsg = 1;
    return send(res, 200, { ok: true });
  }
  if (method === 'POST' && path === '/seed/deals') {
    for (const d of body.deals || []) {
      state.deals.push({ id: 'd' + (nextDeal++), properties: d });
    }
    return send(res, 200, { ok: true, count: state.deals.length });
  }
  if (method === 'POST' && path === '/seed/dlq') {
    for (const r of body.rows || []) state.sheets.Dead_Letter_Queue.push(r);
    return send(res, 200, { ok: true, count: state.sheets.Dead_Letter_Queue.length });
  }

  // ---- HubSpot: search contacts
  if (method === 'POST' && path === '/crm/v3/objects/contacts/search') {
    const vals = [];
    for (const g of body.filterGroups || []) for (const f of g.filters || []) vals.push(String(f.value));
    if (vals.includes('201000000000')) { logCall(method, path, body, 500); return send(res, 500, { message: 'simulated search failure' }); }
    const results = state.contacts.filter((c) => matchesFilters(c.properties, body.filterGroups));
    logCall(method, path, body, 200);
    return send(res, 200, { results: results.map((c) => ({ id: c.id, properties: c.properties })), total: results.length });
  }
  // ---- HubSpot: create contact
  if (method === 'POST' && path === '/crm/v3/objects/contacts') {
    const p = body.properties || {};
    if (String(p.email || '').includes('fail-contact')) { logCall(method, path, body, 500); return send(res, 500, { message: 'simulated contact create failure' }); }
    const c = { id: 'c' + (nextContact++), properties: p };
    state.contacts.push(c);
    logCall(method, path, body, 201);
    return send(res, 201, { id: c.id, properties: c.properties });
  }
  // ---- HubSpot: update contact
  const mContact = path.match(/^\/crm\/v3\/objects\/contacts\/([^/]+)$/);
  if (method === 'PATCH' && mContact) {
    const c = state.contacts.find((x) => x.id === mContact[1]);
    if (!c) { logCall(method, path, body, 404); return send(res, 404, { message: 'contact not found' }); }
    Object.assign(c.properties, body.properties || {});
    logCall(method, path, body, 200);
    return send(res, 200, { id: c.id, properties: c.properties });
  }
  // ---- HubSpot: search deals
  if (method === 'POST' && path === '/crm/v3/objects/deals/search') {
    const results = state.deals.filter((d) => matchesFilters(d.properties, body.filterGroups));
    const props = body.properties || [];
    logCall(method, path, body, 200);
    return send(res, 200, {
      results: results.map((d) => {
        const out = {};
        for (const k of props) out[k] = d.properties[k];
        return { id: d.id, properties: out };
      }),
      total: results.length,
    });
  }
  // ---- HubSpot: create deal
  if (method === 'POST' && path === '/crm/v3/objects/deals') {
    const p = body.properties || {};
    // Transient failure: fail the FIRST create attempt per dealname only.
    // The workflow does NOT retry creates (a retry would re-create the
    // resource), so one failure is enough to prove the DLQ path (T6) while
    // the DLQ retry then succeeds (T8).
    if (String(p.dealname || '').includes('FAIL_DEAL')) {
      const n = (state.dealFailCount[p.dealname] || 0) + 1;
      state.dealFailCount[p.dealname] = n;
      if (n <= 1) { logCall(method, path, body, 500); return send(res, 500, { message: 'simulated deal create failure' }); }
    }
    const d = { id: 'd' + (nextDeal++), properties: { ...p, createdate: new Date().toISOString() } };
    state.deals.push(d);
    logCall(method, path, body, 201);
    return send(res, 201, { id: d.id, properties: d.properties });
  }
  // ---- HubSpot: update deal
  const mDeal = path.match(/^\/crm\/v3\/objects\/deals\/([^/]+)$/);
  if (method === 'PATCH' && mDeal) {
    const d = state.deals.find((x) => x.id === mDeal[1]);
    if (!d) { logCall(method, path, body, 404); return send(res, 404, { message: 'deal not found' }); }
    Object.assign(d.properties, body.properties || {});
    logCall(method, path, body, 200);
    return send(res, 200, { id: d.id, properties: d.properties });
  }
  // ---- HubSpot: associate deal -> contact
  const mAssoc = path.match(/^\/crm\/v3\/objects\/deals\/([^/]+)\/associations\/contacts\/([^/]+)\/deal_to_contact$/);
  if (method === 'PUT' && mAssoc) {
    const d = state.deals.find((x) => x.id === mAssoc[1]);
    const c = state.contacts.find((x) => x.id === mAssoc[2]);
    if (!d || !c) { logCall(method, path, body, 404); return send(res, 404, { message: 'deal or contact not found' }); }
    d.properties.associated_contact = mAssoc[2];
    logCall(method, path, body, 200);
    return send(res, 200, { status: 'ok' });
  }
  // ---- Sheets
  if (method === 'POST' && path === '/sheets/append') {
    const sheet = body.sheet || 'Audit_Log';
    if (!state.sheets[sheet]) state.sheets[sheet] = [];
    if (String((body.row && body.row.lead_id) || '') === 'FAIL_SHEETS') { logCall(method, path, body, 500); return send(res, 500, { message: 'simulated sheets failure' }); }
    state.sheets[sheet].push(body.row || {});
    logCall(method, path, body, 200);
    return send(res, 200, { ok: true, sheet, rowCount: state.sheets[sheet].length });
  }
  if (method === 'POST' && path === '/sheets/read') {
    const sheet = body.sheet || 'Dead_Letter_Queue';
    logCall(method, path, body, 200);
    return send(res, 200, state.sheets[sheet] || []);
  }
  if (method === 'POST' && path === '/sheets/appendOrUpdate') {
    const sheet = body.sheet || 'Dead_Letter_Queue';
    if (!state.sheets[sheet]) state.sheets[sheet] = [];
    const row = body.row || {};
    const idx = state.sheets[sheet].findIndex((r) => r.lead_id === row.lead_id);
    if (idx >= 0) state.sheets[sheet][idx] = { ...state.sheets[sheet][idx], ...row };
    else state.sheets[sheet].push(row);
    logCall(method, path, body, 200);
    return send(res, 200, { ok: true, sheet, rowCount: state.sheets[sheet].length });
  }
  // ---- Telegram
  if (method === 'POST' && path === '/telegram/send') {
    if (String(body.text || '').includes('FAIL_TELEGRAM')) { logCall(method, path, body, 500); return send(res, 500, { message: 'simulated telegram failure' }); }
    state.telegram.push({ chat_id: body.chat_id, text: body.text, at: new Date().toISOString() });
    logCall(method, path, body, 200);
    return send(res, 200, { ok: true, result: { message_id: nextMsg++ } });
  }

  logCall(method, path, body, 404);
  return send(res, 404, { message: 'mock: no such route ' + method + ' ' + path });
});

server.listen(5999, '127.0.0.1', () => {
  console.log('mock hubspot/sheets/telegram listening on 127.0.0.1:5999');
});
