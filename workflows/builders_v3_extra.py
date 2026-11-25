"""SLA + Error + DLQ Recovery workflow builders (v3) for n8n 2.48.7.
Imported by build_workflows_v3.py. All Code JS uses $input / pairedItem, never bare `json`.
"""
import json  # noqa: F401  (kept for symmetry with the main builder)

# ---------------------------------------------------------------- CONFIG
# SINGLE SOURCE OF TRUTH for the HubSpot pipeline / New stage.
# Change the stage in HubSpot? Change these TWO lines only, then rebuild:
# every deal body (main + recovery) and the SLA search filter are generated
# from them, so they can never drift apart. build_workflows_v3.py imports
# these names; it defines no pipeline/stage of its own.
# NOTE: the literal stage id must appear NOWHERE else in either builder file.
PIPELINE_ID = "default"
STAGE_NEW_ID = "appointmentscheduled"

SLA_SEARCH_BODY = """={{ JSON.stringify({filterGroups: [{filters: [{propertyName: "dealstage", operator: "EQ", value: "%s"}]}], properties: ["dealname", "dealstage", "createdate", "reminder_sent", "follow_up_due", "lead_score", "band"], limit: 100}) }}""" % STAGE_NEW_ID

SLA_VALIDATE_SEARCH_JS = """const res = $input.first().json;
if (res.error) {
  return [{ json: { has_error: true, error_message: String((res.error && res.error.message) || res.error), results: [] } }];
}
const status = res.statusCode;
const body = res.body || {};
if (status < 200 || status >= 300 || !Array.isArray(body.results)) {
  return [{ json: { has_error: true, error_message: 'HubSpot deal search HTTP ' + status, results: [] } }];
}
return [{ json: { has_error: false, error_message: '', results: body.results } }];
"""

SLA_DUE_JS = """const j = $input.first().json;
const now = Date.now();
const out = [];
for (const d of (j.results || [])) {
  const p = d.properties || {};
  const band = p.band || '';
  const threshold = band === 'Hot' ? 24 : band === 'Warm' ? 48 : null;
  if (threshold === null) continue;
  // NO dealstage check here on purpose (round 4): the HubSpot search filter
  // in SLA_SEARCH_BODY is the SINGLE enforcement point for the New stage
  // (it queries dealstage EQ STAGE_NEW_ID). A second hard-coded check here
  // could drift from the search filter after a stage change in HubSpot and
  // silently kill all reminders. A deal that left New is already excluded
  // by the search itself -- proven by T11 (Hot Replied never reaches Due).
  const reminded = p.reminder_sent === true || p.reminder_sent === 'true';
  if (reminded) continue;
  const created = new Date(p.createdate).getTime();
  if (isNaN(created)) continue;
  const ageH = (now - created) / 3600000;
  if (ageH < threshold) continue;
  // mark_body is pre-serialised here: n8n 2.48.7's expression parser rejects
  // nested object literals ({properties: {...}}) with "invalid syntax".
  out.push({ json: { deal_id: String(d.id), dealname: p.dealname || '', band: band,
    threshold_hours: threshold, age_hours: Math.round(ageH), createdate: p.createdate,
    mark_body: JSON.stringify({properties: {reminder_sent: "true", follow_up_due: "true"}}) } });
}
return out;
"""

# Pre-serialised by SLA_DUE_JS (nested object literals are rejected by
# n8n 2.48.7's expression parser). The search body is safe: it nests
# objects inside ARRAYS only.
SLA_MARK_BODY = """={{ $json.mark_body }}"""

SLA_VALIDATE_MARK_JS = """// Loops over ALL input items: SLA Due? can emit several deals per run.
// (A $input.first() version silently dropped every deal after the first.)
const out = [];
for (const item of $input.all()) {
  const paired = item.pairedItem;
  const deal = $('SLA Due?').all()[paired ? paired.item : 0].json;
  const res = item.json;
  if (res.error) {
    out.push({ json: { ...deal, mark_ok: false, error_message: String((res.error && res.error.message) || res.error) } });
    continue;
  }
  const status = res.statusCode;
  const body = res.body || {};
  if (status < 200 || status >= 300 || !body.id) {
    out.push({ json: { ...deal, mark_ok: false, error_message: 'HubSpot update deal HTTP ' + status } });
    continue;
  }
  out.push({ json: { ...deal, mark_ok: true, error_message: '' } });
}
return out;
"""

SLA_PREPARE_ALERT_JS = """// Loops over ALL input items (one alert per due deal).
return $input.all().map(function(item){
  const j = item.json;
  return { json: { ...j, alert_text: 'SLA reminder: ' + j.dealname + ' — ' + j.band + ' — no reply in ' + j.threshold_hours + 'h' } };
});
"""

SLA_CHECK_TELEGRAM_JS = """// Loops over ALL input items (one Telegram result per due deal).
const out = [];
for (const item of $input.all()) {
  const paired = item.pairedItem;
  const j = $('Prepare SLA Alert').all()[paired ? paired.item : 0].json;
  const res = item.json;
  const failed = !!res.error || (typeof res.statusCode === 'number' && (res.statusCode < 200 || res.statusCode >= 300));
  out.push({ json: { ...j, telegram_failed: failed, telegram_error: failed ? String((res.error && res.error.message) || ('HTTP ' + res.statusCode)) : '' } });
}
return out;
"""

SLA_AUDIT_COLUMNS = ["deal_id", "action", "band", "threshold_hours", "telegram_failed", "created_at"]
SLA_PREPARE_AUDIT_JS = """// Loops over ALL input items (one audit row per due deal).
return $input.all().map(function(item){
  const j = item.json;
  return { json: {
    deal_id: j.deal_id || '',
    action: j.mark_ok === false ? 'mark_failed' : (j.has_error ? 'search_failed' : 'reminder_sent'),
    band: j.band || '',
    threshold_hours: String(j.threshold_hours || ''),
    telegram_failed: j.telegram_failed ? 'true' : 'false',
    created_at: new Date().toISOString()
  }};
});
"""

ERROR_EXTRACT_JS = """const j = $input.first().json;
const execution = j.execution || {};
const workflow = j.workflow || {};
const err = j.error || execution.error || {};
return [{ json: {
  execution_id: String(execution.id || j.executionId || ''),
  workflow_name: String(workflow.name || j.workflowName || ''),
  failed_node: String((err.node && err.node.name) || j.nodeName || ''),
  error_message: String((err.message) || j.message || 'unknown error').slice(0, 500),
  stage_failed: 'error_trigger',
  status: 'pending',
  created_at: new Date().toISOString(),
  lead_id: '', name: '', phone_normalized: '', email_normalized: '', product: '',
  budget_egp: '', band: '', score: '', contact_id: '', deal_id: '', retry_count: '0'
}}];
"""

ERROR_ALERT_JS = """const j = $input.first().json;
return [{ json: { ...j, alert_text: 'Workflow error before lead was recorded. Execution ' + j.execution_id + ' in workflow ' + j.workflow_name + '. Error: ' + j.error_message + '. Recover the lead from the saved failed execution in n8n (Executions list).' } }];
"""

RECOVERY_FILTER_JS = """const rows = $input.all().map(function(i){ return i.json; });
const out = [];
for (const r of rows) {
  if (String(r.status || '').toLowerCase() !== 'pending') continue;
  const retry = Number(r.retry_count) || 0;
  if (retry >= 3) continue;
  out.push({ json: (function(){
    const first = String(r.name || '').split(/\\s+/)[0] || '';
    const last = String(r.name || '').split(/\\s+/).slice(1).join(' ') || '';
    // HubSpot bodies pre-serialised here (real JS): n8n 2.48.7's expression
    // parser rejects nested object literals ({properties: {...}}).
    const contactBody = JSON.stringify({properties: {email: r.email_normalized || '', phone: r.phone_normalized || '', firstname: first, lastname: last}});
    const dealBody = JSON.stringify({properties: {dealname: (r.name || '') + ' - ' + (r.product || ''), amount: Number(r.budget_egp) || 0, pipeline: '%s', dealstage: '%s', lead_score: Number(r.score) || 0, band: r.band || '', reminder_sent: 'false', follow_up_due: 'false'}});
    return {
    lead_id: r.lead_id || '', name: r.name || '', phone_normalized: r.phone_normalized || '',
    email_normalized: r.email_normalized || '', product: r.product || '',
    budget_egp: Number(r.budget_egp) || 0, band: r.band || '', score: Number(r.score) || 0,
    stage_failed: r.stage_failed || '', error_message: r.error_message || '',
    contact_id: r.contact_id || '', deal_id: r.deal_id || '', retry_count: retry,
    created_at: r.created_at || '',
    has_contact: !!(r.contact_id || ''),
    dlq_stage_failed: r.stage_failed || '',
    firstname: first,
    lastname: last,
    create_contact_body: contactBody, update_contact_body: contactBody,
    create_deal_body: dealBody,
    source: 'dlq-recovery'
  };})()});
}
return out;
""" % (PIPELINE_ID, STAGE_NEW_ID)

RECOVERY_VALIDATE_SEARCH_JS = """// Loops over ALL input items: several DLQ rows can be pending at once.
// (A $input.first() version silently dropped every row after the first.)
const out = [];
for (const item of $input.all()) {
  // item.json is the MERGED item: lead fields + HTTP response fields.
  // Strip the response envelope before spreading: a stale `body` would
  // deep-merge into the NEXT validator's res.body (proven: a contact
  // response body.id overwrote the deal response body.id).
  const strip = function(o){ const c = Object.assign({}, o); delete c.body; delete c.statusCode; delete c.statusMessage; delete c.headers; delete c.error; return c; };
  const lead = strip(item.json);
  const res = item.json;
  if (res.error) {
    out.push({ json: { ...lead, has_error: true, error_message: String((res.error && res.error.message) || res.error), match_count: 0, is_duplicate: false, match_id: null } });
    continue;
  }
  const status = res.statusCode;
  const body = res.body || {};
  if (status < 200 || status >= 300 || !Array.isArray(body.results)) {
    out.push({ json: { ...lead, has_error: true, error_message: 'HubSpot search HTTP ' + status, match_count: 0, is_duplicate: false, match_id: null } });
    continue;
  }
  const results = body.results;
  const match = results[0] || null;
  out.push({ json: { ...lead, has_error: false, error_message: '', match_count: results.length, is_duplicate: results.length > 0, match_id: match ? String(match.id) : null } });
}
return out;
"""

RECOVERY_VALIDATE_CONTACT_JS = """// Loops over ALL input items (one per pending DLQ row).
const out = [];
for (const item of $input.all()) {
  // item.json is the MERGED item: lead fields + HTTP response fields.
  // Strip the response envelope before spreading: a stale `body` would
  // deep-merge into the NEXT validator's res.body (proven: a contact
  // response body.id overwrote the deal response body.id).
  const strip = function(o){ const c = Object.assign({}, o); delete c.body; delete c.statusCode; delete c.statusMessage; delete c.headers; delete c.error; return c; };
  const base = strip(item.json);
  const res = item.json;
  if (res.error) {
    out.push({ json: { ...base, has_error: true, error_message: String((res.error && res.error.message) || res.error), stage_failed: 'recovery_contact' } });
    continue;
  }
  const status = res.statusCode;
  const body = res.body || {};
  if (status < 200 || status >= 300 || !body.id) {
    out.push({ json: { ...base, has_error: true, error_message: 'HubSpot contact HTTP ' + status, stage_failed: 'recovery_contact' } });
    continue;
  }
  out.push({ json: { ...base, has_error: false, error_message: '', contact_id: String(body.id) } });
}
return out;
"""

RECOVERY_VALIDATE_DEAL_JS = """// Loops over ALL input items (one per pending DLQ row).
const out = [];
for (const item of $input.all()) {
  // item.json is the MERGED item: lead fields + HTTP response fields.
  // Strip the response envelope before spreading: a stale `body` would
  // deep-merge into the NEXT validator's res.body (proven: a contact
  // response body.id overwrote the deal response body.id).
  const strip = function(o){ const c = Object.assign({}, o); delete c.body; delete c.statusCode; delete c.statusMessage; delete c.headers; delete c.error; return c; };
  const base = strip(item.json);
  const res = item.json;
  if (res.error) {
    out.push({ json: { ...base, has_error: true, error_message: String((res.error && res.error.message) || res.error), stage_failed: 'recovery_deal' } });
    continue;
  }
  const status = res.statusCode;
  const body = res.body || {};
  if (status < 200 || status >= 300 || !body.id) {
    out.push({ json: { ...base, has_error: true, error_message: 'HubSpot deal HTTP ' + status, stage_failed: 'recovery_deal' } });
    continue;
  }
  out.push({ json: { ...base, has_error: false, error_message: '', deal_id: String(body.id) } });
}
return out;
"""

RECOVERY_VALIDATE_ASSOC_JS = """// Loops over ALL input items (one per recovered deal).
// Association failure is NON-FATAL on purpose (round 4): the Deal already
// exists and the Contact already exists, so the row is still recovered --
// retrying would create a SECOND Deal (recovery dedup only re-checks the
// Contact). The failure is recorded in error_message so a human sees it in
// the DLQ row; the PUT itself is idempotent and retries 3x at node level.
// Input is the MERGED item: lead fields + HTTP response fields; the response
// envelope is stripped before spreading (same stale-body hazard as the other
// recovery validators).
const out = [];
for (const item of $input.all()) {
  const strip = function(o){ const c = Object.assign({}, o); delete c.body; delete c.statusCode; delete c.statusMessage; delete c.headers; delete c.error; return c; };
  const base = strip(item.json);
  const res = item.json;
  if (res.error) {
    out.push({ json: { ...base, has_error: false, association_ok: false, association_failed: true, error_message: 'HubSpot association failed: ' + String((res.error && res.error.message) || res.error) } });
    continue;
  }
  const status = res.statusCode;
  if (typeof status === 'number' && (status < 200 || status >= 300)) {
    out.push({ json: { ...base, has_error: false, association_ok: false, association_failed: true, error_message: 'HubSpot association HTTP ' + status } });
    continue;
  }
  out.push({ json: { ...base, has_error: false, association_ok: true, association_failed: false, error_message: '' } });
}
return out;
"""

RECOVERY_MARK_JS = """// Loops over ALL input items (one mark per pending DLQ row).
return $input.all().map(function(item){
const j = item.json;
const ok = !j.has_error;
return { json: {
  lead_id: j.lead_id || '',
  name: j.name || '',
  phone_normalized: j.phone_normalized || '',
  email_normalized: j.email_normalized || '',
  product: j.product || '',
  budget_egp: String(j.budget_egp || ''),
  band: j.band || '',
  score: String(j.score || ''),
  stage_failed: j.dlq_stage_failed || j.stage_failed || '',
  error_message: String(j.error_message || '').slice(0, 500),
  contact_id: j.contact_id || '',
  deal_id: j.deal_id || '',
  status: ok ? 'recovered' : 'pending',
  retry_count: String((Number(j.retry_count) || 0) + (ok ? 0 : 1)),
  created_at: j.created_at || '',
  recovered_at: ok ? new Date().toISOString() : ''
}};
});
"""
