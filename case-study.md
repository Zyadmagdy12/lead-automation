# Case Study — Lead Automation (Synthetic)

**All data in this case study is synthetic, invented for demonstration. It proves the system works as designed; it does not prove real sales results.**

## The problem (as a typical small business experiences it)

Leads arrive from a form, WhatsApp and email, in no order. The owner checks them when they have time. Good leads wait hours. The same person writes twice and gets counted twice. A bad lead wastes a sales call. And when the CRM has a bad day, a lead simply disappears — nobody notices until the customer buys elsewhere.

## Before

- Leads in three inboxes, no single list.
- No scoring: every message looks equally urgent.
- No duplicate check: the same phone creates two contacts.
- No follow-up clock: a Hot lead unanswered for two days looks the same as a new one.
- No safety net: a failed CRM write is a lost lead.

## What was built

One n8n flow, with a real CRM (HubSpot Free) and a Telegram alert channel:

1. **Capture** from a form (WhatsApp / email in the full design).
2. **Clean** phone and email into a standard form.
3. **Score** with explicit rules (budget, product, contact completeness, intent) — Hot needs a budget of 3,000+ and clear intent, not just a high total.
4. **Dedup** by normalised phone or email before anything is created.
5. **Record** in HubSpot, alert sales on Telegram for Hot leads.
6. **Follow up** on a clock: Hot after 24 hours without a reply, Warm after 48 hours.
7. **Survive failure:** HubSpot down → Sheets dead-letter queue → 3 retries → failure alert. The lead is never deleted.

## Evidence

- 20 synthetic leads run through the exact logic: **20/20 passed, 0 lost**.
- 3 planted duplicates caught; 1 similar-name / different-phone case correctly kept as new.
- 1 simulated HubSpot failure correctly routed to the dead-letter queue.
- 4 junk/incomplete leads correctly kept out of the sales alerts.

## What this does NOT prove

It does not prove that a real business would gain revenue, save a measured number of hours, or convert more leads. Those claims would need a real pilot, which this sample deliberately does not claim. What it proves is: the workflow handles the messy cases — duplicates, junk, missing data, and integration failure — instead of only the happy path.
