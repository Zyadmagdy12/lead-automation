# تعليمات الإعداد اليدوي — العينة 1: Lead Automation (v3)

**لا ترسل أي كلمة مرور أو API Token أو Service Key في المحادثة أبداً.**
كل مفتاح يُدخل فقط في المكان المخصص له: HubSpot أو n8n أو Google أو Telegram. إن طُلب منك مفتاح هنا، لا ترسله.

## ما تحتاجه قبل البدء (حوالي ساعة)

1. حساب HubSpot Free (مجاني).
2. حساب n8n — سحابي أو على جهازك، **الإصدار 2.48.7** (الـ Workflows صُدِّرت من هذا الإصدار).
3. حساب Google (لـ Sheets).
4. تطبيق Telegram على هاتفك.

## ما تم اختباره فعلاً (وما لم يُختبر)

- **تم في n8n 2.48.7 حقيقي ضد Mock:** استيراد الـ Workflows الأربعة، تشغيل كامل (فورم حقيقي، HubSpot REST وهمي، Sheets وهمية، Telegram وهمي)، **58/58 اختباراً ناجحاً** (T0–T13، بما فيها ربط الـ Deal بعد الاسترداد وفشل الربط غير القاتل).
- **صُدِّرت ملفات JSON من n8n نفسه** بعد الاستيراد (`exported-from-n8n/`) وطابقت مخرجات الباني دون أي اختلاف في المعاملات أو الاتصالات، وهي **مرفقة في الـ ZIP** (مجلد `exported-from-n8n/`).
- **لم يُختبر بعد (Blocked):** الاتصال بـ HubSpot / Sheets / Telegram الحقيقية، والتشغيل الحي بـ 20 ليد، وانتظار SLA الفعلي 24/48 ساعة. هذه خطواتك التالية بعد الإعداد.

## القيم التي يجب استبدالها بعد الاستيراد (Placeholders)

ابحث في كل ملف JSON عن هذه القيم واستبدلها في n8n بعد الاستيراد (كلها ظاهرة في العقد، لا أسرار مخفية):

| القيمة | أين | ماذا تضع مكانها |
|---|---|---|
| `default` (pipeline) | جسم إنشاء الـ Deal (الرئيسي والاسترداد) | ID الـ Pipeline الحقيقي في HubSpot (Settings → Objects → Deals → Pipelines) |
| `appointmentscheduled` (dealstage) | جسم إنشاء الـ Deal + بحث الـ SLA | ID مرحلة "New" في الـ Pipeline نفسه — **مصدر واحد**: في الباني هو الثابت `STAGE_NEW_ID` في `builders_v3_extra.py` (كل الأجسام والبحث يُبنون منه، فلا يمكن أن يختلفا). إن عدّلته يدوياً في n8n بعد الاستيراد، بدّله في الأماكن الثلاثة: (1) جسم الـ Deal في عقدة `Validate Search Result` (الرئيسي)، (2) جسم الـ Deal في عقدة `Filter Pending Rows` (الاسترداد)، (3) جسم البحث في عقدة `Search New Deals` (الـ SLA). كود `SLA Due?` لا يحتوي أي فحص للمرحلة — يعتمد على البحث وحده، فتغيير البحث يكفي داخل workflow الـ SLA. |
| `FILL_SHEETS_ID` | عقد Google Sheets (رابط الـ Sheet) | رقم الـ Sheet من الرابط (بين `/d/` و`/edit`) |
| `FILL_TELEGRAM_CHAT_ID` | عقد Telegram | الـ Chat ID الخاص بك |
| Credential باسم "HubSpot Service Key" (بلا ID) | كل عقد HTTP لـ HubSpot | أنشئ Header Auth بالـ Service Key (الخطوة 2) واختره في كل عقدة |

## الخطوة 1 — HubSpot Free + الحقول المخصصة

1. أنشئ حساب HubSpot Free، وادخل إلى الـ CRM.
2. أنشئ Pipeline واحداً للـ Deals (أي مراحل؛ سجّل الـ IDs في الجدول أعلاه).
3. في **Deal**، أنشئ الحقول المخصصة التالية **قبل الاستيراد** (إلزامي، وإلا سيفشل إنشاء الـ Deal):
   - `band` (نص)
   - `lead_score` (رقم)
   - `reminder_sent` (Checkbox / Boolean، افتراضي false)
   - `follow_up_due` (Checkbox / Boolean، افتراضي false)
4. في **Contact** لا حاجة لحقول مخصصة: الـ Workflow يرسل فقط `email` و`phone` و`firstname` و`lastname` (حقول قياسية).
5. لا تنشئ أي Contact يدوياً الآن — الاختبار سيستخدم بيانات تجريبية.

## الخطوة 2 — Service Key في HubSpot (وليس Private App)

1. في HubSpot، اذهب إلى **Development → Keys → Service keys**.
2. اضغط **Create service key**، وسمّه `Lead Automation Sample`.
3. اختر الـ Scopes التالية فقط:
   - `crm.objects.contacts.read`
   - `crm.objects.contacts.write`
   - `crm.objects.deals.read`
   - `crm.objects.deals.write`
4. انسخ المفتاح فوراً واحفظه في مدير كلمات مرور خاص بك. **لا تلصقه في المحادثة.**
5. ملاحظة: لا تنشئ Private App قديماً — إنشاؤها توقف في أكتوبر 2026. ولا تستخدم OAuth2 في n8n لهذه العينة. الـ Workflow لا يستخدم عقدة HubSpot أصلاً، بل REST عبر HTTP Request.

## الخطوة 3 — Google Sheet

1. أنشئ Google Sheet جديداً، وسمّه `Lead Automation Sample`.
2. أنشئ تابين بالاسمين بالضبط، وبالأعمدة التالية (الصف الأول = أسماء الأعمدة):
   - `Dead_Letter_Queue`: lead_id, name, phone_normalized, email_normalized, product, budget_egp, band, score, stage_failed, error_message, contact_id, deal_id, status, retry_count, created_at, recovered_at
   - `Audit_Log`: lead_id, action, band, score, duplicate_of, contact_id, deal_id, association_failed, telegram_failed, created_at, threshold_hours
   - ملاحظة الأعمدة: `recovered_at` يملؤه workflow الاسترداد فقط عند نجاح الاسترداد (ويبقى فارغاً في صفوف DLQ الجديدة)، و`threshold_hours` يملؤه SLA فقط (24 أو 48). أي عمود زائد في التاب يبقى فارغاً في الصفوف التي لا تملؤه — لا تحذف عموداً لأن workflow آخر لا يستخدمه.
3. انسخ رقم الـ Sheet من الرابط (الجزء بين `/d/` و`/edit`) واحفظه جانباً — ستضعه في n8n فقط.

## الخطوة 4 — Telegram Bot

1. في Telegram، افتح `@BotFather`، وأنشئ بوتاً جديداً، واحفظ الـ Token في مدير كلمات مرورك. **لا ترسله هنا.**
2. ابدأ محادثة مع البوت الجديد من حسابك (أرسل `/start`).
3. لمعرفة Chat ID الخاص بك، افتح في المتصفح: `https://api.telegram.org/bot<TOKEN>/getUpdates` بعد إرسال رسالة للبوت، وابحث عن `chat.id`. ضع الـ Token والـ ID في n8n فقط.

## الخطوة 5 — استيراد الـ Workflows في n8n

1. في n8n، اذهب إلى Workflows → Import from File، واستورد الملفات الأربعة من الـ ZIP:
   - `lead-automation-workflow.json` (الرئيسي، 33 عقدة)
   - `sla-workflow.json` (13 عقدة)
   - `error-workflow.json` (5 عقد)
   - `dlq-recovery-workflow.json` (25 عقدة)
2. **HubSpot (في كل عقد HTTP):** أنشئ Credential جديد من نوع **Header Auth**:
   - الاسم: `HubSpot Service Key`
   - Header Name: `Authorization`
   - Header Value: `Bearer <Service Key من الخطوة 2>` (اكتب Bearer ثم مسافة ثم المفتاح)
   - اختر هذا الـ Credential في كل عقدة HTTP التي تتصل بـ `api.hubapi.com`.
3. **Google Sheets:** اربط حساب Google في عقد Sheets، وضع رقم الـ Sheet من الخطوة 3 مكان `FILL_SHEETS_ID`.
4. **Telegram:** أنشئ Credential بالـ Token من الخطوة 4، وضع Chat ID مكان `FILL_TELEGRAM_CHAT_ID`.
5. **Pipeline/Stage:** استبدل `default` و`appointmentscheduled` بالـ IDs الحقيقية (الجدول أعلاه). ملاحظة: الـ SLA يبحث عن Deals في مرحلة New فقط، فاستخدم نفس الـ Stage ID في الأماكن الثلاثة المذكورة في الجدول. وإن غيّرت مرحلة New لاحقاً في HubSpot، غيّر البحث أولاً ثم أجسام إنشاء الـ Deal — كود `SLA Due?` لن يكسر التذكيرات لأنه لا يفحص المرحلة أصلاً.
6. استورد `error-workflow.json`، ثم في إعدادات الـ Workflow الرئيسي (Settings → Error Workflow) اختر Error Handler workflow. وفعّل Save Failed Executions.
7. تحقق من إصدارات الـ Nodes بعد الاستيراد: Form Trigger 2.2، Code 2، HTTP Request 4.5، Google Sheets 4.7، Telegram 1.2، Schedule Trigger 1.2، IF 2.2، Merge 3، Error Trigger 1، Manual Trigger 1. إن ظهر إصدار مختلف، لا تغيّره يدوياً قبل إخباري — سجّله في تقرير التشغيل الحي.
8. فعّل الـ Workflows (Active = true) فقط بعد الخطوة 7.

## الخطوة 6 — التشغيل التجريبي (20 ليد)

1. افتح فورم الـ Lead في n8n، وأدخل أول 3 ليدات من `eval-set.csv` (L01، L03، L05).
2. تحقق في HubSpot: L01 ظهر كـ Contact + Deal، L03 ظهر Warm، L05 لم يُنشئ تنبيه بيع.
3. أدخل L07 (نفس هاتف L01) وتحقق: لا Contact جديد، بل تحديث + سجل في Audit Log.
4. لاختبار الفشل: عطّل Credential HubSpot مؤقتاً في n8n، ثم أدخل L16. تحقق: ظهر في `Dead_Letter_Queue`، ووصل تنبيه Telegram.
5. أعد تفعيل Credential، وشغّل `dlq-recovery-workflow` يدوياً. تحقق: لم يُنشأ Contact مكرر، والصف أصبح recovered، **والـ Deal المسترد مرتبط بالـ Contact** (افتح الـ Deal في HubSpot وتأكد أن الـ Contact ظاهر كـ Associated — لا Deal يتيم). وتحقق أن `recovered_at` مملوء في صف الـ DLQ.
6. سجّل النتائج الفعلية في `review-report.md` تحت قسم "Live Run" — بما فيها أي فشل حدث فعلاً.

## الخطوة 7 — اختبار SLA (يحتاج انتظاراً فعلياً)

1. أنشئ ليد Hot تجريبياً واتركه بلا رد.
2. بعد 24 ساعة فعلاً، تحقق: وصل تذكير Telegram مرة واحدة فقط.
3. أنشئ ليد Warm واتركه بلا رد، وبعد 48 ساعة تحقق من التذكير.
4. سجّل التواريخ والأوقات الفعلية. لا تعتمد على تواريخ مُقدَّمة في الاختبار الحي.

## بعد الإعداد

أخبرني فقط بالنتيجة، لا بالمفاتيح: مثلاً "HubSpot متصل، 20/20 حياً، SLA بعد 24 ساعة وصل مرة واحدة". عندها فقط نحدّث `readiness-review.md` ونعلن العينة جاهزة.
