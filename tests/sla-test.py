#!/usr/bin/env python3
"""SLA test — local logic only, synthetic data.
Simulates time passing (no real 24/48h wait) with explicit created_at timestamps.
Rules from scope.md:
- Hot, no reply in 24h -> reminder once, follow_up_due = True
- Warm, no reply in 48h -> reminder once, follow_up_due = True
- Replied leads -> no reminder
- A reminder must never be sent twice for the same lead

Run: python3 sla-test.py
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent

def check_sla(leads, now):
    """Returns list of (lead_id, action). Mutates reminder_sent flag."""
    actions = []
    for lead in leads:
        if lead.get("replied"):
            continue
        age_h = (now - lead["created_at"]).total_seconds() / 3600
        threshold = 24 if lead["band"] == "Hot" else 48 if lead["band"] == "Warm" else None
        if threshold is None:
            continue
        if age_h >= threshold and not lead.get("reminder_sent"):
            lead["reminder_sent"] = True
            lead["follow_up_due"] = True
            actions.append((lead["id"], "reminder_sent"))
    return actions

def main():
    t0 = datetime(2026, 11, 21, 9, 0, 0)
    leads = [
        {"id": "S01", "band": "Hot", "created_at": t0, "replied": False, "reminder_sent": False},
        {"id": "S02", "band": "Hot", "created_at": t0, "replied": True, "reminder_sent": False},
        {"id": "S03", "band": "Warm", "created_at": t0, "replied": False, "reminder_sent": False},
        {"id": "S04", "band": "Warm", "created_at": t0, "replied": False, "reminder_sent": False},
        {"id": "S05", "band": "Junk", "created_at": t0, "replied": False, "reminder_sent": False},
        {"id": "S06", "band": "Hot", "created_at": t0 + timedelta(hours=20), "replied": False, "reminder_sent": False},
    ]
    cases = []
    # At +24h: S01 Hot should fire, S03 Warm should NOT (needs 48h), S02 replied no, S06 only 4h old no
    a24 = check_sla(leads, t0 + timedelta(hours=24))
    cases.append({"case": "SLA +24h Hot fires", "got": a24, "expected": [("S01", "reminder_sent")], "pass": a24 == [("S01", "reminder_sent")]})
    # At +25h again: no duplicate reminder for S01
    a25 = check_sla(leads, t0 + timedelta(hours=25))
    cases.append({"case": "SLA +25h no duplicate reminder", "got": a25, "expected": [], "pass": a25 == []})
    # At +48h: S03 and S04 Warm fire (48h old), S06 Hot fires (28h old, threshold 24h), S01 does not refire
    a48 = check_sla(leads, t0 + timedelta(hours=48))
    cases.append({"case": "SLA +48h Warm + late Hot fire", "got": sorted(a48), "expected": sorted([("S03", "reminder_sent"), ("S04", "reminder_sent"), ("S06", "reminder_sent")]), "pass": sorted(a48) == sorted([("S03", "reminder_sent"), ("S04", "reminder_sent"), ("S06", "reminder_sent")])})
    # At +49h: nothing refires
    a49 = check_sla(leads, t0 + timedelta(hours=49))
    cases.append({"case": "SLA +49h no duplicate reminder", "got": a49, "expected": [], "pass": a49 == []})
    passed = sum(c["pass"] for c in cases)
    print(json.dumps({"total": len(cases), "passed": passed, "failed": len(cases) - passed}, indent=2))
    for c in cases:
        print(f"{c['case']}: got={c['got']} pass={c['pass']}")
    (BASE / "sla-test-results.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == len(cases) else 1

if __name__ == "__main__":
    raise SystemExit(main())
