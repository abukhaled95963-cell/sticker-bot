#!/usr/bin/env python3
"""🎨 StickerBot v1.2.0"""
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
import httpx

load_dotenv()
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
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

FAL_HEADERS = {
    "Authorization": f"Key {FAL_KEY}",
    "Content-Type":  "application/json",
}

STYLES = {
    "pixar":      ("🎬 Pixar 3D",   "Pixar 3D animation style, cute Disney Pixar 3D character"),
    "anime":      ("🌸 أنمي",       "anime style, Japanese animation, cute anime character"),
    "watercolor": ("🎨 ووترکلر",   "watercolor painting style, soft artistic brushstrokes"),
    "cartoon":    ("😄 كاريكاتير", "cartoon style, bold outlines, exaggerated features"),
    "pixel":      ("👾 بكسل آرت",  "pixel art style, 8-bit retro game character"),
}

EXPRESSIONS = [
    ("happy",       "😄 سعيد",     "happy smiling joyful"),
    ("angry",       "😠 غاضب",     "angry furious mad"),
    ("sad",         "😢 حزين",     "sad crying tearful"),
    ("surprised",   "😲 مندهش",    "surprised shocked amazed"),
    ("loving",      "😍 محب",      "loving heart eyes"),
    ("sleeping",    "😴 نايم",     "sleeping tired zzz"),
    ("thinking",    "🤔 مفكر",     "thinking pondering"),
    ("celebrating", "🥳 يحتفل",   "celebrating party festive"),
]

# ══════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)

def db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            uid INTEGER PRIMARY KEY, username TEXT DEFAULT '',
            full_name TEXT DEFAULT '', ref_code TEXT UNIQUE,
            referred_by INTEGER, free_today INTEGER DEFAULT 0,
            last_date TEXT DEFAULT '', total_uses INTEGER DEFAULT 0,
            bonus_uses INTEGER DEFAULT 0, stars_spent INTEGER DEFAULT 0,
            joined_at TEXT, last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, style TEXT, file_ids TEXT, created_at TEXT
        );
        """)
        c.commit()
    log.info(f"✅ DB: {DB_PATH}")

def save_user(uid, username, full_name):
    ref = hashlib.md5(str(uid).encode()).hexdigest()[:8]
    with db() as c:
        c.execute("""INSERT INTO users(uid,username,full_name,ref_code,joined_at,last_seen)
            VALUES(?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET
            username=excluded.username,full_name=excluded.full_name,last_seen=excluded.last_seen""",
            (uid, username or "", full_name or "", ref,
             datetime.now().isoformat(), datetime.now().isoformat()))
        c.commit()

def get_user(uid):
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(r) if r else None

def get_by_ref(ref):
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE ref_code=?", (ref,)).fetchone()
        return dict(r) if r else None

def check_free(uid) -> int:
    u = get_user(uid)
    if not u: return FREE_DAILY_LIMIT
    today = date.today().isoformat()
    if u["last_date"] != today:
        with db() as c:
            c.execute("UPDATE users SET free_today=0,last_date=? WHERE uid=?", (today,uid))
            c.commit()
        return FREE_DAILY_LIMIT + max(0, u.get("bonus_uses",0))
    return max(0, FREE_DAILY_LIMIT - u.get("free_today",0) + max(0, u.get("bonus_uses",0)))

def use_free(uid):
    with db() as c:
        today = date.today().isoformat()
        c.execute("""UPDATE users SET
            free_today=CASE WHEN last_date=? THEN free_today+1 ELSE 1 END,
            last_date=?,total_uses=total_uses+1,last_seen=? WHERE uid=?""",
            (today,today,datetime.now().isoformat(),uid))
        c.commit()

def add_bonus(uid):
    with db() as c:
        c.execute("UPDATE users SET bonus_uses=bonus_uses+1 WHERE uid=?", (uid,))
        c.commit()

def set_referred(uid, ref_uid):
    with db() as c:
        c.execute("UPDATE users SET referred_by=? WHERE uid=? AND referred_by IS NULL",
                  (ref_uid, uid))
        c.commit()

def save_pack(uid, style, ids):
    with db() as c:
        c.execute("INSERT INTO packs(uid,style,file_ids,created_at) VALUES(?,?,?,?)",
                  (uid,style,json.dumps(ids),datetime.now().isoformat()))
        c.commit()

def get_packs(uid, n=5):
    with db() as c:
        rows = c.execute("SELECT * FROM packs WHERE uid=? ORDER BY created_at DESC LIMIT ?",
                         (uid,n)).fetchall()
        return [dict(r) for r in rows]

def record_pay(uid, stars):
    with db() as c:
        c.execute("""UPDATE users SET stars_spent=stars_spent+?,
            total_uses=total_uses+1,last_seen=? WHERE uid=?""",
            (stars,datetime.now().isoformat(),uid))
        c.commit()

def get_stats():
    with db() as c:
        return {
            "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "packs": c.execute("SELECT COUNT(*) FROM packs").fetchone()[0],
            "stars": c.execute("SELECT COALESCE(SUM(stars_spent),0) FROM users").fetchone()[0],
        }

# ══════════════════════════════════════════════════════════
#  RATE LIMITER
# ══════════════════════════════════════════════════════════
_rate: dict = {}
_busy: set = set()

def rate_left(uid) -> int:
    if uid in ADMIN_IDS: return 0
    return max(0, int(RATE_LIMIT_SECS - (time.time() - _rate.get(uid, 0))))

def set_rate(uid): _rate[uid] = time.time()

# ══════════════════════════════════════════════════════════
#  FAL.AI — بدون fal-client (httpx مباشرة أكثر استقراراً)
# ══════════════════════════════════════════════════════════
async def upload_photo_to_fal(photo_bytes: bytes) -> str:
    """يرفع الصورة لـ fal.ai storage ويعيد رابطاً عاماً"""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://rest.alpha.fal.ai/storage/upload/initiate",
                headers={"Authorization": f"Key {FAL_KEY}"},
                json={"content_type": "image/jpeg", "file_name": "photo.jpg"}
            )
            if r.status_code != 200:
                log.error(f"fal initiate upload: {r.status_code} {r.text[:100]}")
                return None
            data      = r.json()
            upload_url= data.get("upload_url")
            file_url  = data.get("file_url")
            if not upload_url:
                log.error("fal: no upload_url in response")
                return None
            # رفع الملف الفعلي
            ur = await client.put(
                upload_url,
                content=photo_bytes,
                headers={"Content-Type": "image/jpeg"},
                timeout=60
            )
            if ur.status_code in (200, 201, 204):
                log.info(f"✅ Photo uploaded: {file_url}")
                return file_url
            log.error(f"fal put: {ur.status_code}")
            return None
    except Exception as e:
        log.error(f"upload_photo_to_fal: {e}")
        return None

async def gen_sticker(image_url: str, style_prompt: str, expr: str) -> str:
    """يولّد ستيكر واحد عبر fal-ai/face-to-sticker"""
    prompt = (
        f"{style_prompt}, {expr}, "
        "sticker art, white background, high quality cute character"
    )
    payload = {
        "image_url":           image_url,
        "prompt":              prompt,
        "negative_prompt":     "nsfw, nude, violence, ugly, blurry, realistic photo",
        "instant_id_strength": 0.7,
        "upscale":             False,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # إرسال الطلب
            r = await client.post(
                "https://queue.fal.run/fal-ai/face-to-sticker",
                headers=FAL_HEADERS, json=payload
            )
            if r.status_code not in (200, 201):
                log.warning(f"fal submit: {r.status_code}")
                return None
            req_id = r.json().get("request_id")
            if not req_id:
                return None

        # انتظر النتيجة
        status_url = f"https://queue.fal.run/fal-ai/face-to-sticker/requests/{req_id}/status"
        result_url = f"https://queue.fal.run/fal-ai/face-to-sticker/requests/{req_id}"

        async with httpx.AsyncClient(timeout=10) as client:
            for _ in range(40):  # max 120 ثانية
                await asyncio.sleep(3)
                sr = await client.get(status_url, headers=FAL_HEADERS)
                status = sr.json().get("status","")
                if status == "COMPLETED":
                    rr = await client.get(result_url, headers=FAL_HEADERS)
                    rd = rr.json()
                    img = rd.get("image") or {}
                    if isinstance(rd.get("images"), list) and rd["images"]:
                        img = rd["images"][0]
                    url = img.get("url","")
                    log.info(f"✅ Sticker: {url[:50]}")
                    return url or None
                elif status == "FAILED":
                    log.warning(f"fal FAILED: {sr.json()}")
                    return None
    except Exception as e:
        log.error(f"gen_sticker: {e}")
    return None

async def gen_pack(image_url: str, style_key: str) -> list[str]:
    """يولّد 8 ستيكرات بالتوازي"""
    _, style_prompt = STYLES[style_key]
    tasks = [gen_sticker(image_url, style_prompt, expr) for _,_,expr in EXPRESSIONS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    urls = [r for r in results if isinstance(r, str) and r]
    log.info(f"Generated {len(urls)}/8 stickers for {style_key}")
    return urls

# ══════════════════════════════════════════════════════════
#  QUEUE
# ══════════════════════════════════════════════════════════
_q: asyncio.Queue = asyncio.Queue()

async def worker(bot):
    log.info("🔄 Worker started")
    while True:
        try:
            job = await asyncio.wait_for(_q.get(), timeout=5.0)
            uid, chat_id = job["uid"], job["chat_id"]
            photo_bytes  = job["photo_bytes"]
            style_key    = job["style_key"]
            is_paid      = job.get("is_paid", False)
            _busy.add(uid)
            try:
                await bot.send_message(chat_id,
                    f"⏳ *جاري توليد ستيكراتك...*\n"
                    f"الأسلوب: {STYLES[style_key][0]}\n"
                    "_انتظر 30-90 ثانية_ 🎨", parse_mode="Markdown")

                # رفع الصورة
                img_url = await upload_photo_to_fal(photo_bytes)
                if not img_url:
                    await bot.send_message(chat_id,
                        "❌ فشل رفع الصورة\nتأكد أن الصورة واضحة وحاول مجدداً")
                    continue

                # توليد الستيكرات
                urls = await gen_pack(img_url, style_key)
                if not urls:
                    await bot.send_message(chat_id,
                        "❌ فشل التوليد\n"
                        "تأكد أن وجهك يظهر بوضوح في الصورة وحاول مجدداً 📸")
                    continue

                await bot.send_message(chat_id,
                    f"✅ *حزمة ستيكراتك جاهزة!*\n"
                    f"{STYLES[style_key][0]} — {len(urls)} ستيكر 🎉",
                    parse_mode="Markdown")

                sent = []
                for url in urls:
                    try:
                        m = await bot.send_sticker(chat_id, sticker=url)
                        sent.append(m.sticker.file_id)
                        await asyncio.sleep(0.4)
                    except Exception:
                        try:
                            await bot.send_photo(chat_id, photo=url)
                            sent.append(url)
                        except Exception: pass

                if sent:
                    save_pack(uid, style_key, sent)
                    if is_paid: record_pay(uid, PACK_PRICE_STARS)
                    else: use_free(uid)

                    u = get_user(uid)
                    if u and u.get("referred_by") and u.get("total_uses",0) == 1:
                        add_bonus(u["referred_by"])
                        try:
                            await bot.send_message(u["referred_by"],
                                "🎁 *مكافأة!* صديقك استخدم البوت\n"
                                "حصلت على استخدام مجاني إضافي 🎉", parse_mode="Markdown")
                        except Exception: pass

                await bot.send_message(chat_id, "هل تريد المزيد؟ 👇",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔄 ستايل آخر", callback_data="newpack"),
                        InlineKeyboardButton("📦 ستيكراتي", callback_data="mystickers"),
                    ]]))
            finally:
                _busy.discard(uid)
                _q.task_done()
        except asyncio.TimeoutError: continue
        except asyncio.CancelledError: log.info("🛑 Worker stopped"); break
        except Exception as e: log.error(f"worker: {e}"); await asyncio.sleep(1)

# ══════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════
def kb_styles():
    keys = list(STYLES.keys())
    rows = []
    for i in range(0,len(keys),2):
        rows.append([InlineKeyboardButton(STYLES[k][0],callback_data=f"style_{k}") for k in keys[i:i+2]])
    return InlineKeyboardMarkup(rows)

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 إنشاء ستيكرات", callback_data="guide")],
        [InlineKeyboardButton("📦 ستيكراتي", callback_data="mystickers"),
         InlineKeyboardButton("🔗 دعوة أصدقاء", callback_data="invite")],
    ])

# ══════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)
    if context.args:
        ref = get_by_ref(context.args[0])
        if ref and ref["uid"] != u.id: set_referred(u.id, ref["uid"])
    free = check_free(u.id)
    await update.message.reply_text(
        f"{'─'*16}\n🎨 *أهلاً {u.first_name}!*\n"
        f"بوت الستيكرات الذكي 🤖\n{'─'*16}\n"
        f"أرسل *صورة وجهك* وحوّلها إلى\n8 ستيكرات كرتونية! 🎭\n\n"
        f"🆓 لديك اليوم: *{free} استخدام مجاني*\n"
        f"⭐ أو ادفع *{PACK_PRICE_STARS} Stars* للمزيد\n"
        f"{'─'*16}\n📸 أرسل صورة وجهك للبدء!",
        parse_mode="Markdown", reply_markup=kb_main())

async def cmd_mystickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    packs = get_packs(uid)
    if not packs:
        await update.message.reply_text("📭 *لا توجد ستيكرات بعد*\nأرسل صورة للبدء! 📸",
                                        parse_mode="Markdown"); return
    await update.message.reply_text(f"📦 *آخر {len(packs)} حزمة:*", parse_mode="Markdown")
    for p in packs:
        ids = json.loads(p["file_ids"])
        lbl = STYLES.get(p["style"],(p["style"],))[0]
        await update.message.reply_text(f"🎨 {lbl}  •  {p['created_at'][:10]}  •  {len(ids)} ستيكر")
        for fid in ids[:4]:
            try: await update.message.reply_sticker(fid); await asyncio.sleep(0.3)
            except Exception: pass

async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)
    user = get_user(u.id)
    link = f"https://t.me/{BOT_USERNAME}?start={user['ref_code']}"
    await update.message.reply_text(
        f"🔗 *رابط دعوتك:*\n\n`{link}`\n\n"
        "🎁 كل صديق يستخدم البوت = *استخدام مجاني إضافي لك!*",
        parse_mode="Markdown")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    s = get_stats()
    await update.message.reply_text(
        f"📊 *StickerBot Stats*\n━━━━━━━━━━━━━━\n"
        f"👥 المستخدمون: *{s['users']:,}*\n"
        f"📦 الحزم: *{s['packs']:,}*\n"
        f"⭐ Stars: *{s['stars']:,}*\n"
        f"💰 الإيراد: *~${s['stars']*0.013:.2f}*", parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)
    wait = rate_left(u.id)
    if wait:
        await update.message.reply_text(f"⏱ *انتظر {wait} ثانية*", parse_mode="Markdown"); return
    if u.id in _busy:
        await update.message.reply_text("⏳ طلبك السابق لا يزال قيد المعالجة"); return
    try:
        photo = update.message.photo[-1]
        file  = await context.bot.get_file(photo.file_id)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(file.file_path)
        context.user_data["photo_bytes"] = r.content
        set_rate(u.id)
    except Exception as e:
        log.error(f"download: {e}")
        await update.message.reply_text("❌ فشل تحميل الصورة، حاول مجدداً"); return

    free = check_free(u.id)
    note = (f"🆓 لديك *{free} استخدام مجاني*" if free>0
            else f"⭐ ستحتاج *{PACK_PRICE_STARS} Stars*")
    await update.message.reply_text(
        f"✅ *تم استلام الصورة!*\n──────────────────\n"
        f"اختر الأسلوب الكرتوني 🎨\n\n{note}",
        parse_mode="Markdown", reply_markup=kb_styles())

async def handle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()
    d   = q.data

    if d.startswith("style_"):
        sk   = d[6:]
        pb   = context.user_data.get("photo_bytes")
        if not pb:
            await q.edit_message_text("❌ أرسل صورة جديدة أولاً 📸"); return
        free = check_free(uid)
        if free > 0:
            await q.edit_message_text(
                f"✅ *{STYLES[sk][0]}*\nتمت الإضافة لقائمة الانتظار ⏳",
                parse_mode="Markdown")
            await _q.put({"uid":uid,"chat_id":q.message.chat_id,
                          "photo_bytes":pb,"style_key":sk,"is_paid":False})
            context.user_data.pop("photo_bytes",None)
        else:
            context.user_data["pending_style"] = sk
            await q.edit_message_text(
                f"⭐ *الدفع مطلوب*\n──────────────────\n"
                f"الأسلوب: {STYLES[sk][0]}\n"
                f"السعر: *{PACK_PRICE_STARS} Stars*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"⭐ ادفع {PACK_PRICE_STARS} Stars",
                                         callback_data=f"pay_{sk}")
                ]]))
        return

    if d.startswith("pay_"):
        sk = d[4:]
        await context.bot.send_invoice(chat_id=uid,
            title="🎨 حزمة ستيكرات كرتونية",
            description=f"8 ستيكرات بأسلوب {STYLES[sk][0]}",
            payload=f"stickers_{uid}_{sk}", currency="XTR",
            prices=[LabeledPrice("حزمة ستيكرات", PACK_PRICE_STARS)])
        return

    if d == "mystickers":
        packs = get_packs(uid)
        await q.edit_message_text(
            "📭 لا ستيكرات بعد — أرسل صورة!" if not packs
            else f"📦 لديك {len(packs)} حزمة\nاستخدم /mystickers للعرض")
        return

    if d == "invite":
        user = get_user(uid)
        if not user: return
        link = f"https://t.me/{BOT_USERNAME}?start={user['ref_code']}"
        await q.edit_message_text(
            f"🔗 *رابط دعوتك:*\n\n`{link}`\n\n🎁 كل صديق = استخدام مجاني!",
            parse_mode="Markdown")
        return

    if d == "guide":
        await q.edit_message_text(
            "📸 *كيفية الاستخدام:*\n──────────────────\n"
            "١️⃣ أرسل *سيلفي واضح* لوجهك\n"
            "٢️⃣ اختر *الأسلوب الكرتوني*\n"
            "٣️⃣ انتظر *30-90 ثانية*\n"
            "٤️⃣ احصل على *8 ستيكرات* 🎉\n\n"
            "📷 أرسل صورتك الآن 👇", parse_mode="Markdown")
        return

    if d == "newpack":
        context.user_data.pop("photo_bytes",None)
        await q.edit_message_text("📸 أرسل صورة جديدة لإنشاء حزمة ستيكرات!")
        return

async def handle_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    pl   = update.message.successful_payment.invoice_payload
    sk   = pl.split("_")[-1]
    pb   = context.user_data.get("photo_bytes")
    if not pb:
        await update.message.reply_text("✅ تم الدفع!\n⚠️ أرسل صورتك مجدداً للمتابعة"); return
    await update.message.reply_text("✅ *تم الدفع!* ⭐\nجاري المعالجة...", parse_mode="Markdown")
    await _q.put({"uid":uid,"chat_id":update.message.chat_id,
                  "photo_bytes":pb,"style_key":sk,"is_paid":True})
    context.user_data.pop("photo_bytes",None)

# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════
_wt = None

async def post_init(app):
    global _wt
    init_db()
    _wt = app.create_task(worker(app.bot), name="worker")
    await app.bot.set_my_commands([
        ("start","🏠 القائمة الرئيسية"),
        ("mystickers","📦 ستيكراتي"),
        ("invite","🔗 دعوة أصدقاء"),
        ("stats","📊 إحصائيات"),
    ])
    log.info("✅ StickerBot v1.2.0 ready!")

async def post_shutdown(app):
    if _wt and not _wt.done():
        _wt.cancel()
        try: await _wt
        except asyncio.CancelledError: pass

def main():
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("mystickers", cmd_mystickers))
    app.add_handler(CommandHandler("invite",     cmd_invite))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_cb))
    app.add_handler(PreCheckoutQueryHandler(handle_precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_payment))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
