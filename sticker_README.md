# 🎨 StickerBot — بوت الستيكرات الذكي

بوت تيليغرام يحوّل صورة الوجه إلى 8 ستيكرات كرتونية بتعابير مختلفة.

## 🚀 الإعداد السريع

### 1. احصل على المفاتيح
| المفتاح | المصدر |
|---------|--------|
| `BOT_TOKEN` | @BotFather على تيليغرام |
| `FAL_KEY` | fal.ai/dashboard/keys |

### 2. فعّل Telegram Stars
```
@BotFather → /mybots → بوتك → Payments → Stars
```

### 3. النشر على Railway

1. ارفع المشروع على GitHub
2. افتح railway.app → New Project → Deploy from GitHub
3. أضف **Volume** ومسار `/data` لحفظ قاعدة البيانات
4. أضف المتغيرات من `.env.example`
5. Deploy ✅

## 💾 حفظ البيانات على Railway

في Railway → مشروعك → **Volumes** → Add:
- Mount Path: `/data`

ثم في Variables:
```
DB_PATH=/data/sticker_bot.db
```

## 💰 التكاليف التقديرية

| العنصر | التكلفة |
|--------|---------|
| توليد 8 ستيكرات (fal.ai) | ~$0.16 |
| سعر البيع (75 Stars) | ~$1.00 |
| الاستضافة (Railway) | $5/شهر |
| **هامش الربح** | **~84%** |

## 📋 أوامر المشرف
- `/stats` — إحصائيات البوت
