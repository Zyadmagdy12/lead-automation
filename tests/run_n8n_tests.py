#!/usr/bin/env python3
"""Execute Sample 1 v3 MOCK workflows in real n8n 2.48.7 and assert behaviour
against the mock HubSpot/Sheets/Telegram server (127.0.0.1:5999).

Prerequisites (local test rig only, no user credentials):
  - n8n 2.48.7 running on 127.0.0.1:5678 with owner test@example.local
    (cookie in /tmp/n8n-cookies.txt)
  - mock server running on 127.0.0.1:5999 (node mock-server.js)
  - test credential "HubSpot Service Key (Test)" id Iifcj7L5O1rNu5Ud

Scenarios:
  T0 Static uniformity checks (round 4, no n8n): stage/pipeline defined once
     in builders, one value in all workflow JSONs, SLA Due? has no stage
     re-check, setup lists all DLQ columns incl. recovered_at
  T1 Hot new lead -> contact + deal + associate + audit + sales telegram
  T2 Duplicate (same phone) -> PATCH update, no new contact/deal, no sales telegram
  T3 Junk lead -> no HubSpot call, audit only, no sales telegram
  T4 Incomplete (no product) -> no HubSpot call, audit only, no sales telegram
  T5 Contact-create failure -> DLQ row + failure telegram, no deal
  T6 Deal-create failure (partial success) -> DLQ row WITH contact_id, no deal
  T7 Search failure -> DLQ row, no contact created
  T8 Recovery of T6 row -> deal created, no new contact, row marked recovered
  T8b Recovery of T6 row -> recovered deal ASSOCIATED to the existing contact
  T9 Recovery of a fresh pending row -> contact + deal created, row recovered
  T9b Recovery of a fresh row -> recovered deal ASSOCIATED to the new contact
  T10 Recovery retry of T5 row (still failing) -> stays pending, retry_count 1
  T13 Association failure is non-fatal -> deal created, row recovered with
      recovered_at, failure noted in error_message, no duplicate on retry
  T11 SLA -> only due deals get reminder_sent=true + telegram; no duplicates on re-run
  T12 Telegram failure after mark -> audit records telegram_failed, no re-reminder

Run: python3 run_n8n_tests.py
Writes: n8n-test-results.json (in this folder)
"""
import json, time, urllib.request, urllib.parse, pathlib

N8N = "http://127.0.0.1:5678"
MOCK = "http://127.0.0.1:5999"
SAMPLE = pathlib.Path.home() / "workspace/projects/portfolio-samples/sample1-lead-automation"
CRED_ID = "Iifcj7L5O1rNu5Ud"
def _cookie():
    for line in pathlib.Path("/tmp/n8n-cookies.txt").read_text().splitlines():
        if "n8n-auth" in line:
            return "n8n-auth=" + line.split()[-1]
    raise RuntimeError("n8n-auth cookie not found")
COOKIE = _cookie()

results = []
def check(name, cond, detail=""):
    results.append({"name": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(detail) if detail else ""))

def n8n(method, path, body=None):
    req = urllib.request.Request(
        N8N + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Cookie": COOKIE})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:500]}

def mock(method, path, body=None):
    req = urllib.request.Request(
        MOCK + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"{}")

def import_workflow(fname, name_suffix=" (TEST)"):
    wf = json.loads((SAMPLE / fname).read_text())
    wf.pop("meta", None)
    # substitute the test credential id for every httpHeaderAuth reference
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "httpHeaderAuth" and isinstance(v, dict):
                    v["id"] = CRED_ID
                    v["name"] = "HubSpot Service Key (Test)"
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(wf)
    wf["name"] = wf["name"] + name_suffix
    r = n8n("POST", "/rest/workflows", wf)
    if "_http_error" in r:
        raise RuntimeError(f"import {fname} failed: {r}")
    return r["data"]["id"]

def activate(wid):
    v = n8n("GET", f"/rest/workflows/{wid}")["data"]["versionId"]
    r = n8n("POST", f"/rest/workflows/{wid}/activate", {"versionId": v})
    assert r.get("data", {}).get("active") is True, f"activate failed: {r}"
    return True

def run_manual(wid, trigger_name):
    r = n8n("POST", f"/rest/workflows/{wid}/run", {"triggerToStartFrom": {"name": trigger_name}})
    assert "data" in r and "executionId" in r["data"], f"manual run failed: {r}"
    eid = r["data"]["executionId"]
    # Wait for the execution to actually finish (a fixed sleep raced ahead:
    # a failing HTTP node retries 3x with 5s waits, so a run can take 20s+).
    for _ in range(90):
        e = n8n("GET", f"/rest/executions/{eid}")
        status = (e.get("data") or {}).get("status")
        if status in ("success", "error", "canceled", "crashed"):
            return eid
        time.sleep(1)
    raise RuntimeError(f"manual run {eid} did not finish in 90s")

FORM_ORDER = ["name", "phone", "email", "product", "budget_egp", "message"]

def submit_form(fields):
    # n8n Form Trigger v2.2 reads submissions by POSITION: field-0, field-1, ...
    # (proven from n8n 2.48.7 source: addFormResponseDataToReturnItem uses
    # `field-${index}`). The output key is the fieldLabel for version < 2.4.
    boundary = "----n8ntestboundary"
    parts = []
    for i, k in enumerate(FORM_ORDER):
        v = fields.get(k, "")
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"field-{i}\"\r\n\r\n{v}\r\n")
    parts.append(f"--{boundary}--\r\n")
    data = "".join(parts).encode()
    req = urllib.request.Request(N8N + "/form/lead-intake", data=data, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500]

def log():
    return mock("GET", "/log")

def calls(path_part, method="POST"):
    return [c for c in log()["calls"] if path_part in c["path"] and c["method"] == method]

def cleanup_test_workflows():
    r = n8n("GET", "/rest/workflows?limit=100")
    data = r.get("data", [])
    items = data.get("results", []) if isinstance(data, dict) else data
    for w in items:
        if "(TEST)" in w.get("name", "") and "Sample 1" in w.get("name", ""):
            n8n("POST", f"/rest/workflows/{w['id']}/deactivate", {"versionId": w.get("versionId", "")})
            n8n("DELETE", f"/rest/workflows/{w['id']}")

def main():
    cleanup_test_workflows()
    mock("POST", "/reset")
    # ---- T0: static uniformity checks (round 4) -- no n8n needed.
    import ast, re
    b_extra = (SAMPLE / "builders_v3_extra.py").read_text()
    b_main = (SAMPLE / "build_workflows_v3.py").read_text()
    stage_id = re.search(r'STAGE_NEW_ID = "([^"]+)"', b_extra).group(1)
    pipe_id = re.search(r'PIPELINE_ID = "([^"]+)"', b_extra).group(1)
    check("T0 stage id defined exactly once in builders (single source of truth)",
          (b_extra + b_main).count(stage_id) == 1 and f'STAGE_NEW_ID = "{stage_id}"' in b_extra)
    check("T0 pipeline id defined exactly once in builders",
          (b_extra + b_main).count(f'"{pipe_id}"') == 1)
    total_stage = total_pipe = 0
    for fname in ["lead-automation-workflow.json", "lead-automation-workflow-MOCK.json",
                  "sla-workflow.json", "sla-workflow-MOCK.json",
                  "error-workflow.json", "error-workflow-MOCK.json",
                  "dlq-recovery-workflow.json", "dlq-recovery-workflow-MOCK.json"]:
        t = (SAMPLE / fname).read_text()
        total_stage += t.count(stage_id)
        total_pipe += t.count(f"pipeline: '{pipe_id}'")
    check("T0 stage id uniform in all workflow JSONs (6 occurrences, one value)", total_stage == 6, total_stage)
    check("T0 pipeline id uniform in all workflow JSONs (4 occurrences, one value)", total_pipe == 4, total_pipe)
    due_js = re.search(r'SLA_DUE_JS = """(.*?)"""', b_extra, re.S).group(1)
    check("T0 SLA Due? contains no dealstage re-check (search filter is the single gate)", "p.dealstage" not in due_js)
    dlq_cols = None
    for node_ in ast.walk(ast.parse(b_main)):
        if isinstance(node_, ast.Assign) and getattr(node_.targets[0], 'id', '') == 'DLQ_COLUMNS':
            dlq_cols = ast.literal_eval(node_.value)
    setup = (SAMPLE / "setup-instructions.md").read_text()
    dlq_line = [ln for ln in setup.splitlines() if ln.strip().startswith("- `Dead_Letter_Queue`")][0]
    check("T0 setup lists all DLQ columns incl. recovered_at",
          dlq_cols is not None and all(c in dlq_line for c in dlq_cols + ["recovered_at"]), dlq_line)
    # ---- import + activate main MOCK workflow
    main_id = import_workflow("lead-automation-workflow-MOCK.json")
    check("import main MOCK workflow into n8n 2.48.7", main_id)
    activate(main_id)
    check("activate main MOCK workflow", True)
    time.sleep(2)

    # T1 Hot new lead
    st, _ = submit_form({"name": "Ahmed Ali", "phone": "01012345678", "email": "ahmed@example.com",
                         "product": "CRM setup", "budget_egp": "5000", "message": "عايز أبدأ بسرعة"})
    time.sleep(3)
    L = log()
    check("T1 form submit accepted", st == 200, f"http {st}")
    check("T1 one contact created", len(L["contacts"]) == 1, L["contacts"])
    check("T1 one deal created", len(L["deals"]) == 1, L["deals"])
    check("T1 deal associated to contact", len(L["deals"]) == 1 and len(L["contacts"]) == 1 and L["deals"][0]["properties"].get("associated_contact") == L["contacts"][0]["id"])
    check("T1 sales telegram sent", len([t for t in L["telegram"] if "Ahmed Ali" in t["text"]]) == 1, L["telegram"])
    check("T1 audit row written", len([r for r in L["sheets"]["Audit_Log"] if r.get("action") == "created"]) == 1)

    # T2 duplicate (same phone, new email)
    submit_form({"name": "Ahmed A.", "phone": "+201012345678", "email": "ahmed2@example.com",
                 "product": "CRM setup", "budget_egp": "6000", "message": "عايز أبدأ"})
    time.sleep(3)
    L = log()
    check("T2 no second contact (dedup by phone)", len(L["contacts"]) == 1)
    check("T2 no second deal for duplicate", len(L["deals"]) == 1)
    check("T2 contact updated with new email", len(L["contacts"]) == 1 and L["contacts"][0]["properties"].get("email") == "ahmed2@example.com", L["contacts"][0]["properties"] if L["contacts"] else "no contacts")
    check("T2 merge audit row", len([r for r in L["sheets"]["Audit_Log"] if r.get("action") == "merged_duplicate"]) == 1)
    check("T2 no sales telegram for duplicate", len(L["telegram"]) == 1, f"telegram count {len(L['telegram'])}")

    # T3 junk
    submit_form({"name": "Test User", "phone": "01099998888", "email": "t@example.com",
                 "product": "CRM setup", "budget_egp": "1000", "message": "test"})
    time.sleep(3)
    L = log()
    check("T3 junk: no new contact", len(L["contacts"]) == 1)
    check("T3 junk: no new deal", len(L["deals"]) == 1)
    check("T3 junk: audit logged_junk, no sales telegram", len([r for r in L["sheets"]["Audit_Log"] if r.get("action") == "logged_junk"]) == 1 and len(L["telegram"]) == 1)

    # T4 incomplete (no product)
    submit_form({"name": "Sara", "phone": "01122223333", "email": "sara@example.com",
                 "product": "", "budget_egp": "4000", "message": "عايز أبدأ"})
    time.sleep(3)
    L = log()
    check("T4 incomplete: no new contact", len(L["contacts"]) == 1)
    check("T4 incomplete: audit logged_incomplete", len([r for r in L["sheets"]["Audit_Log"] if r.get("action") == "logged_incomplete"]) == 1)

    # T5 contact-create failure (failure path retries 3x with 5s waits, ~10s+)
    submit_form({"name": "Fail Contact", "phone": "01055556666", "email": "fail-contact@example.com",
                 "product": "CRM setup", "budget_egp": "5000", "message": "عايز أبدأ"})
    time.sleep(16)
    L = log()
    check("T5 contact failure: no contact created", len(L["contacts"]) == 1)
    check("T5 contact failure: no deal created", len(L["deals"]) == 1)
    check("T5 contact failure: DLQ row pending", len([r for r in L["sheets"]["Dead_Letter_Queue"] if r.get("stage_failed") == "create_contact" and r.get("status") == "pending"]) == 1)
    check("T5 contact failure: failure telegram sent", len([t for t in L["telegram"] if "HubSpot failed" in t["text"]]) == 1)

    # T6 deal-create failure (partial success: contact created, deal not)
    submit_form({"name": "FAIL_DEAL Omar", "phone": "01077778888", "email": "omar@example.com",
                 "product": "CRM setup", "budget_egp": "8000", "message": "عايز أبدأ"})
    time.sleep(16)
    L = log()
    check("T6 deal failure: contact WAS created (partial success)", len(L["contacts"]) == 2)
    check("T6 deal failure: no deal created", len(L["deals"]) == 1)
    dlq6 = [r for r in L["sheets"]["Dead_Letter_Queue"] if r.get("stage_failed") == "create_deal"]
    check("T6 deal failure: DLQ row keeps contact_id", len(dlq6) == 1 and dlq6[0].get("contact_id") != "", dlq6)

    # T7 search failure (search retries 3x with 5s waits, ~10s+)
    submit_form({"name": "Search Fail", "phone": "+201000000000", "email": "sf@example.com",
                 "product": "CRM setup", "budget_egp": "5000", "message": "عايز أبدأ"})
    time.sleep(16)
    L = log()
    check("T7 search failure: no contact created", len(L["contacts"]) == 2)
    check("T7 search failure: DLQ row (unknown stage)", len([r for r in L["sheets"]["Dead_Letter_Queue"] if r.get("stage_failed") == "unknown"]) == 1)

    # ---- Recovery workflow
    rec_id = import_workflow("dlq-recovery-workflow-MOCK.json")
    check("import DLQ recovery MOCK workflow", rec_id)
    # T8: recover the T6 row (has contact_id) -> deal only, no new contact
    run_manual(rec_id, "Manual Trigger")
    L = log()
    check("T8 recovery: deal created for T6 row", len(L["deals"]) == 2, L["deals"])
    check("T8 recovery: no new contact created", len(L["contacts"]) == 2)
    dlq6 = [r for r in L["sheets"]["Dead_Letter_Queue"] if r.get("stage_failed") == "create_deal"]
    check("T8 recovery: T6 row marked recovered", len(dlq6) == 1 and dlq6[0].get("status") == "recovered" and dlq6[0].get("created_at") != "", dlq6)
    d8 = [d for d in L["deals"] if d["properties"].get("dealname", "").startswith("FAIL_DEAL Omar")]
    check("T8b recovery: recovered deal associated to existing contact", len(d8) == 1 and d8[0]["properties"].get("associated_contact") == dlq6[0].get("contact_id"), d8)
    # T9: fresh pending row (seeded, good email, no contact) -> contact + deal, recovered
    mock("POST", "/seed/dlq", {"rows": [{"lead_id": "lead-seed9", "name": "Mona H", "phone_normalized": "201122233344",
        "email_normalized": "mona@example.com", "product": "CRM setup", "budget_egp": "7000",
        "band": "Hot", "score": "90", "stage_failed": "create_contact", "error_message": "seeded",
        "contact_id": "", "deal_id": "", "status": "pending", "retry_count": "0", "created_at": "2026-11-22T00:00:00Z"}]})
    # T10: the T5 row (failing contact create) is retried in BOTH recovery runs,
    # so its retry_count grows by exactly 1 per run -- capture it before this run.
    r5_before = [r for r in log()["sheets"]["Dead_Letter_Queue"] if r.get("phone_normalized") == "201055556666"]
    assert len(r5_before) == 1, r5_before
    r5_before_n = int(r5_before[0].get("retry_count") or 0)
    run_manual(rec_id, "Manual Trigger")
    L = log()
    check("T9 recovery: contact created for seeded row", len(L["contacts"]) == 3)
    check("T9 recovery: deal created for seeded row", len(L["deals"]) == 3)
    r9 = [r for r in L["sheets"]["Dead_Letter_Queue"] if r.get("lead_id") == "lead-seed9"]
    check("T9 recovery: seeded row marked recovered", len(r9) == 1 and r9[0].get("status") == "recovered" and r9[0].get("created_at") == "2026-11-22T00:00:00Z", r9)
    d9 = [d for d in L["deals"] if d["properties"].get("dealname", "").startswith("Mona H")]
    c9 = [c for c in L["contacts"] if c["properties"].get("email") == "mona@example.com"]
    check("T9b recovery: recovered deal associated to newly created contact", len(d9) == 1 and len(c9) == 1 and d9[0]["properties"].get("associated_contact") == c9[0]["id"], (d9, c9))
    # T10: T5 row still fails -> stays pending, retry_count +1 (exactly one
    # increment for this run: no reset, no recovery, no double-counting)
    r5 = [r for r in L["sheets"]["Dead_Letter_Queue"] if r.get("phone_normalized") == "201055556666"]
    check("T10 recovery: failing row stays pending, retry_count +1", len(r5) == 1 and r5[0].get("status") == "pending" and r5[0].get("retry_count") == str(r5_before_n + 1), r5)
    check("T10 recovery: no duplicate contact from retry", len(L["contacts"]) == 3)

    # T13: association failure is non-fatal -- seed a row whose contact_id
    # does not exist in HubSpot. Recovery creates the Deal, the PUT 404s,
    # the row is still marked recovered (retrying it would create a 2nd Deal).
    mock("POST", "/seed/dlq", {"rows": [{"lead_id": "lead-seed13", "name": "Assoc Fail Sara", "phone_normalized": "201133344455",
        "email_normalized": "sara13@example.com", "product": "CRM setup", "budget_egp": "6000",
        "band": "Hot", "score": "85", "stage_failed": "create_deal", "error_message": "seeded assoc fail",
        "contact_id": "c9999", "deal_id": "", "status": "pending", "retry_count": "0", "created_at": "2026-11-22T00:00:00Z"}]})
    run_manual(rec_id, "Manual Trigger")
    L = log()
    d13 = [d for d in L["deals"] if d["properties"].get("dealname", "").startswith("Assoc Fail Sara")]
    check("T13 assoc failure: deal still created", len(d13) == 1, d13)
    check("T13 assoc failure: no new contact created", len(L["contacts"]) == 3)
    r13 = [r for r in L["sheets"]["Dead_Letter_Queue"] if r.get("lead_id") == "lead-seed13"]
    check("T13 assoc failure: row recovered with recovered_at set", len(r13) == 1 and r13[0].get("status") == "recovered" and r13[0].get("recovered_at") != "" and r13[0].get("created_at") == "2026-11-22T00:00:00Z", r13)
    check("T13 assoc failure: row notes the association failure, deal left unassociated", len(r13) == 1 and "association" in str(r13[0].get("error_message") or "").lower() and d13[0]["properties"].get("associated_contact") is None, (r13, d13))

    # ---- SLA workflow
    sla_id = import_workflow("sla-workflow-MOCK.json")
    check("import SLA MOCK workflow", sla_id)
    now = time.time()
    def hrs_ago(h):
        import datetime
        return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=h)).isoformat().replace("+00:00", "Z")
    mock("POST", "/seed/deals", {"deals": [
        {"dealname": "Hot Old", "dealstage": "appointmentscheduled", "createdate": hrs_ago(25), "reminder_sent": "false", "follow_up_due": "false", "band": "Hot"},
        {"dealname": "Warm Old", "dealstage": "appointmentscheduled", "createdate": hrs_ago(49), "reminder_sent": "false", "follow_up_due": "false", "band": "Warm"},
        {"dealname": "Hot Fresh", "dealstage": "appointmentscheduled", "createdate": hrs_ago(5), "reminder_sent": "false", "follow_up_due": "false", "band": "Hot"},
        {"dealname": "Hot Replied", "dealstage": "qualifiedtobuy", "createdate": hrs_ago(30), "reminder_sent": "false", "follow_up_due": "false", "band": "Hot"},
        {"dealname": "Hot Already Reminded", "dealstage": "appointmentscheduled", "createdate": hrs_ago(30), "reminder_sent": "true", "follow_up_due": "true", "band": "Hot"},
    ]})
    n_tg_before = len(log()["telegram"])
    run_manual(sla_id, "SLA Check — Every Hour")
    L = log()
    patched = [c for c in L["calls"] if c["method"] == "PATCH" and "/objects/deals/d" in c["path"]]
    check("T11 SLA: exactly 2 deals marked (Hot Old, Warm Old)", len(patched) == 2, patched)
    check("T11 SLA: 2 reminder telegrams", len(L["telegram"]) - n_tg_before == 2, L["telegram"][n_tg_before:])
    check("T11 SLA: Hot Old reminder_sent=true in mock", any(d["properties"].get("dealname") == "Hot Old" and d["properties"].get("reminder_sent") == "true" for d in L["deals"]))
    check("T11 SLA: Hot Fresh NOT reminded", all(d["properties"].get("reminder_sent") != "true" or d["properties"].get("dealname") != "Hot Fresh" for d in L["deals"]))
    # re-run: no duplicates
    n_tg = len(L["telegram"])
    run_manual(sla_id, "SLA Check — Every Hour")
    L = log()
    check("T11 SLA re-run: no duplicate reminders", len(L["telegram"]) == n_tg, f"telegram {n_tg} -> {len(L['telegram'])}")

    # T12: Telegram failure after mark (seed a deal whose name triggers FAIL_TELEGRAM in the alert text)
    mock("POST", "/seed/deals", {"deals": [
        {"dealname": "FAIL_TELEGRAM Deal", "dealstage": "appointmentscheduled", "createdate": hrs_ago(30), "reminder_sent": "false", "follow_up_due": "false", "band": "Hot"},
    ]})
    n_tg = len(log()["telegram"])
    run_manual(sla_id, "SLA Check — Every Hour")
    L = log()
    d12 = [d for d in L["deals"] if d["properties"].get("dealname") == "FAIL_TELEGRAM Deal"]
    check("T12 telegram failure: reminder_sent still marked true", len(d12) == 1 and d12[0]["properties"].get("reminder_sent") == "true", d12)
    check("T12 telegram failure: no telegram recorded for it", len(L["telegram"]) == n_tg)
    check("T12 telegram failure: audit row records telegram_failed", len([r for r in L["sheets"]["Audit_Log"] if r.get("telegram_failed") == "true"]) >= 1)
    # re-run: no re-reminder for the failed one either
    run_manual(sla_id, "SLA Check — Every Hour")
    L = log()
    check("T12 re-run: still no re-reminder", len(L["telegram"]) == n_tg)

    out = {"passed": sum(1 for r in results if r["pass"]), "failed": sum(1 for r in results if not r["pass"]), "checks": results}
    pathlib.Path(__file__).parent.joinpath("n8n-test-results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n== {out['passed']} passed, {out['failed']} failed ==")

if __name__ == "__main__":
    main()
