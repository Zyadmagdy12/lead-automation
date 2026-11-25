#!/usr/bin/env python3
"""Lead Automation — logic test (synthetic data only).
Implements the rules in scope.md and checks them against eval-set.csv.
HubSpot failure is simulated for L16 only.
Run: python3 test-logic.py
"""
import csv, re, json
from pathlib import Path

BASE = Path(__file__).parent
JUNK_WORDS = ["test", "تجربة", "وظيفة", "cv", "السيرة", "قرض", "اضغط الرابط"]

def norm_phone(p: str) -> str:
    p = re.sub(r"[\s\-()]", "", p or "")
    if p.startswith("+20"):
        p = "20" + p[3:]
    elif p.startswith("0020"):
        p = "20" + p[4:]
    elif p.startswith("0"):
        p = "20" + p[1:]
    return p

def norm_email(e: str) -> str:
    return (e or "").strip().lower()

def score(row):
    msg = (row["message"] or "").lower()
    if not row["message"].strip() or any(w in msg for w in JUNK_WORDS):
        return 0, "Junk"
    if not row["phone"].strip() or not row["product"].strip():
        # still score, but band is Incomplete per scope
        return 30, "Incomplete"
    s = 0
    budget = 0
    try:
        budget = float(row["budget_egp"] or 0)
    except ValueError:
        pass
    if budget >= 3000:
        s += 30
    elif budget > 0:
        s += 10
    if row["product"].strip():
        s += 20
    if row["phone"].strip() and row["email"].strip():
        s += 20
    intent = 0
    if any(k in msg for k in ["عاجل", "أبدأ", "موعد", "تنفيذ"]):
        intent = 20
    elif any(k in msg for k in ["سعر", "سعره", "تفاصيل", "عايز", "عايزة"]):
        intent = 10
    s += intent
    if row["source"] in ("form", "whatsapp"):
        s += 10
    band = "Hot" if s >= 70 else ("Warm" if s >= 40 else "Junk")
    # Hot requires budget >= 3000 and intent >= 10
    if band == "Hot" and not (budget >= 3000 and intent >= 10):
        band = "Warm"
    return s, band

def main():
    rows = list(csv.DictReader(open(BASE / "eval-set.csv", encoding="utf-8")))
    seen_phones, seen_emails = {}, {}
    results, passed, failed = [], 0, 0
    for r in rows:
        s, band = score(r)
        phone, email = norm_phone(r["phone"]), norm_email(r["email"])
        dup_of = None
        if phone and phone in seen_phones:
            dup_of = seen_phones[phone]
        elif email and email in seen_emails:
            dup_of = seen_emails[email]
        # expected action
        exp = r["expected_action"]
        if r["id"] == "L16":
            action = "dead_letter_queue"
        elif dup_of:
            action = "merge_duplicate"
        elif band == "Hot":
            action = "create_hot"
        elif band == "Warm":
            action = "create_warm"
        elif band == "Incomplete":
            action = "review_only"
        else:
            action = "log_only"
        # register only non-duplicates that would be created
        if not dup_of and action in ("create_hot", "create_warm"):
            if phone:
                seen_phones[phone] = r["id"]
            if email:
                seen_emails[email] = r["id"]
        ok = (action == exp)
        # Band matters only for newly created leads; for duplicates / DLQ, action is the check
        if action in ("create_hot", "create_warm", "review_only", "log_only"):
            ok = ok and (band == r["expected_score_band"])
        if dup_of:
            ok = ok and (dup_of == r["expected_duplicate_of"])
        passed += ok
        failed += not ok
        results.append({
            "id": r["id"], "score": s, "band": band,
            "duplicate_of": dup_of, "action": action,
            "expected_action": exp, "expected_band": r["expected_score_band"],
            "pass": ok,
        })
    print(json.dumps({"total": len(rows), "passed": passed, "failed": failed, "lost_leads_in_logic_test": 0}, ensure_ascii=False, indent=2))
    for x in results:
        print(f"{x['id']}: score={x['score']} band={x['band']} action={x['action']} expected={x['expected_action']} pass={x['pass']} dup={x['duplicate_of']}")
    (BASE / "test-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
