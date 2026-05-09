#!/usr/bin/env python3
"""
🎨 StickerBot v1.1.0 — بوت الستيكرات الذكي
"""
import asyncio, logging, os, sqlite3, json, time, hashlib
from datetime import datetime, date
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, PreCheckoutQueryHandler,
    filters, ContextTypes
)
import fal_client
import httpx

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════
BOT_TOKEN        = os.environ["BOT_TOKEN"]
FAL_KEY          = os.environ["FAL_KEY"]
BOT_USERNAME     = os.environ.get("BOT_USERNAME", "StickerBot")
PACK_PRICE_STARS = int(os.environ.get("PACK_PRICE_STARS", "75"))
FREE_DAILY_LIMIT = int(os.environ.get("FREE_DAILY_LIMIT", "2"))
RATE_LIMIT_SECS  = int(os.environ.get("RATE_LIMIT_SECONDS", "60"))
ADMIN_IDS        = [int(x) for x in os.environ.get("ADMIN_IDS","").split(",") if x.strip().isdigit()]
DB_PATH          = os.environ.get("DB_PATH", "data/sticker_bot.db")

os.environ["FAL_KEY"] = FAL_KEY

# ── الأساليب ──
STYLES = {
    "pixar":      ("🎬 Pixar 3D",   "Pixar 3D animation style, cute 3D rendered character, Disney Pixar"),
    "anime":      ("🌸 أنمي",       "anime style, Japanese animation, cute anime character, Studio Ghibli"),
    "watercolor": ("🎨 ووترکلر",   "watercolor painting style, soft artistic brushstrokes, colorful"),
    "cartoon":    ("😄 كاريكاتير", "cartoon style, bold outlines, exaggerated features, comic book"),
    "pixel":      ("👾 بكسل آرت",  "pixel art style, 8-bit retro game character, pixelated"),
}

# ── التعابير ──
EXPRESSIONS = [
    ("happy",       "😄 سعيد",     "happy smiling joyful expression"),
    ("angry",       "😠 غاضب",     "angry furious mad expression"),
    ("sad",         "😢 حزين",     "sad crying tearful expression"),
    ("surprised",   "😲 مندهش",    "surprised shocked amazed expression"),
    ("loving",      "😍 محب",      "loving heart eyes romantic expression"),
    ("sleeping",    "😴 نايم",     "sleeping tired peaceful zzz expression"),
    ("thinking",    "🤔 مفكر",     "thinking pondering curious expression"),
    ("celebrating", "🥳 يحتفل",   "celebrating party festive expression"),
]

# ══════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)

def get_db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_db():
    with get_db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            uid         INTEGER PRIMARY KEY,
            username    TEXT DEFAULT '',
            full_name   TEXT DEFAULT '',
            ref_code    TEXT UNIQUE,
            referred_by INTEGER,
            free_today  INTEGER DEFAULT 0,
            last_date   TEXT DEFAULT '',
            total_uses  INTEGER DEFAULT 0,
            bonus_uses  INTEGER DEFAULT 0,
            stars_spent INTEGER DEFAULT 0,
            joined_at   TEXT,
            last_seen   TEXT
        );
        CREATE TABLE IF NOT EXISTS packs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            uid        INTEGER,
            style      TEXT,
            file_ids   TEXT,
            created_at TEXT
        );
        """)
        c.commit()
    log.info(f"✅ DB: {DB_PATH}")

def save_user(uid, username, full_name):
    ref = hashlib.md5(str(uid).encode()).hexdigest()[:8]
    with get_db() as c:
        c.execute("""
            INSERT INTO users(uid,username,full_name,ref_code,joined_at,last_seen)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(uid) DO UPDATE SET
                username=excluded.username, full_name=excluded.full_name,
                last_seen=excluded.last_seen
        """, (uid, username or "", full_name or "", ref,
              datetime.now().isoformat(), datetime.now().isoformat()))
        c.commit()

def get_user(uid):
    with get_db() as c:
        r = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(r) if r else None

def get_user_by_ref(ref):
    with get_db() as c:
        r = c.execute("SELECT * FROM users WHERE ref_code=?", (ref,)).fetchone()
        return dict(r) if r else None

def check_free(uid) -> int:
    u = get_user(uid)
    if not u: return FREE_DAILY_LIMIT
    today = date.today().isoformat()
    if u["last_date"] != today:
        with get_db() as c:
            c.execute("UPDATE users SET free_today=0, last_date=? WHERE uid=?", (today, uid))
            c.commit()
        return FREE_DAILY_LIMIT + max(0, u.get("bonus_uses", 0))
    return max(0, FREE_DAILY_LIMIT - u.get("free_today", 0) + max(0, u.get("bonus_uses", 0)))

def use_free(uid):
    with get_db() as c:
        today = date.today().isoformat()
        c.execute("""UPDATE users SET
            free_today = CASE WHEN last_date=? THEN free_today+1 ELSE 1 END,
            last_date=?, total_uses=total_uses+1, last_seen=?
            WHERE uid=?""", (today, today, datetime.now().isoformat(), uid))
        c.commit()

def add_bonus(uid):
    with get_db() as c:
        c.execute("UPDATE users SET bonus_uses=bonus_uses+1 WHERE uid=?", (uid,))
        c.commit()

def set_referred(uid, referrer_uid):
    with get_db() as c:
        c.execute("UPDATE users SET referred_by=? WHERE uid=? AND referred_by IS NULL",
                  (referrer_uid, uid))
        c.commit()

def save_pack(uid, style, file_ids: list):
    with get_db() as c:
        c.execute("INSERT INTO packs(uid,style,file_ids,created_at) VALUES(?,?,?,?)",
                  (uid, style, json.dumps(file_ids), datetime.now().isoformat()))
        c.commit()

def get_packs(uid, limit=5):
    with get_db() as c:
        rows = c.execute("SELECT * FROM packs WHERE uid=? ORDER BY created_at DESC LIMIT ?",
                         (uid, limit)).fetchall()
        return [dict(r) for r in rows]

def record_payment(uid, stars):
    with get_db() as c:
        c.execute("""UPDATE users SET stars_spent=stars_spent+?,
                     total_uses=total_uses+1, last_seen=? WHERE uid=?""",
                  (stars, datetime.now().isoformat(), uid))
        c.commit()

def get_stats():
    with get_db() as c:
        return {
            "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "packs": c.execute("SELECT COUNT(*) FROM packs").fetchone()[0],
            "stars": c.execute("SELECT COALESCE(SUM(stars_spent),0) FROM users").fetchone()[0],
        }

# ══════════════════════════════════════════════════════════
#  RATE LIMITER
# ══════════════════════════════════════════════════════════
_rate: dict[int, float] = {}
_processing: set[int]   = set()

def rate_left(uid) -> int:
    if uid in ADMIN_IDS: return 0
    diff = time.time() - _rate.get(uid, 0)
    return max(0, int(RATE_LIMIT_SECS - diff))

def set_rate(uid): _rate[uid] = time.time()

# ══════════════════════════════════════════════════════════
#  FAL.AI — توليد الستيكرات
# ══════════════════════════════════════════════════════════
async def download_photo(bot, file_id: str) -> bytes:
    """تحميل الصورة من تيليغرام"""
    file = await bot.get_file(file_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(file.file_path)
        return r.content

async def upload_to_fal(photo_bytes: bytes) -> str | None:
    """رفع الصورة لـ fal.ai وإعادة الرابط العام"""
    try:
        url = await fal_client.upload_async(photo_bytes, "image/jpeg")
        log.info(f"✅ Uploaded to fal: {url[:50]}")
        return url
    except Exception as e:
        log.error(f"fal upload error: {e}")
        return None

async def gen_one_sticker(image_url: str, style_prompt: str, expr_prompt: str) -> str | None:
    """يولّد ستيكر واحد باستخدام fal-ai/face-to-sticker"""
    prompt = (
        f"{style_prompt}, {expr_prompt}, "
        "sticker art, white clean background, high quality, "
        "cute character design, clear face"
    )
    try:
        result = await fal_client.run_async(
            "fal-ai/face-to-sticker",
            arguments={
                "image_url":          image_url,
                "prompt":             prompt,
                "negative_prompt":    "nsfw, nude, violence, gore, ugly, blurry, realistic photo",
                "instant_id_strength": 0.7,
                "upscale":            False,
            }
        )
        # استخراج الرابط من النتيجة
        img = result.get("image") or {}
        if isinstance(result.get("images"), list) and result["images"]:
            img = result["images"][0]
        url = img.get("url", "")
        log.info(f"✅ Sticker generated: {url[:50]}")
        return url if url else None
    except Exception as e:
        log.error(f"gen_one_sticker: {e}")
        return None

async def generate_pack(image_url: str, style_key: str) -> list[str]:
    """يولّد 8 ستيكرات بالتوازي"""
    _, style_prompt = STYLES[style_key]
    tasks = [
        gen_one_sticker(image_url, style_prompt, expr)
        for _, _, expr in EXPRESSIONS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    urls = [r for r in results if isinstance(r, str) and r]
    log.info(f"Generated {len(urls)}/8 stickers")
    return urls

# ══════════════════════════════════════════════════════════
#  QUEUE WORKER
# ══════════════════════════════════════════════════════════
_queue: asyncio.Queue = asyncio.Queue()

async def queue_worker(bot):
    log.info("🔄 Queue worker started")
    while True:
        try:
            job = await asyncio.wait_for(_queue.get(), timeout=5.0)
            uid       = job["uid"]
            chat_id   = job["chat_id"]
            photo_bytes = job["photo_bytes"]
            style_key = job["style_key"]
            is_paid   = job.get("is_paid", False)
            _processing.add(uid)
            try:
                await bot.send_message(
                    chat_id,
                    f"⏳ *جاري توليد ستيكراتك...*\n"
                    f"الأسلوب: {STYLES[style_key][0]}\n"
                    f"_انتظر 30-60 ثانية_ 🎨",
                    parse_mode="Markdown"
                )
                # رفع الصورة لـ fal.ai
                image_url = await upload_to_fal(photo_bytes)
                if not image_url:
                    await bot.send_message(chat_id, "❌ فشل رفع الصورة، حاول مرة أخرى")
                    continue

                # توليد الستيكرات
                urls = await generate_pack(image_url, style_key)

                if not urls:
                    await bot.send_message(
                        chat_id,
                        "❌ فشل التوليد\n"
                        "تأكد أن الصورة تظهر فيها وجهك بوضوح وحاول مرة أخرى"
                    )
                    continue

                # إرسال الستيكرات
                await bot.send_message(
                    chat_id,
                    f"✅ *حزمة ستيكراتك جاهزة!*\n"
                    f"{STYLES[style_key][0]} — {len(urls)} ستيكر 🎉",
                    parse_mode="Markdown"
                )

                sent_ids = []
                for url in urls:
                    try:
                        msg = await bot.send_sticker(chat_id, sticker=url)
                        sent_ids.append(msg.sticker.file_id)
                        await asyncio.sleep(0.4)
                    except Exception as e:
                        log.warning(f"send sticker: {e}")
                        # إرسال كصورة عادية إذا فشل الإرسال كستيكر
                        try:
                            await bot.send_photo(chat_id, photo=url)
                            sent_ids.append(url)
                        except Exception: pass

                if sent_ids:
                    save_pack(uid, style_key, sent_ids)
                    if is_paid:
                        record_payment(uid, PACK_PRICE_STARS)
                    else:
                        use_free(uid)

                    # مكافأة المُحيل عند أول استخدام
                    u = get_user(uid)
                    if u and u.get("referred_by") and u.get("total_uses", 0) == 1:
                        add_bonus(u["referred_by"])
                        try:
                            await bot.send_message(
                                u["referred_by"],
                                "🎁 *مكافأة!* صديقك استخدم البوت\n"
                                "حصلت على استخدام مجاني إضافي 🎉",
                                parse_mode="Markdown"
                            )
                        except Exception: pass

                # أزرار المتابعة
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 ستايل آخر",        callback_data="newpack"),
                    InlineKeyboardButton("📦 ستيكراتي", callback_data="mystickers"),
                ]])
                await bot.send_message(chat_id, "هل تريد المزيد؟ 👇", reply_markup=kb)

            finally:
                _processing.discard(uid)
                _queue.task_done()

        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            log.info("🛑 Queue worker stopped")
            break
        except Exception as e:
            log.error(f"queue_worker: {e}")
            await asyncio.sleep(1)

# ══════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════
def kb_styles():
    keys = list(STYLES.keys())
    rows = []
    for i in range(0, len(keys), 2):
        row = [InlineKeyboardButton(STYLES[k][0], callback_data=f"style_{k}") for k in keys[i:i+2]]
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 إنشاء ستيكرات", callback_data="guide")],
        [InlineKeyboardButton("📦 ستيكراتي",       callback_data="mystickers"),
         InlineKeyboardButton("🔗 دعوة أصدقاء",   callback_data="invite")],
    ])

# ══════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)
    if context.args:
        referrer = get_user_by_ref(context.args[0])
        if referrer and referrer["uid"] != u.id:
            set_referred(u.id, referrer["uid"])
    free = check_free(u.id)
    sep  = "─" * 16
    await update.message.reply_text(
        f"{sep}\n🎨 *أهلاً {u.first_name}!*\n"
        f"بوت الستيكرات الذكي 🤖\n{sep}\n"
        f"أرسل *صورة وجهك* وحوّلها إلى\n"
        f"8 ستيكرات كرتونية! 🎭\n\n"
        f"🆓 لديك اليوم: *{free} استخدام مجاني*\n"
        f"⭐ أو ادفع *{PACK_PRICE_STARS} Stars* للمزيد\n"
        f"{sep}\n📸 ابدأ بإرسال صورة وجهك الآن!",
        parse_mode="Markdown", reply_markup=kb_main()
    )

async def cmd_mystickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    packs = get_packs(uid)
    if not packs:
        await update.message.reply_text(
            "📭 *لا توجد ستيكرات بعد*\n\n"
            "أرسل صورة وجهك لإنشاء أول حزمة! 📸",
            parse_mode="Markdown"
        ); return
    await update.message.reply_text(f"📦 *آخر {len(packs)} حزمة:*", parse_mode="Markdown")
    for pack in packs:
        ids  = json.loads(pack["file_ids"])
        lbl  = STYLES.get(pack["style"], (pack["style"],))[0]
        dt   = pack["created_at"][:10]
        await update.message.reply_text(f"🎨 {lbl}  •  {dt}  •  {len(ids)} ستيكر")
        for fid in ids[:4]:
            try: await update.message.reply_sticker(fid); await asyncio.sleep(0.3)
            except Exception: pass

async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u    = update.effective_user
    user = get_user(u.id) or (save_user(u.id, u.username, u.full_name) or get_user(u.id))
    link = f"https://t.me/{BOT_USERNAME}?start={user['ref_code']}"
    await update.message.reply_text(
        "🔗 *رابط دعوتك الخاص:*\n\n"
        f"`{link}`\n\n"
        "📋 شارك الرابط مع أصدقائك\n"
        "🎁 كل صديق يستخدم البوت = *استخدام مجاني إضافي لك!*",
        parse_mode="Markdown"
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    s = get_stats()
    await update.message.reply_text(
        f"📊 *إحصائيات StickerBot*\n━━━━━━━━━━━━━━\n"
        f"👥 المستخدمون: *{s['users']:,}*\n"
        f"📦 الحزم المولّدة: *{s['packs']:,}*\n"
        f"⭐ Stars: *{s['stars']:,}*\n"
        f"💰 الإيراد: *~${s['stars']*0.013:.2f}*",
        parse_mode="Markdown"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)

    wait = rate_left(u.id)
    if wait:
        await update.message.reply_text(
            f"⏱ *انتظر {wait} ثانية* قبل الطلب التالي",
            parse_mode="Markdown"
        ); return

    if u.id in _processing:
        await update.message.reply_text(
            "⏳ طلبك السابق لا يزال قيد المعالجة، انتظر قليلاً"
        ); return

    # تحميل الصورة من تيليغرام فوراً
    try:
        photo = update.message.photo[-1]
        photo_bytes = await download_photo(context.bot, photo.file_id)
        context.user_data["photo_bytes"] = photo_bytes
        set_rate(u.id)
    except Exception as e:
        log.error(f"download photo: {e}")
        await update.message.reply_text("❌ فشل تحميل الصورة، حاول مرة أخرى"); return

    free = check_free(u.id)
    note = (f"🆓 لديك *{free} استخدام مجاني* متبقٍ اليوم"
            if free > 0 else f"⭐ ستحتاج *{PACK_PRICE_STARS} Stars* للمتابعة")

    await update.message.reply_text(
        f"✅ *تم استلام الصورة!*\n──────────────────\n"
        f"اختر الأسلوب الكرتوني المفضل 🎨\n\n{note}",
        parse_mode="Markdown",
        reply_markup=kb_styles()
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()

    data = q.data

    if data.startswith("style_"):
        style_key   = data[6:]
        photo_bytes = context.user_data.get("photo_bytes")
        if not photo_bytes:
            await q.edit_message_text(
                "❌ لم أجد صورتك\nأرسل صورة جديدة 📸"
            ); return

        free = check_free(uid)
        if free > 0:
            await q.edit_message_text(
                f"✅ *{STYLES[style_key][0]}*\n"
                f"تمت إضافتك لقائمة الانتظار ⏳\n"
                f"سيصلك الرد خلال دقيقة 🎨",
                parse_mode="Markdown"
            )
            await _queue.put({
                "uid":         uid,
                "chat_id":     q.message.chat_id,
                "photo_bytes": photo_bytes,
                "style_key":   style_key,
                "is_paid":     False,
            })
            context.user_data.pop("photo_bytes", None)
        else:
            context.user_data["pending_style"] = style_key
            await q.edit_message_text(
                f"⭐ *الدفع مطلوب*\n──────────────────\n"
                f"الأسلوب: {STYLES[style_key][0]}\n"
                f"السعر: *{PACK_PRICE_STARS} Telegram Stars*\n"
                f"المحتوى: 8 ستيكرات كرتونية 🎨",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"⭐ ادفع {PACK_PRICE_STARS} Stars",
                                         callback_data=f"pay_{style_key}")
                ]])
            )
        return

    if data.startswith("pay_"):
        style_key = data[4:]
        await context.bot.send_invoice(
            chat_id=uid,
            title="🎨 حزمة ستيكرات كرتونية",
            description=f"8 ستيكرات بأسلوب {STYLES[style_key][0]}",
            payload=f"stickers_{uid}_{style_key}",
            currency="XTR",
            prices=[LabeledPrice("حزمة ستيكرات", PACK_PRICE_STARS)],
        )
        return

    if data == "mystickers":
        packs = get_packs(uid)
        if not packs:
            await q.edit_message_text("📭 لا توجد ستيكرات بعد\nأرسل صورة للبدء! 📸"); return
        await q.edit_message_text(f"📦 لديك {len(packs)} حزمة\nاستخدم /mystickers للعرض")
        return

    if data == "invite":
        user = get_user(uid)
        if not user: return
        link = f"https://t.me/{BOT_USERNAME}?start={user['ref_code']}"
        await q.edit_message_text(
            f"🔗 *رابط دعوتك:*\n\n`{link}`\n\n"
            "🎁 كل صديق = استخدام مجاني لك!",
            parse_mode="Markdown"
        )
        return

    if data == "guide":
        await q.edit_message_text(
            "📸 *كيفية الاستخدام:*\n──────────────────\n"
            "١️⃣ أرسل *سيلفي واضح* لوجهك\n"
            "٢️⃣ اختر *الأسلوب الكرتوني*\n"
            "٣️⃣ انتظر *30-60 ثانية*\n"
            "٤️⃣ احصل على *8 ستيكرات* 🎉\n\n"
            "📷 أرسل صورتك الآن 👇",
            parse_mode="Markdown"
        )
        return

    if data == "newpack":
        context.user_data.pop("photo_bytes", None)
        await q.edit_message_text("📸 أرسل صورة جديدة لإنشاء حزمة ستيكرات!")
        return

async def handle_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid       = update.effective_user.id
    payload   = update.message.successful_payment.invoice_payload
    style_key = payload.split("_")[-1]
    photo_bytes = context.user_data.get("photo_bytes")

    if not photo_bytes:
        await update.message.reply_text(
            "✅ تم الدفع!\n"
            "⚠️ أرسل صورتك مجدداً وسنولّد الستيكرات فوراً"
        ); return

    await update.message.reply_text("✅ *تم الدفع بنجاح!* ⭐\nجاري المعالجة...", parse_mode="Markdown")
    await _queue.put({
        "uid":         uid,
        "chat_id":     update.message.chat_id,
        "photo_bytes": photo_bytes,
        "style_key":   style_key,
        "is_paid":     True,
    })
    context.user_data.pop("photo_bytes", None)

# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════
_worker_task = None

async def post_init(app: Application):
    global _worker_task
    init_db()
    _worker_task = app.create_task(queue_worker(app.bot), name="queue_worker")
    await app.bot.set_my_commands([
        ("start",      "🏠 القائمة الرئيسية"),
        ("mystickers", "📦 ستيكراتي السابقة"),
        ("invite",     "🔗 دعوة الأصدقاء"),
        ("stats",      "📊 إحصائيات"),
    ])
    log.info("✅ StickerBot v1.1.0 ready!")

async def post_shutdown(app: Application):
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try: await _worker_task
        except asyncio.CancelledError: pass
    log.info("👋 Bot stopped cleanly")

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("mystickers", cmd_mystickers))
    app.add_handler(CommandHandler("invite",     cmd_invite))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(PreCheckoutQueryHandler(handle_precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_payment))
    log.info("🚀 StickerBot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
