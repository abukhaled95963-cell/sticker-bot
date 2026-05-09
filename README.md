# 🎨 Sticker Bot — بوت الستيكرات الذكي

بوت تيليغرام يحوّل صور الوجه إلى ستيكرات كرتونية احترافية باستخدام الذكاء الاصطناعي.

## ✨ الميزات

- 🎭 **5 أساليب كرتونية** — Pixar، أنمي، ووترکلر، كاريكاتير، بكسل آرت
- 😄 **8 تعابير مختلفة** لكل حزمة — سعيد، غاضب، حزين، وأكثر
- 🛡️ **فلتر محتوى ثنائي الطبقة** — OpenAI + fal.ai built-in
- 💳 **دفع عبر Telegram Stars** مدمج مباشرة
- 🎁 **نظام إحالة** — شارك وادعُ أصدقاءك لكسب ستيكرات مجانية
- ⚡ **قائمة انتظار ذكية** — تتحمل آلاف الطلبات دون انهيار

---

## 🚀 خطوات الإعداد

### 1. الحصول على الـ API Keys

| المفتاح | من أين | التكلفة |
|---|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) على تيليغرام | مجاني |
| `FAL_KEY` | [fal.ai/dashboard](https://fal.ai/dashboard/keys) | $0.025/ستيكر |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) | مجاني (Moderation) |

### 2. تفعيل Telegram Stars على بوتك

في @BotFather:
```
/mybots → اختر البوت → Payments → Stars
```

### 3. إعداد ملف البيئة

```bash
cp .env.example .env
# افتح .env وأضف مفاتيحك
```

### 4. تشغيل محلي (للاختبار)

```bash
pip install -r requirements.txt
python main.py
```

---

## 🚂 النشر على Railway

### الطريقة الأسهل — من GitHub مباشرة:

1. **ارفع المشروع لـ GitHub:**
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/username/sticker-bot.git
git push -u origin main
```

2. **افتح [railway.app](https://railway.app) وسجّل دخول**

3. **New Project → Deploy from GitHub repo**
   - اختر مستودعك
   - Railway يكتشف `railway.toml` تلقائياً

4. **أضف المتغيرات البيئية:**
   في Railway Dashboard → Variables → أضف من `.env.example`

5. **Deploy** ✅ — Railway يشغّل البوت تلقائياً!

### إعادة النشر التلقائي:

```bash
# أي تعديل وتدفع على GitHub → Railway ينشر تلقائياً
git add .
git commit -m "Update feature"
git push
```

---

## 📁 هيكل المشروع

```
sticker-bot/
├── main.py                 # نقطة الدخول الرئيسية
├── railway.toml            # إعدادات Railway
├── Procfile                # أمر التشغيل
├── requirements.txt        # المكتبات
├── .env.example            # نموذج المتغيرات البيئية
├── .gitignore
│
├── bot/
│   ├── handlers.py         # معالجة الأوامر والصور
│   └── payment_handler.py  # معالجة الدفع بـ Telegram Stars
│
├── workers/
│   └── queue_worker.py     # معالج قائمة الانتظار
│
├── utils/
│   ├── config.py           # إعدادات المشروع
│   ├── database.py         # قاعدة البيانات SQLite
│   ├── fal_service.py      # خدمة توليد الستيكرات
│   ├── moderation.py       # فحص المحتوى
│   └── messages.py         # نصوص البوت (عربي)
│
└── data/                   # قاعدة بيانات محلية (gitignored)
```

---

## 💰 حساب التكاليف

| الحدث | التكلفة |
|---|---|
| حزمة 8 ستيكرات (fal.ai) | ~$0.20 |
| فحص محتوى (OpenAI) | $0.00 (مجاني) |
| استضافة Railway | $5/شهر |
| **سعر البيع** | **$2 (150 Stars)** |
| **هامش الربح** | **~90%** |

---

## 🔧 التوسعة المستقبلية

- [ ] Redis Queue (أضف Redis plugin على Railway)
- [ ] ستيكرات متحركة GIF
- [ ] إهداء الستيكرات
- [ ] حزمة الثنائي (صورتان)
- [ ] اشتراك شهري

---

## 📄 الترخيص

MIT License — استخدم حر
