#!/usr/bin/env python3
"""Build Sample 1 Lead Automation workflows v3 for n8n 2.48.7.

Schema sources (all read from the installed n8n 2.48.7 package, not guessed):
- Code node v2: $input / $json are defined; bare `json` is NOT defined (proven by
  executing a probe workflow in local n8n 2.48.7).
- FormTrigger v2.2: formFields is a fixedCollection {values: [{fieldLabel, fieldType,
  requiredField, placeholder}]}; for <2.4 the submitted key is the fieldLabel.
- If v2.2: conditions is a filter object {options, conditions: [{id, leftValue,
  rightValue, operator: {type, operation, name}}], combinator}.
- HubSpot node V2 (2.48.7): contact operations are upsert/delete/get/getAll/
  getRecentlyCreatedUpdated/search -- there is NO update-by-id for contacts, so
  phone-merge (different email) cannot be done with the node. Contact search
  propertyName and deal stage/pipeline are loadOptions (live HubSpot data).
  => All HubSpot calls use the HubSpot REST API v3 via HTTP Request nodes with
  one Header Auth credential (Service Key as Bearer). This is explicit, and
  testable against a mock server without user credentials.
- Google Sheets node v4.7: documentId/sheetName are resourceLocators; append uses
  a resourceMapper `columns` with a schema built from the known tab headers.
- Telegram node v1.2: resource "message", operation "sendMessage", chatId is a
  plain string, text is a string.
- HTTP Request node v4.5: parameters are requestMethod/url/jsonParameters/
  bodyParametersJson/options{fullResponse}/authentication+genericAuthType.

Two variants are built from the same node definitions:
  real: HubSpot https://api.hubapi.com, native Sheets/Telegram nodes (deliverable)
  mock: HubSpot/Sheets/Telegram all via HTTP to http://127.0.0.1:5999 (test only)

Run: python3 build_workflows_v3.py
"""
import json, pathlib, copy

# Pipeline / New stage: SINGLE SOURCE OF TRUTH lives in builders_v3_extra.py
# (CONFIG block). This file imports it; it defines no pipeline/stage itself.
from builders_v3_extra import PIPELINE_ID, STAGE_NEW_ID

BASE = pathlib.Path(__file__).parent
HUBSPOT_REAL = "https://api.hubapi.com"
MOCK = "http://127.0.0.1:5999"
SHEETS_URL = "https://docs.google.com/spreadsheets/d/FILL_SHEETS_ID/edit"
TELEGRAM_CHAT = "FILL_TELEGRAM_CHAT_ID"

def node(id_, name, type_, params, pos, creds=None, version=2, extra=None):
    n = {"id": id_, "name": name, "parameters": params, "position": list(pos),
         "type": type_, "typeVersion": version}
    if creds:
        n["credentials"] = creds
    if extra:
        n.update(extra)
    return n

HUB_CREDS = {"httpHeaderAuth": {"id": None, "name": "HubSpot Service Key"}}
SHEETS_CREDS = {"googleSheetsOAuth2": {"id": None, "name": "Google Sheets — fill on import"}}
TG_CREDS = {"telegramApi": {"id": None, "name": "Telegram Bot — fill on import"}}

def http_node(id_, name, method, url, body_json, pos, base, continue_on_fail=True, full_response=True,
              retry_on_fail=True):
    # retry_on_fail=True is ONLY safe for idempotent calls (reads, PATCH, PUT).
    # A POST create retried by n8n re-runs the WHOLE node batch: an item that
    # already succeeded is created AGAIN (proven: one failing item in a batch
    # made the node re-create the successful contact 3 times). Creates and
    # plain appends therefore pass retry_on_fail=False; the DLQ recovery
    # workflow (with its dedup re-check) is the retry mechanism for them.
    # url starting with "=" is a full expression (dynamic path); otherwise base+url
    full_url = url if url.startswith("=") else base + url
    # HTTP Request V3 (typeVersion 4.5 in n8n 2.48.7): parameter names are
    # method / sendBody / contentType / specifyBody / jsonBody, and the response
    # options are nested under options.response.response. (V2 names requestMethod /
    # jsonParameters / bodyParametersJson are silently IGNORED by 4.5 -- proven by
    # a live probe: POST nodes were actually sent as GET.)
    params = {
        "method": method,
        "url": full_url,
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": True,
        "contentType": "json",
        "specifyBody": "json",
        "options": {"response": {"response": {"fullResponse": full_response, "responseFormat": "json"}}},
    }
    if body_json is not None:
        # jsonBody is stored exactly as given. Proven by live probes:
        #  - a plain JSON string with inline {{ }} is sent LITERALLY;
        #  - a full expression (={{ JSON.stringify(...) }}) is evaluated;
        #  - n8n 2.48.7's expression parser rejects NESTED object literals
        #    ({properties: {...}}) with "invalid syntax", so HubSpot bodies
        #    are pre-serialised in the upstream Code node and referenced
        #    here as ={{ $json.<body_field> }}.
        params["jsonBody"] = body_json
    extra = {"continueOnFail": continue_on_fail}
    if retry_on_fail:
        extra.update({"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 5000})
    return node(id_, name, "n8n-nodes-base.httpRequest", params, pos,
                creds=HUB_CREDS, version=4.5, extra=extra)

def merge_node(id_, name, pos):
    # Merge v3 (n8n 2.48.7): combine by position. The merged item carries BOTH
    # the upstream item's fields AND the HTTP response fields (statusCode,
    # body, headers) -- proven by a live probe. This is how a validator reads
    # the lead data after an HTTP node replaced the item, without the fragile
    # $('Node').all()[paired.item] lookup (which breaks across IF branches:
    # an IF re-bases pairedItem to the branch-local index).
    return node(id_, name, "n8n-nodes-base.merge",
                {"mode": "combine", "combineBy": "combineByPosition", "numberInputs": 2, "options": {}},
                pos, version=3)

def code_node(id_, name, js, pos):
    return node(id_, name, "n8n-nodes-base.code",
                {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": js},
                pos, version=2)

def if_node(id_, name, left_expr, pos):
    return node(id_, name, "n8n-nodes-base.if",
                {"conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                    "conditions": [{"id": "cond-" + id_, "leftValue": left_expr, "rightValue": True,
                                    "operator": {"type": "boolean", "operation": "true",
                                                 "name": "filter.operator.boolean.true"}}],
                    "combinator": "and"},
                 "looseTypeValidation": True},
                pos, version=2.2)

def sheets_append(id_, name, tab, columns_value, schema, pos):
    return node(id_, name, "n8n-nodes-base.googleSheets",
                {"resource": "sheet", "operation": "append",
                 "documentId": {"__rl": True, "mode": "url", "value": SHEETS_URL},
                 "sheetName": {"__rl": True, "mode": "list", "value": tab, "cachedResultName": tab},
                 "columns": {"mappingMode": "defineBelow", "value": columns_value,
                             "matchingColumns": [], "schema": schema},
                 "options": {}},
                pos, creds=SHEETS_CREDS, version=4.7,
                extra={"continueOnFail": True})

def sheets_col(name):
    return {"id": name, "displayName": name, "required": False, "display": True,
            "type": "string", "canBeUsedToMatch": True, "removed": False}

def telegram_node(id_, name, pos):
    return node(id_, name, "n8n-nodes-base.telegram",
                {"resource": "message", "operation": "sendMessage",
                 "chatId": TELEGRAM_CHAT, "text": "={{ $json.alert_text }}",
                 "additionalFields": {"appendAttribution": False}},
                pos, creds=TG_CREDS, version=1.2,
                extra={"continueOnFail": True})

# ---------------------------------------------------------------- Code JS
NORMALIZE_JS = """const lead = $input.first().json;
const rawPhone = String(lead.phone || '').replace(/[\\s\\-()]/g, '');
let phone = rawPhone;
if (phone.startsWith('+20')) phone = '20' + phone.slice(3);
else if (phone.startsWith('0020')) phone = '20' + phone.slice(4);
else if (phone.startsWith('0')) phone = '20' + phone.slice(1);
const email = String(lead.email || '').trim().toLowerCase();
const name = String(lead.name || '').trim();
const parts = name.split(/\\s+/).filter(Boolean);
const budget = Number(lead.budget_egp) || 0;
return [{ json: {
  ...lead,
  name: name,
  phone_normalized: phone,
  email_normalized: email,
  firstname: parts[0] || '',
  lastname: parts.slice(1).join(' ') || '',
  budget_egp: budget,
  source: lead.source || 'form',
  lead_id: 'lead-' + phone + '-' + Math.floor(Date.now() / 1000)
}}];
"""

SCORE_JS = """const lead = $input.first().json;
const msg = String(lead.message || '').toLowerCase();
const JUNK = ['test','تجربة','وظيفة','cv','السيرة','قرض','اضغط الرابط'];
if (!lead.message || !String(lead.message).trim() || JUNK.some(function(w){ return msg.includes(w); })) {
  return [{ json: { ...lead, score: 0, band: 'Junk', action: 'log_only', sellable: false } }];
}
if (!lead.phone || !lead.product || !lead.email) {
  return [{ json: { ...lead, score: 30, band: 'Incomplete', action: 'review_only', sellable: false } }];
}
const budget = Number(lead.budget_egp) || 0;
let s = 0;
if (budget >= 3000) s += 30; else if (budget > 0) s += 10;
if (lead.product) s += 20;
if (lead.phone && lead.email) s += 20;
let intent = 0;
if (['عاجل','أبدأ','موعد','تنفيذ'].some(function(k){ return msg.includes(k); })) intent = 20;
else if (['سعر','سعره','تفاصيل','عايز','عايزة'].some(function(k){ return msg.includes(k); })) intent = 10;
s += intent;
if (['form','whatsapp'].includes(lead.source)) s += 10;
let band = s >= 70 ? 'Hot' : s >= 40 ? 'Warm' : 'Junk';
if (band === 'Hot' && !(budget >= 3000 && intent >= 10)) band = 'Warm';
const action = band === 'Hot' ? 'create_hot' : band === 'Warm' ? 'create_warm' : 'log_only';
return [{ json: { ...lead, score: s, band: band, action: action, sellable: (band === 'Hot' || band === 'Warm') } }];
"""

VALIDATE_SEARCH_JS = """const paired = $input.first().pairedItem;
const lead = $('Score Lead').all()[paired ? paired.item : 0].json;
const res = $input.first().json;
if (res.error) {
  return [{ json: { ...lead, has_error: true, error_message: String((res.error && res.error.message) || res.error), match_count: 0, is_duplicate: false, match_id: null } }];
}
const status = res.statusCode;
const body = res.body || {};
if (status < 200 || status >= 300 || !Array.isArray(body.results)) {
  return [{ json: { ...lead, has_error: true, error_message: 'HubSpot search HTTP ' + status + ': ' + JSON.stringify(body).slice(0, 200), match_count: 0, is_duplicate: false, match_id: null } }];
}
const results = body.results;
const match = results[0] || null;
// HubSpot request bodies are pre-serialised here (real JS) because n8n 2.48.7's
// expression parser rejects NESTED object literals ({properties: {...}})
// with "invalid syntax" -- proven with n8n's own Expression class.
// The HTTP nodes reference them as ={{ $json.create_contact_body }} etc.
const contactBody = JSON.stringify({properties: {email: lead.email_normalized, phone: lead.phone_normalized, firstname: lead.firstname, lastname: lead.lastname}});
const dealBody = JSON.stringify({properties: {dealname: lead.name + ' - ' + lead.product, amount: lead.budget_egp, pipeline: '%s', dealstage: '%s', lead_score: lead.score, band: lead.band, reminder_sent: 'false', follow_up_due: 'false'}});
return [{ json: { ...lead, has_error: false, error_message: '', match_count: results.length, is_duplicate: results.length > 0, match_id: match ? String(match.id) : null, match_email: match ? ((match.properties || {}).email || null) : null, create_contact_body: contactBody, update_contact_body: contactBody, create_deal_body: dealBody } }];
""" % (PIPELINE_ID, STAGE_NEW_ID)

VALIDATE_CREATE_CONTACT_JS = """const paired = $input.first().pairedItem;
const base = $('Validate Search Result').all()[paired ? paired.item : 0].json;
const res = $input.first().json;
if (res.error) {
  return [{ json: { ...base, has_error: true, error_message: String((res.error && res.error.message) || res.error), stage_failed: 'create_contact' } }];
}
const status = res.statusCode;
const body = res.body || {};
if (status < 200 || status >= 300 || !body.id) {
  return [{ json: { ...base, has_error: true, error_message: 'HubSpot create contact HTTP ' + status + ': ' + JSON.stringify(body).slice(0, 200), stage_failed: 'create_contact' } }];
}
return [{ json: { ...base, has_error: false, error_message: '', contact_id: String(body.id), contact_created: true } }];
"""

VALIDATE_UPDATE_CONTACT_JS = """const paired = $input.first().pairedItem;
const base = $('Validate Search Result').all()[paired ? paired.item : 0].json;
const res = $input.first().json;
if (res.error) {
  return [{ json: { ...base, has_error: true, error_message: String((res.error && res.error.message) || res.error), stage_failed: 'update_contact' } }];
}
const status = res.statusCode;
const body = res.body || {};
if (status < 200 || status >= 300 || !body.id) {
  return [{ json: { ...base, has_error: true, error_message: 'HubSpot update contact HTTP ' + status + ': ' + JSON.stringify(body).slice(0, 200), stage_failed: 'update_contact' } }];
}
return [{ json: { ...base, has_error: false, error_message: '', contact_id: String(body.id), contact_updated: true, duplicate_of: base.match_id } }];
"""

VALIDATE_CREATE_DEAL_JS = """const paired = $input.first().pairedItem;
const base = $('Validate Create Contact').all()[paired ? paired.item : 0].json;
const res = $input.first().json;
if (res.error) {
  return [{ json: { ...base, has_error: true, error_message: String((res.error && res.error.message) || res.error), stage_failed: 'create_deal' } }];
}
const status = res.statusCode;
const body = res.body || {};
if (status < 200 || status >= 300 || !body.id) {
  return [{ json: { ...base, has_error: true, error_message: 'HubSpot create deal HTTP ' + status + ': ' + JSON.stringify(body).slice(0, 200), stage_failed: 'create_deal' } }];
}
return [{ json: { ...base, has_error: false, error_message: '', deal_id: String(body.id), deal_created: true } }];
"""

VALIDATE_ASSOCIATE_JS = """const paired = $input.first().pairedItem;
const base = $('Validate Create Deal').all()[paired ? paired.item : 0].json;
const res = $input.first().json;
if (res.error || res.statusCode < 200 || res.statusCode >= 300) {
  return [{ json: { ...base, association_ok: false, association_failed: true } }];
}
return [{ json: { ...base, association_ok: true, association_failed: false } }];
"""

PREPARE_DLQ_JS = """const j = $input.first().json;
return [{ json: {
  lead_id: j.lead_id || '',
  name: j.name || '',
  phone_normalized: j.phone_normalized || '',
  email_normalized: j.email_normalized || '',
  product: j.product || '',
  budget_egp: String(j.budget_egp || ''),
  band: j.band || '',
  score: String(j.score || ''),
  stage_failed: j.stage_failed || 'unknown',
  error_message: String(j.error_message || '').slice(0, 500),
  contact_id: j.contact_id || '',
  deal_id: j.deal_id || '',
  status: 'pending',
  retry_count: '0',
  created_at: new Date().toISOString()
}}];
"""

CHECK_DLQ_JS = """const paired = $input.first().pairedItem;
const row = $('Prepare DLQ Row').all()[paired ? paired.item : 0].json;
const res = $input.first().json;
const failed = !!res.error || (typeof res.statusCode === 'number' && (res.statusCode < 200 || res.statusCode >= 300));
return [{ json: { ...row, dlq_ok: !failed, dlq_error: failed ? String((res.error && res.error.message) || ('HTTP ' + res.statusCode)) : '' } }];
"""

PREPARE_AUDIT_JS = """const j = $input.first().json;
return [{ json: {
  lead_id: j.lead_id || '',
  action: j.audit_action || (j.is_duplicate ? 'merged_duplicate' : (j.deal_created ? 'created' : (j.contact_updated ? 'merged_duplicate' : 'logged'))),
  band: j.band || '',
  score: String(j.score || ''),
  duplicate_of: j.match_id || j.duplicate_of || '',
  contact_id: j.contact_id || '',
  deal_id: j.deal_id || '',
  association_failed: j.association_failed ? 'true' : 'false',
  telegram_failed: j.telegram_failed ? 'true' : 'false',
  // Kept for the sales-alert text (ALERT_SALES_JS reads them from this row);
  // the real Sheets append only maps the AUDIT_COLUMNS above.
  name: j.name || '',
  product: j.product || '',
  budget_egp: String(j.budget_egp || ''),
  created_at: new Date().toISOString()
}}];
"""

ALERT_SALES_JS = """const paired = $input.first().pairedItem;
const j = $('Prepare Audit Row').all()[paired ? paired.item : 0].json;
return [{ json: { ...j, alert_text: 'New ' + j.band + ' lead: ' + j.name + ' — ' + j.product + ' — ' + j.budget_egp + ' EGP (score ' + j.score + ')' } }];
"""

ALERT_FAILURE_JS = """const paired = $input.first().pairedItem;
const j = $('Check DLQ Result').all()[paired ? paired.item : 0].json;
const where = j.dlq_ok ? 'saved in Dead-Letter Queue' : 'NOT saved in Sheets - recover from n8n execution data';
return [{ json: { ...j, alert_text: 'HubSpot failed (' + (j.stage_failed || 'unknown') + '). Lead ' + where + '. ' + j.name + ' — ' + j.phone_normalized + ' — ' + j.email_normalized + ' — ' + j.product } }];
"""

ALERT_JUNK_JS = """const j = $input.first().json;
return [{ json: { ...j, audit_action: 'logged_' + j.band.toLowerCase() } }];
"""

# ---------------------------------------------------------------- HTTP bodies
SEARCH_CONTACT_BODY = """={{ JSON.stringify({filterGroups: [{filters: [{propertyName: "phone", operator: "EQ", value: $json.phone_normalized}]}, {filters: [{propertyName: "email", operator: "EQ", value: $json.email_normalized}]}], properties: ["email", "phone", "firstname", "lastname"], limit: 10}) }}"""

# HubSpot bodies are pre-serialised by the upstream Code node (see
# VALIDATE_SEARCH_JS / RECOVERY_FILTER_JS). n8n 2.48.7's expression parser
# rejects nested object literals ({properties: {...}}) with "invalid syntax",
# so the HTTP nodes reference the serialised string, not a JSON.stringify
# expression. The search body is safe: it nests objects inside ARRAYS only.
CREATE_CONTACT_BODY = """={{ $json.create_contact_body }}"""
UPDATE_CONTACT_BODY = """={{ $json.update_contact_body }}"""
CREATE_DEAL_BODY = """={{ $json.create_deal_body }}"""

DLQ_COLUMNS = ["lead_id", "name", "phone_normalized", "email_normalized", "product",
               "budget_egp", "band", "score", "stage_failed", "error_message",
               "contact_id", "deal_id", "status", "retry_count", "created_at"]
AUDIT_COLUMNS = ["lead_id", "action", "band", "score", "duplicate_of", "contact_id",
                 "deal_id", "association_failed", "telegram_failed", "created_at"]

def columns_map(cols):
    return {c: "={{ $json." + c + " }}" for c in cols}

def build_main(base, mock_mode):
    """mock_mode: True -> Sheets/Telegram via HTTP to mock; False -> native nodes."""
    nodes = []
    # 1 Form
    form_fields = [
        {"fieldLabel": "name", "fieldType": "text", "requiredField": True, "placeholder": "e.g. Ahmed Mohamed"},
        {"fieldLabel": "phone", "fieldType": "text", "requiredField": False, "placeholder": "e.g. 01012345678"},
        {"fieldLabel": "email", "fieldType": "email", "requiredField": False, "placeholder": "name@email.com"},
        {"fieldLabel": "product", "fieldType": "text", "requiredField": False, "placeholder": "e.g. CRM setup"},
        {"fieldLabel": "budget_egp", "fieldType": "number", "requiredField": False, "placeholder": "e.g. 5000"},
        {"fieldLabel": "message", "fieldType": "textarea", "requiredField": False, "placeholder": "What do you need?"},
    ]
    nodes.append(node("form-intake", "Lead Intake Form", "n8n-nodes-base.formTrigger",
        {"formTitle": "Lead Intake", "formDescription": "Synthetic demo lead form",
         "formFields": {"values": form_fields}, "responseMode": "onReceived",
         "options": {"path": "lead-intake", "buttonLabel": "Send", "appendAttribution": False}},
        [0, 0], version=2.2))
    nodes.append(code_node("normalize", "Normalize Lead", NORMALIZE_JS, [200, 0]))
    nodes.append(code_node("score", "Score Lead", SCORE_JS, [400, 0]))
    nodes.append(if_node("is-sellable", "Is Sellable?", "={{ $json.sellable }}", [600, 0]))
    # Junk / Incomplete branch: log only
    nodes.append(code_node("log-junk", "Mark Junk/Incomplete for Audit", ALERT_JUNK_JS, [800, -160]))
    # Dedup search
    nodes.append(http_node("dedup", "Search Contact by Phone/Email", "POST",
                           "/crm/v3/objects/contacts/search", SEARCH_CONTACT_BODY, [800, 0], base))
    nodes.append(code_node("validate-search", "Validate Search Result", VALIDATE_SEARCH_JS, [1000, 0]))
    nodes.append(if_node("search-error", "Search Failed?", "={{ $json.has_error }}", [1200, -160]))
    nodes.append(if_node("is-dup", "Is Duplicate?", "={{ $json.is_duplicate }}", [1200, 0]))
    # Duplicate branch
    nodes.append(http_node("update-contact", "Update Contact (PATCH)", "PATCH",
                           "={{ '" + base + "/crm/v3/objects/contacts/' + $json.match_id }}", UPDATE_CONTACT_BODY, [1400, -160], base))
    nodes.append(code_node("validate-update", "Validate Update Contact", VALIDATE_UPDATE_CONTACT_JS, [1600, -160]))
    nodes.append(if_node("update-error", "Update Failed?", "={{ $json.has_error }}", [1800, -160]))
    # New contact branch
    nodes.append(http_node("create-contact", "Create Contact (POST)", "POST",
                           "/crm/v3/objects/contacts", CREATE_CONTACT_BODY, [1400, 0], base,
                           retry_on_fail=False))
    nodes.append(code_node("validate-create", "Validate Create Contact", VALIDATE_CREATE_CONTACT_JS, [1600, 0]))
    nodes.append(if_node("create-error", "Create Failed?", "={{ $json.has_error }}", [1800, 0]))
    nodes.append(http_node("create-deal", "Create Deal (POST)", "POST",
                           "/crm/v3/objects/deals", CREATE_DEAL_BODY, [2000, 0], base,
                           retry_on_fail=False))
    nodes.append(code_node("validate-deal", "Validate Create Deal", VALIDATE_CREATE_DEAL_JS, [2200, 0]))
    nodes.append(if_node("deal-error", "Deal Failed?", "={{ $json.has_error }}", [2400, 0]))
    nodes.append(http_node("associate", "Associate Deal to Contact (PUT)", "PUT",
                           "={{ '" + base + "/crm/v3/objects/deals/' + $json.deal_id + '/associations/contacts/' + $json.contact_id + '/deal_to_contact' }}",
                           "{}", [2600, 0], base))
    nodes.append(code_node("validate-assoc", "Validate Association", VALIDATE_ASSOCIATE_JS, [2800, 0]))
    # Audit + sales alert (create path only). Merge and Junk paths have their own
    # terminal audit nodes and NEVER reach a sales alert (requirement 3).
    nodes.append(code_node("prep-audit", "Prepare Audit Row", PREPARE_AUDIT_JS, [3000, 0]))
    nodes.append(code_node("prep-merge-audit", "Prepare Merge Audit Row", PREPARE_AUDIT_JS, [2000, -160]))
    nodes.append(code_node("prep-junk-audit", "Prepare Junk Audit Row", PREPARE_AUDIT_JS, [1000, -160]))
    nodes.append(code_node("prep-alert", "Prepare Sales Alert", ALERT_SALES_JS, [3400, 0]))
    # DLQ path
    nodes.append(code_node("prep-dlq", "Prepare DLQ Row", PREPARE_DLQ_JS, [2000, -320]))
    nodes.append(code_node("check-dlq", "Check DLQ Result", CHECK_DLQ_JS, [2600, -320]))
    nodes.append(code_node("prep-fail-alert", "Prepare Failure Alert", ALERT_FAILURE_JS, [2800, -320]))

    if mock_mode:
        nodes.append(http_node("dlq", "DLQ Append (Mock Sheets)", "POST", "/sheets/append",
                               "={{ JSON.stringify({sheet: 'Dead_Letter_Queue', row: $json}) }}", [2200, -320], base, retry_on_fail=False))
        nodes.append(http_node("audit", "Audit Log Append (Mock Sheets)", "POST", "/sheets/append",
                               "={{ JSON.stringify({sheet: 'Audit_Log', row: $json}) }}", [3200, 0], base, retry_on_fail=False))
        nodes.append(http_node("audit-merge", "Audit Log Merge (Mock Sheets)", "POST", "/sheets/append",
                               "={{ JSON.stringify({sheet: 'Audit_Log', row: $json}) }}", [2200, -160], base, retry_on_fail=False))
        nodes.append(http_node("audit-junk", "Audit Log Junk (Mock Sheets)", "POST", "/sheets/append",
                               "={{ JSON.stringify({sheet: 'Audit_Log', row: $json}) }}", [1200, -160], base, retry_on_fail=False))
        nodes.append(http_node("alert-sales", "Alert Sales (Mock Telegram)", "POST", "/telegram/send",
                               "={{ JSON.stringify({chat_id: 'FILL', text: $json.alert_text}) }}", [3600, 0], base, retry_on_fail=False))
        nodes.append(http_node("alert-failure", "Alert Failure (Mock Telegram)", "POST", "/telegram/send",
                               "={{ JSON.stringify({chat_id: 'FILL', text: $json.alert_text}) }}", [3000, -320], base, retry_on_fail=False))
    else:
        nodes.append(sheets_append("dlq", "Dead-Letter Queue (Sheets)", "Dead_Letter_Queue",
                                   columns_map(DLQ_COLUMNS), [sheets_col(c) for c in DLQ_COLUMNS], [2200, -320]))
        nodes.append(sheets_append("audit", "Audit Log (Sheets)", "Audit_Log",
                                   columns_map(AUDIT_COLUMNS), [sheets_col(c) for c in AUDIT_COLUMNS], [3200, 0]))
        nodes.append(sheets_append("audit-merge", "Audit Log — Merge (Sheets)", "Audit_Log",
                                   columns_map(AUDIT_COLUMNS), [sheets_col(c) for c in AUDIT_COLUMNS], [2200, -160]))
        nodes.append(sheets_append("audit-junk", "Audit Log — Junk (Sheets)", "Audit_Log",
                                   columns_map(AUDIT_COLUMNS), [sheets_col(c) for c in AUDIT_COLUMNS], [1200, -160]))
        nodes.append(telegram_node("alert-sales", "Alert Sales (Telegram)", [3600, 0]))
        nodes.append(telegram_node("alert-failure", "Alert Failure (Telegram)", [3000, -320]))

    conns = {
        "Lead Intake Form": {"main": [[{"node": "Normalize Lead", "type": "main", "index": 0}]]},
        "Normalize Lead": {"main": [[{"node": "Score Lead", "type": "main", "index": 0}]]},
        "Score Lead": {"main": [[{"node": "Is Sellable?", "type": "main", "index": 0}]]},
        # Is Sellable? true -> Dedup, false -> Junk log (terminal, no sales alert)
        "Is Sellable?": {"main": [[{"node": "Search Contact by Phone/Email", "type": "main", "index": 0}],
                                   [{"node": "Mark Junk/Incomplete for Audit", "type": "main", "index": 0}]]},
        "Mark Junk/Incomplete for Audit": {"main": [[{"node": "Prepare Junk Audit Row", "type": "main", "index": 0}]]},
        "Prepare Junk Audit Row": {"main": [[{"node": "Audit Log Junk (Mock Sheets)" if mock_mode else "Audit Log — Junk (Sheets)", "type": "main", "index": 0}]]},
        "Search Contact by Phone/Email": {"main": [[{"node": "Validate Search Result", "type": "main", "index": 0}]]},
        "Validate Search Result": {"main": [[{"node": "Search Failed?", "type": "main", "index": 0}]]},
        # Search Failed? true -> DLQ, false -> Is Duplicate?
        "Search Failed?": {"main": [[{"node": "Prepare DLQ Row", "type": "main", "index": 0}],
                                     [{"node": "Is Duplicate?", "type": "main", "index": 0}]]},
        # Is Duplicate? true -> Update, false -> Create
        "Is Duplicate?": {"main": [[{"node": "Update Contact (PATCH)", "type": "main", "index": 0}],
                                   [{"node": "Create Contact (POST)", "type": "main", "index": 0}]]},
        "Update Contact (PATCH)": {"main": [[{"node": "Validate Update Contact", "type": "main", "index": 0}]]},
        "Validate Update Contact": {"main": [[{"node": "Update Failed?", "type": "main", "index": 0}]]},
        # Update Failed? true -> DLQ, false -> Merge Audit (terminal, no deal, no sales alert)
        "Update Failed?": {"main": [[{"node": "Prepare DLQ Row", "type": "main", "index": 0}],
                                     [{"node": "Prepare Merge Audit Row", "type": "main", "index": 0}]]},
        "Prepare Merge Audit Row": {"main": [[{"node": "Audit Log Merge (Mock Sheets)" if mock_mode else "Audit Log — Merge (Sheets)", "type": "main", "index": 0}]]},
        "Create Contact (POST)": {"main": [[{"node": "Validate Create Contact", "type": "main", "index": 0}]]},
        "Validate Create Contact": {"main": [[{"node": "Create Failed?", "type": "main", "index": 0}]]},
        # Create Failed? true -> DLQ, false -> Create Deal
        "Create Failed?": {"main": [[{"node": "Prepare DLQ Row", "type": "main", "index": 0}],
                                     [{"node": "Create Deal (POST)", "type": "main", "index": 0}]]},
        "Create Deal (POST)": {"main": [[{"node": "Validate Create Deal", "type": "main", "index": 0}]]},
        "Validate Create Deal": {"main": [[{"node": "Deal Failed?", "type": "main", "index": 0}]]},
        # Deal Failed? true -> DLQ (contact_id preserved in row), false -> Associate
        "Deal Failed?": {"main": [[{"node": "Prepare DLQ Row", "type": "main", "index": 0}],
                                  [{"node": "Associate Deal to Contact (PUT)", "type": "main", "index": 0}]]},
        "Associate Deal to Contact (PUT)": {"main": [[{"node": "Validate Association", "type": "main", "index": 0}]]},
        "Validate Association": {"main": [[{"node": "Prepare Audit Row", "type": "main", "index": 0}]]},
        "Prepare Audit Row": {"main": [[{"node": "Audit Log Append (Mock Sheets)" if mock_mode else "Audit Log (Sheets)", "type": "main", "index": 0}]]},
        "Audit Log Append (Mock Sheets)" if mock_mode else "Audit Log (Sheets)": {"main": [[{"node": "Prepare Sales Alert", "type": "main", "index": 0}]]},
        "Prepare Sales Alert": {"main": [[{"node": "Alert Sales (Mock Telegram)" if mock_mode else "Alert Sales (Telegram)", "type": "main", "index": 0}]]},
        "Prepare DLQ Row": {"main": [[{"node": "DLQ Append (Mock Sheets)" if mock_mode else "Dead-Letter Queue (Sheets)", "type": "main", "index": 0}]]},
        "DLQ Append (Mock Sheets)" if mock_mode else "Dead-Letter Queue (Sheets)": {"main": [[{"node": "Check DLQ Result", "type": "main", "index": 0}]]},
        "Check DLQ Result": {"main": [[{"node": "Prepare Failure Alert", "type": "main", "index": 0}]]},
        "Prepare Failure Alert": {"main": [[{"node": "Alert Failure (Mock Telegram)" if mock_mode else "Alert Failure (Telegram)", "type": "main", "index": 0}]]},
    }
    wf = {"name": "Lead Automation — Sample 1 (Synthetic Demo)",
          "nodes": nodes, "connections": conns, "active": False,
          "settings": {"executionOrder": "v1", "saveFailedExecutions": True,
                       "saveSuccessfulExecutions": True},
          "pinData": {},
          "meta": {"n8n_target_version": "2.48.7",
                   "note": "HubSpot via REST API (see workflow-spec.md for why the HubSpot node was not used). JSON structure checked in local n8n 2.48.7; live HubSpot/Sheets/Telegram calls need user credentials."}}
    return wf

# ---------------------------------------------------------------- SLA / Error / Recovery builders (v3)
from builders_v3_extra import (
    SLA_SEARCH_BODY, SLA_VALIDATE_SEARCH_JS, SLA_DUE_JS, SLA_MARK_BODY,
    SLA_VALIDATE_MARK_JS, SLA_PREPARE_ALERT_JS, SLA_CHECK_TELEGRAM_JS,
    SLA_AUDIT_COLUMNS, SLA_PREPARE_AUDIT_JS,
    ERROR_EXTRACT_JS, ERROR_ALERT_JS,
    RECOVERY_FILTER_JS, RECOVERY_VALIDATE_SEARCH_JS, RECOVERY_VALIDATE_CONTACT_JS,
    RECOVERY_VALIDATE_DEAL_JS, RECOVERY_VALIDATE_ASSOC_JS, RECOVERY_MARK_JS,
)


def build_sla(base, mock_mode):
    nodes = [
        node("schedule", "SLA Check — Every Hour", "n8n-nodes-base.scheduleTrigger",
             {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}}, [0, 0], version=1.2),
        http_node("search-deals", "Search New Deals", "POST",
                  "/crm/v3/objects/deals/search", SLA_SEARCH_BODY, [200, 0], base),
        code_node("validate-search", "Validate Deal Search", SLA_VALIDATE_SEARCH_JS, [400, 0]),
        if_node("search-error", "Search Failed?", "={{ $json.has_error }}", [600, -160]),
        code_node("sla-due", "SLA Due?", SLA_DUE_JS, [600, 0]),
        http_node("mark", "Mark reminder_sent (PATCH)", "PATCH",
                  "={{ '" + base + "/crm/v3/objects/deals/' + $json.deal_id }}", SLA_MARK_BODY, [800, 0], base),
        code_node("validate-mark", "Validate Mark", SLA_VALIDATE_MARK_JS, [1000, 0]),
        if_node("mark-error", "Mark Failed?", "={{ $json.mark_ok === false }}", [1200, 0]),
        code_node("prep-alert", "Prepare SLA Alert", SLA_PREPARE_ALERT_JS, [1400, 0]),
        code_node("check-tg", "Check Telegram Result", SLA_CHECK_TELEGRAM_JS, [1800, 0]),
        code_node("prep-audit", "Prepare SLA Audit Row", SLA_PREPARE_AUDIT_JS, [2000, 0]),
    ]
    if mock_mode:
        nodes.append(http_node("tg", "SLA Reminder (Mock Telegram)", "POST", "/telegram/send",
                               "={{ JSON.stringify({chat_id: 'FILL', text: $json.alert_text}) }}", [1600, 0], base, retry_on_fail=False))
        nodes.append(http_node("audit", "SLA Audit Log (Mock Sheets)", "POST", "/sheets/append",
                               "={{ JSON.stringify({sheet: 'Audit_Log', row: $json}) }}", [2200, 0], base, retry_on_fail=False))
    else:
        nodes.append(telegram_node("tg", "SLA Reminder (Telegram)", [1600, 0]))
        nodes.append(sheets_append("audit", "SLA Audit Log (Sheets)", "Audit_Log",
                                   columns_map(SLA_AUDIT_COLUMNS), [sheets_col(c) for c in SLA_AUDIT_COLUMNS], [2200, 0]))
    tg_name = "SLA Reminder (Mock Telegram)" if mock_mode else "SLA Reminder (Telegram)"
    audit_name = "SLA Audit Log (Mock Sheets)" if mock_mode else "SLA Audit Log (Sheets)"
    conns = {
        "SLA Check — Every Hour": {"main": [[{"node": "Search New Deals", "type": "main", "index": 0}]]},
        "Search New Deals": {"main": [[{"node": "Validate Deal Search", "type": "main", "index": 0}]]},
        "Validate Deal Search": {"main": [[{"node": "Search Failed?", "type": "main", "index": 0}]]},
        # Search Failed? true -> audit (search_failed) and stop; false -> SLA Due?
        "Search Failed?": {"main": [[{"node": "Prepare SLA Audit Row", "type": "main", "index": 0}],
                                     [{"node": "SLA Due?", "type": "main", "index": 0}]]},
        "SLA Due?": {"main": [[{"node": "Mark reminder_sent (PATCH)", "type": "main", "index": 0}]]},
        "Mark reminder_sent (PATCH)": {"main": [[{"node": "Validate Mark", "type": "main", "index": 0}]]},
        "Validate Mark": {"main": [[{"node": "Mark Failed?", "type": "main", "index": 0}]]},
        # Mark Failed? true -> audit (mark_failed), no Telegram; false -> Telegram.
        # The shared Prepare SLA Audit Row handles all three cases (search_failed /
        # mark_failed / reminder_sent) from the flags on its input item.
        "Mark Failed?": {"main": [[{"node": "Prepare SLA Audit Row", "type": "main", "index": 0}],
                                   [{"node": "Prepare SLA Alert", "type": "main", "index": 0}]]},
        "Prepare SLA Alert": {"main": [[{"node": tg_name, "type": "main", "index": 0}]]},
        tg_name: {"main": [[{"node": "Check Telegram Result", "type": "main", "index": 0}]]},
        "Check Telegram Result": {"main": [[{"node": "Prepare SLA Audit Row", "type": "main", "index": 0}]]},
        "Prepare SLA Audit Row": {"main": [[{"node": audit_name, "type": "main", "index": 0}]]},
    }
    return {"name": "Lead Automation — SLA Follow-up (Sample 1)",
            "nodes": nodes, "connections": conns, "active": False,
            "settings": {"executionOrder": "v1", "saveFailedExecutions": True},
            "pinData": {},
            "meta": {"n8n_target_version": "2.48.7",
                     "note": "reminder_sent is marked in HubSpot BEFORE Telegram is sent, so a crash cannot produce a duplicate reminder. If Telegram then fails, the failure is recorded in the Audit Log and the reminder is not re-sent. Reply state = dealstage still equals the New stage id; moving the deal out of New stops reminders. Live HubSpot/Sheets/Telegram calls need user credentials."}}


def build_error(base, mock_mode):
    nodes = [
        node("error-trigger", "Error Trigger", "n8n-nodes-base.errorTrigger", {}, [0, 0], version=1),
        code_node("extract", "Extract Error Info", ERROR_EXTRACT_JS, [200, 0]),
        code_node("prep-alert", "Prepare Error Alert", ERROR_ALERT_JS, [600, 0]),
    ]
    if mock_mode:
        nodes.append(http_node("err-dlq", "Error DLQ (Mock Sheets)", "POST", "/sheets/append",
                               "={{ JSON.stringify({sheet: 'Dead_Letter_Queue', row: $json}) }}", [400, 0], base, retry_on_fail=False))
        nodes.append(http_node("err-alert", "Error Alert (Mock Telegram)", "POST", "/telegram/send",
                               "={{ JSON.stringify({chat_id: 'FILL', text: $json.alert_text}) }}", [800, 0], base, retry_on_fail=False))
        dlq_name = "Error DLQ (Mock Sheets)"
        alert_name = "Error Alert (Mock Telegram)"
    else:
        nodes.append(sheets_append("err-dlq", "Error DLQ (Sheets)", "Dead_Letter_Queue",
                                   columns_map(DLQ_COLUMNS), [sheets_col(c) for c in DLQ_COLUMNS], [400, 0]))
        nodes.append(telegram_node("err-alert", "Error Alert (Telegram)", [800, 0]))
        dlq_name = "Error DLQ (Sheets)"
        alert_name = "Error Alert (Telegram)"
    conns = {
        "Error Trigger": {"main": [[{"node": "Extract Error Info", "type": "main", "index": 0}]]},
        "Extract Error Info": {"main": [[{"node": dlq_name, "type": "main", "index": 0}]]},
        dlq_name: {"main": [[{"node": "Prepare Error Alert", "type": "main", "index": 0}]]},
        "Prepare Error Alert": {"main": [[{"node": alert_name, "type": "main", "index": 0}]]},
    }
    return {"name": "Lead Automation — Error Handler (Sample 1)",
            "nodes": nodes, "connections": conns, "active": False,
            "settings": {"executionOrder": "v1"}, "pinData": {},
            "meta": {"n8n_target_version": "2.48.7",
                     "note": "Honest limitation: the Error Trigger output does NOT contain the original lead payload. This workflow records the execution id / workflow / error and alerts. The lead itself is recovered from the saved failed execution in n8n (Save Failed Executions is enabled on the main workflow). Set this workflow as the Error Workflow in the main workflow settings after import."}}


def build_recovery(base, mock_mode):
    nodes = [
        node("manual", "Manual Trigger", "n8n-nodes-base.manualTrigger", {}, [0, 0], version=1),
        code_node("filter", "Filter Pending Rows", RECOVERY_FILTER_JS, [400, 0]),
        if_node("has-contact", "Has Contact Already?", "={{ $json.has_contact }}", [600, -160]),
        http_node("search", "Search Contact by Phone/Email", "POST",
                  "/crm/v3/objects/contacts/search", SEARCH_CONTACT_BODY, [600, 0], base),
        merge_node("merge-search", "Merge Search Result", [800, 0]),
        code_node("validate-search", "Validate Recovery Search", RECOVERY_VALIDATE_SEARCH_JS, [1000, 0]),
        if_node("search-error", "Search Failed?", "={{ $json.has_error }}", [1200, -160]),
        if_node("is-dup", "Is Duplicate?", "={{ $json.is_duplicate }}", [1200, 0]),
        http_node("update-contact", "Update Contact (PATCH)", "PATCH",
                  "={{ '" + base + "/crm/v3/objects/contacts/' + $json.match_id }}", UPDATE_CONTACT_BODY, [1200, -160], base),
        http_node("create-contact", "Create Contact (POST)", "POST",
                  "/crm/v3/objects/contacts", CREATE_CONTACT_BODY, [1200, 0], base,
                  retry_on_fail=False),
        merge_node("merge-update", "Merge Update Result", [1600, -160]),
        merge_node("merge-create", "Merge Create Result", [1600, 0]),
        code_node("validate-contact", "Validate Recovery Contact", RECOVERY_VALIDATE_CONTACT_JS, [1800, 0]),
        if_node("contact-error", "Contact Failed?", "={{ $json.has_error }}", [2000, 0]),
        # Pass-through: the single node feeding Create Deal from BOTH paths
        # (Has Contact Already? true, and Contact Failed? false). The deal
        # validator looks this node up by pairedItem index -- a lookup against
        # a node with two feeders would be ambiguous, so both paths merge here.
        code_node("prep-deal", "Prepare Deal",
                  "return $input.all().map(function(i){ return {json: i.json}; });", [1700, 0]),
        http_node("create-deal", "Create Deal (POST)", "POST",
                  "/crm/v3/objects/deals", CREATE_DEAL_BODY, [2400, 0], base,
                  retry_on_fail=False),
        merge_node("merge-deal", "Merge Deal Result", [2600, 0]),
        code_node("validate-deal", "Validate Recovery Deal", RECOVERY_VALIDATE_DEAL_JS, [2800, 0]),
        if_node("deal-error", "Deal Failed?", "={{ $json.has_error }}", [3000, 0]),
        # Association (round 4): a recovered Deal must be linked to its Contact,
        # exactly like the main workflow does. PUT is idempotent, so node-level
        # retry (3x) is safe here. A failed association is NON-FATAL: the Deal
        # already exists, so the row is still marked recovered with the failure
        # noted in error_message -- retrying the row would create a second Deal.
        http_node("associate", "Associate Deal to Contact (PUT)", "PUT",
                  "={{ '" + base + "/crm/v3/objects/deals/' + $json.deal_id + '/associations/contacts/' + $json.contact_id + '/deal_to_contact' }}",
                  "{}", [3200, 0], base),
        merge_node("merge-assoc", "Merge Association Result", [3400, 0]),
        code_node("validate-assoc", "Validate Recovery Association", RECOVERY_VALIDATE_ASSOC_JS, [3600, 0]),
        code_node("mark", "Mark Row Recovered / Retry", RECOVERY_MARK_JS, [3800, 0]),
    ]
    if mock_mode:
        nodes.insert(1, http_node("read-dlq", "Read DLQ Rows (Mock Sheets)", "POST", "/sheets/read",
                                  "={{ JSON.stringify({sheet: 'Dead_Letter_Queue'}) }}", [200, 0], base,
                                  full_response=False))
        nodes.append(http_node("mark-row", "Update DLQ Row (Mock Sheets)", "POST", "/sheets/appendOrUpdate",
                               "={{ JSON.stringify({sheet: 'Dead_Letter_Queue', row: $json}) }}", [4000, 0], base))
        read_name = "Read DLQ Rows (Mock Sheets)"
        mark_name = "Update DLQ Row (Mock Sheets)"
    else:
        nodes.insert(1, node("read-dlq", "Read DLQ Rows", "n8n-nodes-base.googleSheets",
             {"resource": "sheet", "operation": "read",
              "documentId": {"__rl": True, "mode": "url", "value": SHEETS_URL},
              "sheetName": {"__rl": True, "mode": "list", "value": "Dead_Letter_Queue", "cachedResultName": "Dead_Letter_Queue"},
              "options": {}},
             [200, 0], creds=SHEETS_CREDS, version=4.7,
             extra={"continueOnFail": True}))
        nodes.append(node("mark-row", "Update DLQ Row (Sheets)", "n8n-nodes-base.googleSheets",
             {"resource": "sheet", "operation": "appendOrUpdate",
              "documentId": {"__rl": True, "mode": "url", "value": SHEETS_URL},
              "sheetName": {"__rl": True, "mode": "list", "value": "Dead_Letter_Queue", "cachedResultName": "Dead_Letter_Queue"},
              "columns": {"mappingMode": "defineBelow",
                          "value": {**columns_map(DLQ_COLUMNS), "recovered_at": "={{ $json.recovered_at }}"},
                          "matchingColumns": ["lead_id"],
                          "schema": [sheets_col(c) for c in DLQ_COLUMNS] + [sheets_col("recovered_at")]},
              "options": {}},
             [4000, 0], creds=SHEETS_CREDS, version=4.7,
             extra={"continueOnFail": True}))
        read_name = "Read DLQ Rows"
        mark_name = "Update DLQ Row (Sheets)"
    conns = {
        "Manual Trigger": {"main": [[{"node": read_name, "type": "main", "index": 0}]]},
        read_name: {"main": [[{"node": "Filter Pending Rows", "type": "main", "index": 0}]]},
        "Filter Pending Rows": {"main": [[{"node": "Has Contact Already?", "type": "main", "index": 0}]]},
        # Has Contact Already? true -> Prepare Deal; false -> Search AND Merge Search (input 1)
        "Has Contact Already?": {"main": [[{"node": "Prepare Deal", "type": "main", "index": 0}],
                                          [{"node": "Search Contact by Phone/Email", "type": "main", "index": 0},
                                           {"node": "Merge Search Result", "type": "main", "index": 1}]]},
        "Search Contact by Phone/Email": {"main": [[{"node": "Merge Search Result", "type": "main", "index": 0}]]},
        "Merge Search Result": {"main": [[{"node": "Validate Recovery Search", "type": "main", "index": 0}]]},
        "Validate Recovery Search": {"main": [[{"node": "Search Failed?", "type": "main", "index": 0}]]},
        # Search Failed? true -> Mark (retry_count+1, still pending); false -> Is Duplicate?
        "Search Failed?": {"main": [[{"node": "Mark Row Recovered / Retry", "type": "main", "index": 0}],
                                     [{"node": "Is Duplicate?", "type": "main", "index": 0}]]},
        # Is Duplicate? true -> Update AND Merge Update (input 1); false -> Create AND Merge Create (input 1)
        "Is Duplicate?": {"main": [[{"node": "Update Contact (PATCH)", "type": "main", "index": 0},
                                    {"node": "Merge Update Result", "type": "main", "index": 1}],
                                   [{"node": "Create Contact (POST)", "type": "main", "index": 0},
                                    {"node": "Merge Create Result", "type": "main", "index": 1}]]},
        "Update Contact (PATCH)": {"main": [[{"node": "Merge Update Result", "type": "main", "index": 0}]]},
        "Create Contact (POST)": {"main": [[{"node": "Merge Create Result", "type": "main", "index": 0}]]},
        "Merge Update Result": {"main": [[{"node": "Validate Recovery Contact", "type": "main", "index": 0}]]},
        "Merge Create Result": {"main": [[{"node": "Validate Recovery Contact", "type": "main", "index": 0}]]},
        "Validate Recovery Contact": {"main": [[{"node": "Contact Failed?", "type": "main", "index": 0}]]},
        # Contact Failed? true -> Mark (retry); false -> Prepare Deal -> Create Deal
        "Contact Failed?": {"main": [[{"node": "Mark Row Recovered / Retry", "type": "main", "index": 0}],
                                     [{"node": "Prepare Deal", "type": "main", "index": 0}]]},
        # Prepare Deal -> Create Deal AND Merge Deal (input 1)
        "Prepare Deal": {"main": [[{"node": "Create Deal (POST)", "type": "main", "index": 0},
                                   {"node": "Merge Deal Result", "type": "main", "index": 1}]]},
        "Create Deal (POST)": {"main": [[{"node": "Merge Deal Result", "type": "main", "index": 0}]]},
        "Merge Deal Result": {"main": [[{"node": "Validate Recovery Deal", "type": "main", "index": 0}]]},
        "Validate Recovery Deal": {"main": [[{"node": "Deal Failed?", "type": "main", "index": 0}]]},
        # Deal Failed? true -> Mark (retry); false -> Associate the Deal to its
        # Contact (round 4) AND feed the Merge node (input 1) with the same row.
        "Deal Failed?": {"main": [[{"node": "Mark Row Recovered / Retry", "type": "main", "index": 0}],
                                  [{"node": "Associate Deal to Contact (PUT)", "type": "main", "index": 0},
                                   {"node": "Merge Association Result", "type": "main", "index": 1}]]},
        "Associate Deal to Contact (PUT)": {"main": [[{"node": "Merge Association Result", "type": "main", "index": 0}]]},
        "Merge Association Result": {"main": [[{"node": "Validate Recovery Association", "type": "main", "index": 0}]]},
        # Association is non-fatal (validator sets has_error=false even when the
        # PUT fails): a recovered Deal is still a recovered row. -> Mark.
        "Validate Recovery Association": {"main": [[{"node": "Mark Row Recovered / Retry", "type": "main", "index": 0}]]},
        "Mark Row Recovered / Retry": {"main": [[{"node": mark_name, "type": "main", "index": 0}]]},
    }
    return {"name": "Lead Automation — DLQ Recovery (Sample 1)",
            "nodes": nodes, "connections": conns, "active": False,
            "settings": {"executionOrder": "v1", "saveFailedExecutions": True},
            "pinData": {},
            "meta": {"n8n_target_version": "2.48.7",
                     "note": "Manual retry mechanism for the Dead-Letter Queue. Rows with status=pending and retry_count<3 are re-processed: dedup is re-checked first, so a retry can never create a duplicate Contact/Deal. A row that already has a contact_id skips contact creation and only creates the Deal (partial-success recovery) -- and associates it to the Contact (PUT, same as the main workflow). Rows that fail again stay pending with retry_count+1 (max 3 attempts). A failed association does NOT block recovery: the Deal exists, so the row is marked recovered with the failure noted in error_message. No automatic in-workflow retry loop: the main workflow retries transient HTTP failures 3x via node-level retryOnFail; business retry is this workflow, run manually or on a schedule."}}

if __name__ == "__main__":
    import json as _json
    jobs = [
        (False, HUBSPOT_REAL, "lead-automation-workflow.json", build_main),
        (True, MOCK, "lead-automation-workflow-MOCK.json", build_main),
        (False, HUBSPOT_REAL, "sla-workflow.json", build_sla),
        (True, MOCK, "sla-workflow-MOCK.json", build_sla),
        (False, HUBSPOT_REAL, "error-workflow.json", build_error),
        (True, MOCK, "error-workflow-MOCK.json", build_error),
        (False, HUBSPOT_REAL, "dlq-recovery-workflow.json", build_recovery),
        (True, MOCK, "dlq-recovery-workflow-MOCK.json", build_recovery),
    ]
    for mock_mode, base, fname, fn in jobs:
        wf = fn(base, mock_mode)
        (BASE / fname).write_text(_json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        print(fname, "nodes:", len(wf["nodes"]))
