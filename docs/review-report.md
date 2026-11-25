# تقرير المراجعة — العينة 1: Lead Automation (الجولة 3، v3 + الجولة 4، v4)

**التاريخ:** 2026-11-22. **n8n المستهدف:** 2.48.7. **طريقة العمل:** n8n حقيقي مثبَّت محلياً (npm) + خادم Mock يحاكي HubSpot REST وGoogle Sheets وTelegram. كل ادعاء أدناه له دليل تنفيذي، لا فحص ملفات فقط.

## الجولة 4 (2026-11-22) — النقاط الثلاث من مراجعة v3 + مجلد التصدير

| # | النقطة | الإصلاح | الدليل |
|---|---|---|---|
| 1 | DLQ Recovery ينشئ Deal دون Association تربطه بالـ Contact | أُضيفت 3 عقد للاسترداد (25 عقدة): Associate Deal to Contact (PUT، idempotent، retry ‏3x) → Merge Association Result → Validate Recovery Association، بعد Validate Recovery Deal. | **T8b**: الـ Deal المسترد لصف T6 مرتبط بالـ Contact الموجود (`associated_contact == c1002`). **T9b**: الـ Deal المسترد لصف جديد مرتبط بالـ Contact المنشأ أثناء الاسترداد (`associated_contact == c1003`). **T13**: فشل الربط (PUT ‏404 لكونتاكت غير موجود) غير قاتل — الـ Deal يُنشأ، والصف يُسجَّل recovered مع `recovered_at`، والفشل موثَّق في `error_message`، والـ Deal يبقى بلا ربط. إعادة المحاولة عندها كانت ستنشئ Deal ثانياً، لذلك لا تُعاد. |
| 2 | كود `SLA Due?` يحتوي `appointmentscheduled` ثابتة | **مصدر واحد:** الـ Pipeline والـ Stage الآن في ثابتين فقط (`PIPELINE_ID` / `STAGE_NEW_ID` في `builders_v3_extra.py`)، وكل أجسام إنشاء الـ Deal (الرئيسي والاسترداد) وفلتر بحث الـ SLA يُبنون منهما. قبل الإصلاح كان النص `appointmentscheduled` مكرراً في 4 أماكن (تعريفان + جسمان مشفران). **وإزالة الفحص المكرر:** كود `SLA Due?` لم يعد يفحص `dealstage` إطلاقاً — فلتر البحث هو نقطة التنفيذ الوحيدة للمرحلة. | **T0**: النص يظهر مرة واحدة في الباني، وقيمة واحدة في كل ملفات JSON الثمانية (6 مواضع)، وقيمة Pipeline واحدة (4 مواضع). **T11** ما زال يمر: "Hot Replied" (خرج من New) يستبعده البحث وحده، فتغيير المرحلة في HubSpot لا يكسر التذكيرات ما دام البحث وأجسام الإنشاء يشيران للمرحلة الجديدة (الأماكن الثلاثة موثقة في `setup-instructions.md`). |
| 3 | `recovered_at` غير موجود في أعمدة Dead_Letter_Queue بتعليمات الإعداد | أُضيف `recovered_at` لسطر أعمدة `Dead_Letter_Queue` (16 عموداً)، ورُوجعت كل الأعمدة مقابل JSON: أُضيف `threshold_hours` أيضاً لسطر `Audit_Log` (11 عموداً) لأنه في مخطط SLA JSON ولم يكن في التعليمات. | **T0**: اختبار آلي يقرأ `DLQ_COLUMNS` من الباني ويطابقها مع سطر الأعمدة في `setup-instructions.md` (بما فيها `recovered_at`). **T8/T9/T13**: الصفوف المستردة تحمل `recovered_at` مملوءاً فعلاً. |
| 4 | إشارة `exported-from-n8n/` بلا مجلد مرفق | **مؤكد:** مجلد `exported-from-n8n/` لم يكن في ZIP ‏v3 (فحص `unzip -l`: لا نتائج). أُعيد التصدير من n8n لـ v4 وأُرفق المجلد في ZIP ‏v4. | الاستيراد والتصدير من n8n ‏2.48.7 لـ v4: **0 اختلاف في المعاملات، والاتصالات متطابقة** في الـ Workflows الأربعة (الفرق الوحيد: `webhookId` التي يعيّنها n8n نفسه عند الاستيراد). |

**عيب إضافي اكتُشف أثناء اختبارات الجولة 4 (لم يكن في قائمة المراجعة):** عقدة `Filter Pending Rows` في الاسترداد كانت تُسقط `created_at` الأصلي للصف — فصف الاسترداد كان يكتب `created_at` فارغاً فوق القيمة الأصلية في الـ Sheet (ظهر في تفاصيل T8/T9 في التشغيل الأول للجولة 4). أُصلح بسطر واحد (تمرير `created_at` في الفلتر)، وتأكد الحفظ في T8/T9/T13. لم يتغير أي شيء آخر بسبب هذا الإصلاح: إعادة البناء بعده غيّرت ملفي الاسترداد فقط.

## العيوب السبعة المطلوب إصلاحها (من مراجعة ChatGPT للـ ZIP السابق) — والنتيجة

| # | العيب | الحكم بعد الإصلاح | الدليل |
|---|---|---|---|
| 1 | `json is not defined` في Normalize وScore | **أُصلح ومثبت** | اختبار Probe في n8n حقيقي: `json` غير معرَّف، `$json` و`$input` معرَّفان. كل أكواد الـ Workflow تستخدم `$input` فقط؛ فحص آلي يؤكد عدم وجود `json` عارٍ في أي Code Node |
| 2 | Form Trigger وحقوله ببنية خاطئة | **أُصلح ومثبت** | مصدر n8n 2.48.7: الإرسال يُقرأ حسب الموضع (`field-0`…). إرسال تجريبي بأسماء الحقول أعطى null لكل القيم؛ الإرسال حسب الموضع يعمل (T1–T7 تمر) |
| 3 | Junk وIncomplete قد ينشئان Contact/Deal أو تنبيهات | **أُصلح ومثبت** | فرع `Is Sellable?` يرسلهما لسجل تدقيق فقط. T3 (Junk): لا Contact ولا Deal ولا تنبيه. T4 (Incomplete): نفس الشيء + سجل `logged_incomplete` |
| 4 | HubSpot Search وDedup وCreate/Update وDeal وحفظ البيانات وربط Contact بالـ Deal | **أُصلح ومثبت** | HubSpot عبر REST API (عقدة HubSpot V2 بلا Update بالـ ID). T1: Contact + Deal + ربط صحيح. T2: مكرر بالهاتف → تحديث الإيميل، بلا Contact/Deal جديدين، بلا تنبيه |
| 5 | Google Sheets وTelegram وإعدادات الأخطاء والـ Credentials حسب بنية الإصدار | **أُصلح ومثبت** | HTTP Request v4.5 بمعاملات V3 الحقيقية؛ Sheets v4.7 بـ resourceMapper؛ Telegram v1.2 بـ resource=message. كلها تعمل ضد الـ Mock (T1–T12) |
| 6 | إعادة المحاولة واسترداد DLQ — منفذة فعلاً أم ادعاء؟ | **أُصلح ومثبت، مع تصحيح تصميمي مهم** | الاسترداد workflow منفصل (22 عقدة) يعمل: T8 (Deal فقط لصف به Contact)، T9 (صف جديد كامل)، T10 (صف فاشل يبقى pending وretry_count +1 بالضبط). **واكتُشف أن retryOnFail على عمليات Create يعيد إنشاء المورد** (عنصر ناجح أُعيد إنشاؤه 3 مرات بسبب عنصر فاشل في نفس الدفعة) — فأُزيل الـ Retry من كل Create/Append، وبقي فقط للعمليات idempotent (بحث، PATCH، PUT) |
| 7 | SLA: حقول HubSpot الفعلية، حالة الرد، فشل Telegram بعد تحديث reminder_sent | **أُصلح ومثبت** | T11: تذكيران فقط للمستحقين (Hot ‏24h، Warm ‏48h)، بلا تكرار عند إعادة التشغيل. T12: فشل Telegram بعد التحديث → ‏reminder_sent يبقى true، وسجل `telegram_failed`، وبلا إعادة تذكير |

## عيوب إضافية اكتُشفت أثناء التنفيذ (لم تكن في قائمة المراجعة)

1. **محلل تعبيرات n8n 2.48.7 يرفض Object literals المتداخلة** (`{properties: {...}}` → خطأ `invalid syntax`، مثبت بكلاس `Expression` الخاص بـ n8n). الحل: أجسام HubSpot تُسلسل مسبقاً في عقد Code وتُمرَّر كنص.
2. **IF يعيد تأسيس `pairedItem` على فهرس الفرع** — بحث `$('Node').all()[paired.item]` عبر فروع IF يعيد بيانات صف خاطئ (مثبت: Deal لصف خاطئ + Contact مكرر 3 مرات). الحل: عقد Merge (combine by position) في workflow الاسترداد.
3. **`pairedItem` خاصية وليست دالة** — استدعاؤها كدالة كان يكسر كل الـ Validators.
4. **عقد Code بـ `$input.first()` تُسقط كل العناصر بعد الأول بصمت** في التدفقات متعددة العناصر — كلها حُوِّلت لحلقات على `$input.all()`.
5. **دمج Merge العميق (lodash) يلوث `body`:** جسم استجابة Contact القديم كان يكتب فوق `body.id` الخاص بالـ Deal. الحل: الـ Validators تجرّد حقول الاستجابة (`body`، `statusCode`، `headers`، `error`) قبل تمرير البيانات.

## نتائج الاختبار الكاملة (n8n 2.48.7 حقيقي + Mock)

`run_n8n_tests.py` — **58/58 ناجحة** (الملف الكامل: `n8n-test-results.json`):

- **T0** فحوصات ثبات ستاتيكية (6): الـ Stage معرَّف مرة واحدة في الباني، وقيمة واحدة في كل JSON، والـ Pipeline كذلك، و`SLA Due?` بلا فحص مرحلة، وأعمدة DLQ في التعليمات تطابق JSON (بما فيها `recovered_at`).
- **T1** ليد Hot جديد: Contact + Deal + ربط + تنبيه مبيعات + سجل تدقيق.
- **T2** مكرر بنفس الهاتف: تحديث الإيميل، بلا Contact/Deal/تنبيه جديدين.
- **T3** Junk: بلا Contact/Deal/تنبيه، وسجل `logged_junk`.
- **T4** Incomplete: بلا Contact/Deal، وسجل `logged_incomplete`.
- **T5** فشل إنشاء Contact: بلا Contact/Deal، صف DLQ pending، وتنبيه فشل.
- **T6** فشل إنشاء Deal (نجاح جزئي): الـ Contact موجود، بلا Deal، وصف DLQ يحتفظ بـ `contact_id`.
- **T7** فشل البحث: بلا Contact، وصف DLQ (stage ‏`unknown`).
- **T8** استرداد صف T6: Deal فقط، بلا Contact جديد، والصف recovered، و`created_at` الأصلي محفوظ.
- **T8b** استرداد صف T6: الـ Deal المسترد **مرتبط بالـ Contact الموجود**.
- **T9** استرداد صف جديد: Contact + Deal، والصف recovered، و`deal_id` حقيقي.
- **T9b** استرداد صف جديد: الـ Deal المسترد **مرتبط بالـ Contact المنشأ أثناء الاسترداد**.
- **T10** استرداد صف فاشل: يبقى pending وretry_count +1 بالضبط، وبلا Contact مكرر.
- **T13** فشل الربط (كونتاكت غير موجود): الـ Deal يُنشأ، والصف recovered مع `recovered_at`، والفشل موثَّق في `error_message`، وبلا Contact جديد.
- **T11** ‏SLA: تذكيران فقط للمستحقين، وبلا تكرار عند إعادة التشغيل.
- **T12** فشل Telegram بعد التحديث: ‏reminder_sent يبقى true، وسجل `telegram_failed`، وبلا إعادة تذكير.

اختبارات المنطق القديمة (محلية، قبل n8n): `test-logic.py` ‏20/20، `failure-matrix-test.py` ‏9/9، `sla-test.py` ‏4/4.

## التصنيف النهائي

### Verified (بدليل تنفيذي في n8n 2.48.7)
- كل ما في جدول T0–T13 أعلاه.
- استيراد الـ Workflows الأربعة الحقيقية (v4) في n8n وتصديرها منه دون أي اختلاف في المعاملات أو الاتصالات (`exported-from-n8n/`، مرفقة في الـ ZIP).
- كل التعبيرات وكل أكواد JS تمر في محلل n8n نفسه.

### Blocked by Credentials (يحتاج حسابات المستخدم — لم تُطلب بعد عمداً)
- الاتصال الحي بـ HubSpot Free (Service Key)، وGoogle Sheets، وبوت Telegram.
- التشغيل الحي بـ 20 ليد (من `eval-set.csv`) وتسجيل النتائج الفعلية.
- انتظار SLA الفعلي 24/48 ساعة.

### Not Tested
- التشغيل الحي الكامل (أعلاه).
- سلوك الـ Workflows على إصدار n8n غير 2.48.7 (3.x غير مدعوم لهذه العينة).
- حدود HubSpot Free (rate limits) تحت حمل حقيقي.

### Needs Fix
- لا شيء معروف حالياً في نطاق العينة. أي عيب جديد يظهر في التشغيل الحي يُسجَّل هنا أولاً قبل إعلان الجاهزية.

## ملفات v4 (المصدر)
- `build_workflows_v3.py` + `builders_v3_extra.py` — الباني (المصدر الوحيد؛ لا تعديل يدوي على JSON).
- `lead-automation-workflow.json` (33 عقدة)، `sla-workflow.json` (13)، `error-workflow.json` (5)، `dlq-recovery-workflow.json` (25) — نسخ حقيقية.
- `*-MOCK.json` — نسخ الاختبار (تشير للـ Mock المحلي).
- `exported-from-n8n/` — الملفات الأربعة كما صدّرها n8n نفسه لـ v4 (وهي ما في الـ ZIP).
- `mock-server.js` + `run_n8n_tests.py` + `n8n-test-results.json` — أدوات الاختبار ونتائجها (58/58).

## Live Run (يُملأ بعد ربط الحسابات)
- (فارغ — بانتظار التشغيل الحي)
