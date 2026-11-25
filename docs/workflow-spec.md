# العينة 1 — Lead Automation: مواصفة الـ Workflow (v3 — مختبَرة في n8n 2.48.7 فعلياً)

**الحالة:** الـ Workflows الأربعة منفَّذة ومختبَرة في n8n **2.48.7 حقيقي** (مثبَّت محلياً عبر npm) ضد خادم Mock يحاكي HubSpot REST API وGoogle Sheets وTelegram. نتيجة مجموعة الاختبار: **58/58 ناجحة** (`run_n8n_tests.py`). التشغيل الحي ضد حسابات حقيقية **لم يُختبر بعد** — انظر `readiness-review.md`.

## HubSpot — عبر REST API، وليس عقدة HubSpot
- **Service Key** فقط، تُلصق في n8n كـ Header Auth (`Authorization: Bearer <Service Key>`). **لا OAuth2، ولا API Key قديم.**
- إنشاء Private Apps القديمة توقف 26 أكتوبر 2026. Service Keys هي البديل، وREST فقط.
- Scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.objects.deals.read`, `crm.objects.deals.write` — لا أكثر.
- **لماذا REST وليس عقدة HubSpot؟** عقدة HubSpot V2 في n8n 2.48.7 لا توفر Update لـ Contact بالـ ID (عملياتها: upsert/delete/get/getAll/search فقط)، وبحثها وحقول الـ pipeline/stage تعتمد على loadOptions تحتاج بيانات حية. كل مكالمات HubSpot تتم عبر عقد HTTP Request إلى `https://api.hubapi.com/crm/v3/...` بكرِدنشن Header Auth واحد.
- **لا HubSpot Trigger:** يحتاج Developer App منفصل. الدخول عبر Form Trigger، والمتابعة عبر Schedule Trigger + بحث (Polling).
- **إصدار n8n المستهدف: 2.48.7.**

## البنية (4 Workflows)

### 1. الرئيسي — `lead-automation-workflow.json` (33 عقدة)
```
Form Trigger (v2.2)
  → Normalize (Code) → Score (Code, حقيقي 0–100)
  → Is Sellable? (IF)
      false → Junk/Incomplete: سجل تدقيق فقط، لا Contact ولا Deal ولا تنبيه مبيعات
      true  → Search Contact by Phone/Email (HubSpot REST, retry 3x)
              → Validate Search Result (Code)
              → Search Failed? (IF) → true: DLQ + تنبيه فشل
              → Is Duplicate? (IF)
                  true  → Update Contact (PATCH, idempotent, retry 3x) → Merge Audit (لا Deal، لا تنبيه)
                  false → Create Contact (POST, بلا retry — انظر سياسة الـ Retry)
                          → Validate → Create Failed? → true: DLQ + تنبيه فشل
                          → Create Deal (POST, بلا retry)
                          → Validate → Deal Failed? → true: DLQ (يحتفظ بـ contact_id)
                          → Associate Deal to Contact (PUT)
                          → Audit Log → Alert Sales (Telegram)
```
**تغطية الفشل:** كل خطوة HubSpot لها IF Error خاص. فشل البحث لا يُعامل كـ "ليس مكرراً" — بل يذهب للـ DLQ. فشل Contact يوقف إنشاء Deal. فشل Deal لا يعيد إنشاء Contact (الـ DLQ يحتفظ بـ `contact_id`، والاسترداد ينشئ الـ Deal فقط).

### 2. SLA — `sla-workflow.json` (13 عقدة)
```
Schedule Trigger كل ساعة
  → Search Deals بحالة New (stage = appointmentscheduled)
  → Validate Deal Search → Search Failed? → سجل تدقيق (search_failed)
  → SLA Due? (Code): Hot بعد 24 ساعة، Warm بعد 48 ساعة، يتخطى reminder_sent=true،
    وأي Deal خرج من مرحلة New يستبعده البحث نفسه قبل وصوله إلى Due (حالة الرد = انتقال الـ Deal من New)
  → Mark reminder_sent=true (PATCH) أولاً → Validate Mark → Mark Failed? → سجل (mark_failed)
  → Prepare SLA Alert → Telegram → Check Telegram Result
  → Prepare SLA Audit Row → Audit Log (يسجل telegram_failed عند فشل التنبيه)
```
الترتيب مقصود: **التحديث قبل التنبيه**، فلو تعطل التنفيذ بعد التحديث لن يُرسل التذكير مرة ثانية. إعادة التشغيل لا تنشئ Deals مكررة لأن الـ SLA لا ينشئ Deals أصلاً.

**الـ Stage من مصدر واحد (الجولة 4):** الـ Pipeline والـ Stage يُعرَّفان في مكان واحد فقط — الثابتان `PIPELINE_ID` و`STAGE_NEW_ID` في `builders_v3_extra.py` — وكل أجسام إنشاء الـ Deal (الرئيسي والاسترداد) وفلتر بحث الـ SLA يُبنون منهما، فلا يمكن أن يختلفا. **كود `SLA Due?` لا يفحص `dealstage` أصلاً:** فلتر البحث (`dealstage EQ STAGE_NEW_ID`) هو نقطة التنفيذ الوحيدة للمرحلة؛ فحص ثانٍ مشفر في Due كان يمكن أن ينحرف عن البحث بعد تغيير المرحلة في HubSpot فيقتل كل التذكيرات بصمت. تغيير المرحلة في HubSpot لا يكسر التذكيرات ما دام البحث وأجسام الإنشاء يشيران للمرحلة الجديدة — وهذا مثبت: T11 يستبعد "Hot Replied" (خرج من New) بالبحث وحده.

### 3. Error Handler — `error-workflow.json` (5 عقد)
Error Trigger → Extract Error Info → DLQ + تنبيه. **قيد موثَّق بصراحة:** مخرجات Error Trigger لا تحتوي حمولة الليد الأصلية؛ الـ Workflow يسجل رقم التنفيذ واسم الـ Workflow والخطأ، والليد يُسترد من التنفيذ المحفوظ (Save Failed Executions = true في الـ Workflow الرئيسي). يُعيَّن كـ Error Workflow في إعدادات الـ Workflow الرئيسي بعد الاستيراد.

### 4. DLQ Recovery — `dlq-recovery-workflow.json` (25 عقدة)
```
Manual Trigger → Read DLQ Rows → Filter Pending Rows (status=pending و retry_count<3)
  → Has Contact Already? (IF)
      true  → Prepare Deal → Create Deal (Deal فقط، بلا Contact جديد)
      false → Search Contact → Merge Search Result → Validate → Search Failed? → Mark (retry+1)
              → Is Duplicate? → Update (PATCH) أو Create (POST) → Merge → Validate
              → Contact Failed? → Mark (retry+1)
              → Prepare Deal → Create Deal → Merge Deal Result → Validate
              → Deal Failed? → Mark (retry+1)
              → Associate Deal to Contact (PUT) → Merge Association Result → Validate
              → Mark Row Recovered / Retry → Update DLQ Row (appendOrUpdate حسب lead_id، status=recovered أو pending)
```
- **إعادة فحص الـ Dedup قبل أي إنشاء**، فلا يمكن لإعادة المحاولة أن تنشئ Contact/Deal مكرراً.
- **صف به contact_id يتخطى إنشاء Contact** (استرداد النجاح الجزئي).
- **الـ Deal المسترد يُربط بالـ Contact دائماً** (PUT، مثل الـ Workflow الرئيسي). فشل الربط **غير قاتل**: الـ Deal موجود، فيُسجَّل الصف recovered مع ملاحظة الفشل في `error_message` — إعادة المحاولة عندها كانت ستنشئ Deal ثانياً.
- كل صف فاشل يبقى pending مع retry_count+1 (حد أقصى 3 محاولات).
- `created_at` الأصلي للصف يُحفَظ عند التحديث، و`recovered_at` يُملأ فقط عند نجاح الاسترداد.

## حقائق مثبتة في n8n 2.48.7 (بتنفيذ فعلي، لا تخمين)
- **Code node:** الاسم العاري `json` **غير معرَّف**؛ `$json` و`$input` معرَّفان. كل أكواد الـ Workflow تستخدم `$input` فقط.
- **`pairedItem` خاصية وليست دالة** (`$input.first().pairedItem` كائن `{item: N}`، واستدعاؤها كدالة يفشل).
- **IF يعيد تأسيس pairedItem على فهرس الفرع:** بعد انقسام IF، الفهرس يشير لموضع العنصر داخل مخرجات الفرع، لا داخل العقدة الأصلية. لذلك أي بحث `$('Node').all()[paired.item]` عبر فروع IF يعيد بيانات صف خاطئ في التدفقات متعددة العناصر (مثبت باختبار: صف "Search Fail" حصل على Deal، و"Mona" أُنشئ Contact لها 3 مرات). الحل في workflow الاسترداد: **عقد Merge (combine by position)** تعيد ربط كل استجابة HTTP بالليد الذي دخلها، والـ Validators تقرأ العنصر المدمج مباشرة من `$input` دون أي بحث عبر العقد.
- **HTTP Request v4.5 = مخطط V3:** أسماء معاملات V2 (`requestMethod`، `jsonParameters`، `bodyParametersJson`) **تُتجاهل بصمت** — الطلبات كانت تُرسل GET بلا جسم. الصحيح: `method` / `sendBody` / `contentType: "json"` / `specifyBody: "json"` / `jsonBody`، وخيارات الاستجابة تحت `options.response.response`.
- **jsonBody:** نص JSON عادي فيه `{{ }}` يُرسل حرفياً دون تقييم. التعبير الكامل `={{ JSON.stringify(...) }}` هو ما يُقيَّم.
- **محلل تعبيرات n8n يرفض الـ Object literals المتداخلة** (`{properties: {...}}` → خطأ `invalid syntax`، مثبت بكلاس `Expression` الخاص بـ n8n نفسه). لذلك أجسام HubSpot **تُسلسل مسبقاً في عقد Code** (JS حقيقي) وتُمرَّر كنص `={{ $json.<body> }}`. أجسام البحث سليمة لأن تداخلها عبر مصفوفات فقط.
- **Form Trigger v2.2:** الحقول تُقرأ **حسب الموضع** (`field-0`، `field-1`، …) لا حسب الاسم؛ الإرسال بأسماء الحقول يعطي قيم null كلها. الحقل الوحيد المطلوب هو `name` حتى يمكن إرسال ليدات Incomplete وتصنيفها داخل الـ Workflow.
- **عقد Code التي تستقبل عناصر متعددة يجب أن تحلق على `$input.all()`:** نسخة `$input.first()` كانت تُسقط كل العناصر بعد الأول بصمت (مثبت: تذكير SLA الثاني لم يُرسل).

## سياسة الـ Retry (مثبتة بالتجربة)
- **تُعاد المحاولة (3x، فاصل 5 ثوانٍ) فقط للعمليات idempotent:** البحث، PATCH، PUT.
- **لا إعادة محاولة لـ POST Create ولا لـ Append العادي:** إعادة تنفيذ عقدة n8n تعيد تشغيل **كل عناصر الدفعة**، فعنصر نجح يُعاد إنشاؤه (مثبت: عنصر واحد فاشل في دفعة جعل العقدة تعيد إنشاء Contact ناجح 3 مرات). آلية إعادة المحاولة لهذه العمليات هي workflow الاسترداد، مع إعادة فحص Dedup أولاً.
- **فشل HTTP 404 لا يظهر كـ error** في عقدة HTTP افتراضياً؛ الـ Validators تفحص `statusCode` أيضاً، لا `error` فقط.

## التسجيل (Scoring) وDedup وSLA — كما في `scope.md`
- Score ‏0–100: budget ‏+30 (≥3000) / ‏+10، product ‏+20، هاتف+إيميل ‏+20، نية ‏+20 قوية / ‏+10 ضعيفة، مصدر ‏+10. **Hot = ‏70+ و budget ≥3000 و intent ≥10، وإلا تُخفَّض إلى Warm.** Warm ‏40–69، Junk ‏<40، Incomplete = بلا هاتف أو بلا منتج (مراجعة فقط).
- Dedup: تطبيع الهاتف (+20/0020/0 ← 20) والإيميل (أحرف صغيرة). تطابق تام للهاتف أو الإيميل = مكرر؛ اسم مشابه بهاتف مختلف **ليس** مكرراً.
- SLA: تذكير Hot بعد 24 ساعة، Warm بعد 48 ساعة، بلا رد (الـ Deal ما زال في مرحلة New)، و`reminder_sent` يمنع التكرار.

## Telegram fallback — ليس تخزيناً دائماً
تنبيه Telegram إشعار بنسخة من الليد، وليس قاعدة بيانات. إن فشلت Sheets أيضاً، الليد يبقى في: (1) بيانات تنفيذ n8n المحفوظة، و(2) رسالة Telegram كنسخة قابلة للاسترجاع يدوياً. لذلك **لا نستخدم عبارة "Zero Leads Lost" كضمان مطلق** في أي ملف.

## الأعمدة (مطابقة JSON — الجولة 4)
- `Dead_Letter_Queue` — **16 عموداً**: lead_id, name, phone_normalized, email_normalized, product, budget_egp, band, score, stage_failed, error_message, contact_id, deal_id, status, retry_count, created_at, recovered_at. `recovered_at` يملؤه الاسترداد فقط عند النجاح؛ صفوف DLQ الجديدة تتركه فارغاً.
- `Audit_Log` — **11 عموداً**: lead_id, action, band, score, duplicate_of, contact_id, deal_id, association_failed, telegram_failed, created_at, threshold_hours. `threshold_hours` يملؤه SLA فقط (24/48)؛ صفوف التدقيق العادية تتركه فارغاً.
- كل عمود في تعليمات الإعداد موجود في مخطط JSON المقابل، وكل حقل يكتبه الـ Mark/Audit موجود في التاب. تحقق آلي: T0 في `run_n8n_tests.py` يقرأ `DLQ_COLUMNS` من الباني ويطابقها مع سطر الأعمدة في `setup-instructions.md`.

## نتائج الاختبار (في n8n 2.48.7 فعلياً، ضد Mock)
- `run_n8n_tests.py` (T0–T13): **58/58 ناجحة** — التفاصيل في `review-report.md` و`n8n-test-results.json`.
- اختبارات المنطق القديمة (محلية، قبل n8n): `test-logic.py` ‏20/20، `failure-matrix-test.py` ‏9/9، `sla-test.py` ‏4/4.
- ملفات JSON الأربعة الحقيقية **صُدِّرت من n8n نفسه** بعد الاستيراد (`exported-from-n8n/`)، وطابقت مخرجات الباني دون أي اختلاف في المعاملات أو الاتصالات.
