#!/usr/bin/env python3
"""
🎨 StickerBot v1.0.0
بوت تيليغرام يحوّل صورة الوجه إلى ستيكرات كرتونية
"""
import asyncio, logging, os, sqlite3, json, time, hashlib
from datetime import datetime, date
from io import BytesIO
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, PreCheckoutQueryHandler,
    filters, ContextTypes
)
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

# ── الأساليب الكرتونية ──
STYLES = {
    "pixar":     ("🎬 Pixar 3D",    "Pixar 3D animation style, cute 3D character"),
    "anime":     ("🌸 أنمي",        "anime style, Japanese animation, cute"),
    "watercolor":("🎨 ووترکلر",    "watercolor painting style, soft brushstrokes"),
    "cartoon":   ("😄 كاريكاتير",  "cartoon style, bold outlines, exaggerated features"),
    "pixel":     ("👾 بكسل آرت",    "pixel art style, 8-bit, retro game character"),
}

# ── التعابير الثمانية ──
EXPRESSIONS = [
    ("happy",       "😄 سعيد",       "happy, smiling, joyful expression"),
    ("angry",       "😠 غاضب",       "angry, furious, mad expression"),
    ("sad",         "😢 حزين",       "sad, crying, tearful expression"),
    ("surprised",   "😲 مندهش",     "surprised, shocked, amazed expression"),
    ("loving",      "😍 محب",        "loving, heart eyes, romantic expression"),
    ("sleeping",    "😴 نايم",       "sleeping, tired, zzz expression"),
    ("thinking",    "🤔 مفكر",      "thinking, pondering, curious expression"),
    ("celebrating", "🥳 يحتفل",     "celebrating, party hat, festive expression"),
]

# ══════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            uid         INTEGER PRIMARY KEY,
            username    TEXT,
            full_name   TEXT,
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
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            uid         INTEGER,
            style       TEXT,
            file_ids    TEXT,
            thumb_id    TEXT,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS queue_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            uid         INTEGER,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT
        );
        """)
        c.commit()
    log.info(f"✅ DB ready: {DB_PATH}")

def save_user(uid, username, full_name):
    ref = hashlib.md5(str(uid).encode()).hexdigest()[:8]
    with get_db() as c:
        c.execute("""
            INSERT INTO users(uid,username,full_name,ref_code,joined_at,last_seen)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(uid) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                last_seen=excluded.last_seen
        """, (uid, username or "", full_name or "", ref,
              datetime.now().isoformat(), datetime.now().isoformat()))
        c.commit()

def get_user(uid) -> dict | None:
    with get_db() as c:
        r = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(r) if r else None

def get_user_by_ref(ref_code) -> dict | None:
    with get_db() as c:
        r = c.execute("SELECT * FROM users WHERE ref_code=?", (ref_code,)).fetchone()
        return dict(r) if r else None

def check_free_uses(uid) -> int:
    """يعيد عدد الاستخدامات المجانية المتبقية اليوم"""
    u = get_user(uid)
    if not u: return FREE_DAILY_LIMIT
    today = date.today().isoformat()
    if u["last_date"] != today:
        with get_db() as c:
            c.execute("UPDATE users SET free_today=0, last_date=? WHERE uid=?", (today, uid))
            c.commit()
        return FREE_DAILY_LIMIT + (u["bonus_uses"] or 0)
    remaining = FREE_DAILY_LIMIT - (u["free_today"] or 0) + (u["bonus_uses"] or 0)
    return max(0, remaining)

def use_free(uid):
    with get_db() as c:
        today = date.today().isoformat()
        c.execute("""
            UPDATE users SET
                free_today = CASE WHEN last_date=? THEN free_today+1 ELSE 1 END,
                last_date  = ?,
                total_uses = total_uses + 1,
                last_seen  = ?
            WHERE uid=?
        """, (today, today, datetime.now().isoformat(), uid))
        c.commit()

def add_bonus(uid):
    """يضيف استخدام مجاني مكافأة للمُحيل"""
    with get_db() as c:
        c.execute("UPDATE users SET bonus_uses = bonus_uses + 1 WHERE uid=?", (uid,))
        c.commit()

def add_referred_by(uid, referrer_uid):
    with get_db() as c:
        c.execute("UPDATE users SET referred_by=? WHERE uid=? AND referred_by IS NULL",
                  (referrer_uid, uid))
        c.commit()

def record_stars(uid, stars):
    with get_db() as c:
        c.execute("""
            UPDATE users SET
                stars_spent = stars_spent + ?,
                total_uses  = total_uses + 1,
                last_seen   = ?
            WHERE uid=?
        """, (stars, datetime.now().isoformat(), uid))
        c.commit()

def save_pack(uid, style, file_ids: list, thumb_id=""):
    with get_db() as c:
        c.execute("""
            INSERT INTO packs(uid,style,file_ids,thumb_id,created_at)
            VALUES(?,?,?,?,?)
        """, (uid, style, json.dumps(file_ids), thumb_id, datetime.now().isoformat()))
        c.commit()

def get_packs(uid, limit=5) -> list:
    with get_db() as c:
        rows = c.execute("""
            SELECT * FROM packs WHERE uid=?
            ORDER BY created_at DESC LIMIT ?
        """, (uid, limit)).fetchall()
        return [dict(r) for r in rows]

def get_stats() -> dict:
    with get_db() as c:
        users  = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        packs  = c.execute("SELECT COUNT(*) FROM packs").fetchone()[0]
        stars  = c.execute("SELECT COALESCE(SUM(stars_spent),0) FROM users").fetchone()[0]
        return {"users": users, "packs": packs, "stars": stars}

# ══════════════════════════════════════════════════════════
#  RATE LIMITER
# ══════════════════════════════════════════════════════════
_rate_map: dict[int, float] = {}

def is_rate_limited(uid) -> int:
    """يعيد الثواني المتبقية، 0 إذا لم يكن محدوداً"""
    if uid in ADMIN_IDS: return 0
    last = _rate_map.get(uid, 0)
    diff = time.time() - last
    if diff < RATE_LIMIT_SECS:
        return int(RATE_LIMIT_SECS - diff)
    return 0

def set_rate(uid):
    _rate_map[uid] = time.time()

# ══════════════════════════════════════════════════════════
#  FAL.AI SERVICE
# ══════════════════════════════════════════════════════════
FAL_HEADERS = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}

async def upload_to_fal(photo_bytes: bytes) -> str | None:
    """يرفع الصورة لـ fal.ai ويعيد الـ URL"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://storage.googleapis.com/fal-queue",
                headers={"Authorization": f"Key {FAL_KEY}"},
                content=photo_bytes,
                params={"content_type": "image/jpeg"}
            )
        if r.status_code == 200:
            return r.json().get("url")
    except Exception as e:
        log.warning(f"fal upload attempt 1 failed: {e}")

    # fallback: استخدم imgbb أو رابط مباشر من تيليغرام
    return None

async def upload_via_url(file_url: str) -> str:
    """يمرر رابط تيليغرام مباشرة لـ fal.ai"""
    return file_url

async def generate_sticker_fal(
    image_url: str, style_prompt: str, expression_prompt: str
) -> str | None:
    """يولّد ستيكر واحد عبر fal.ai"""
    prompt = (
        f"{style_prompt}, {expression_prompt}, "
        f"sticker art, white background, transparent background, "
        f"high quality, cute character"
    )
    payload = {
        "image_url": image_url,
        "prompt": prompt,
        "negative_prompt": (
            "nsfw, nude, violence, gore, realistic, photo, "
            "ugly, deformed, blurry, low quality"
        ),
        "instant_id_strength": 0.7,
        "guidance_scale": 7.5,
        "num_inference_steps": 20,
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            # Submit request
            r = await client.post(
                "https://queue.fal.run/fal-ai/face-to-sticker",
                headers=FAL_HEADERS, json=payload
            )
            if r.status_code not in (200, 201):
                log.warning(f"fal submit: {r.status_code} {r.text[:100]}")
                return None

            data      = r.json()
            req_id    = data.get("request_id")
            status_url= data.get("status_url", f"https://queue.fal.run/fal-ai/face-to-sticker/requests/{req_id}")

            # Poll until done
            for _ in range(60):
                await asyncio.sleep(3)
                sr = await client.get(status_url, headers=FAL_HEADERS)
                sd = sr.json()
                status = sd.get("status")
                if status == "COMPLETED":
                    result_url = sd.get("response_url", status_url.replace("/status","/response"))
                    rr = await client.get(result_url, headers=FAL_HEADERS)
                    rd = rr.json()
                    img = rd.get("image") or (rd.get("images") or [{}])[0]
                    return img.get("url")
                elif status == "FAILED":
                    log.warning(f"fal failed: {sd}")
                    return None
    except Exception as e:
        log.error(f"generate_sticker_fal: {e}")
    return None

async def generate_pack(
    image_url: str, style_key: str, bot
) -> list[str]:
    """يولّد 8 ستيكرات بالتوازي ويعيد قائمة file_ids"""
    _, style_prompt = STYLES[style_key]

    # توليد متوازٍ للستيكرات الثمانية
    tasks = [
        generate_sticker_fal(image_url, style_prompt, expr_prompt)
        for _, _, expr_prompt in EXPRESSIONS
    ]
    urls = await asyncio.gather(*tasks, return_exceptions=True)

    file_ids = []
    for url in urls:
        if isinstance(url, str) and url:
            try:
                msg = await bot.send_sticker(chat_id=bot.id, sticker=url)
                file_ids.append(msg.sticker.file_id)
            except Exception:
                try:
                    # إذا فشل كـ sticker، أرسله كصورة
                    file_ids.append(url)
                except Exception:
                    file_ids.append("")
    return [f for f in file_ids if f]

# ══════════════════════════════════════════════════════════
#  QUEUE SYSTEM
# ══════════════════════════════════════════════════════════
_queue: asyncio.Queue = asyncio.Queue()
_processing: set[int] = set()

async def queue_worker(bot):
    """worker يعمل في الخلفية ويعالج الطلبات بالترتيب"""
    log.info("🔄 Queue worker started")
    while True:
        try:
            job = await _queue.get()
            uid        = job["uid"]
            chat_id    = job["chat_id"]
            image_url  = job["image_url"]
            style_key  = job["style_key"]
            is_paid    = job.get("is_paid", False)

            _processing.add(uid)
            try:
                await bot.send_message(
                    chat_id,
                    f"⏳ جاري توليد ستيكراتك بأسلوب {STYLES[style_key][0]}...\n"
                    f"_قد يستغرق ذلك 30-60 ثانية_ 🎨",
                    parse_mode="Markdown"
                )

                file_ids = await generate_pack(image_url, style_key, bot)

                if not file_ids:
                    await bot.send_message(chat_id, "❌ فشل التوليد، حاول مرة أخرى لاحقاً.")
                    continue

                # أرسل الستيكرات
                await bot.send_message(
                    chat_id,
                    f"✅ *حزمة ستيكراتك جاهزة!*\n{STYLES[style_key][0]}\n"
                    f"📦 {len(file_ids)} ستيكر",
                    parse_mode="Markdown"
                )
                for fid in file_ids:
                    try:
                        await bot.send_sticker(chat_id, fid)
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass

                # حفظ في DB
                save_pack(uid, style_key, file_ids)
                if is_paid:
                    record_stars(uid, PACK_PRICE_STARS)
                else:
                    use_free(uid)

                # مكافأة المُحيل إذا كانت أول استخدام
                u = get_user(uid)
                if u and u.get("referred_by") and u.get("total_uses", 0) == 1:
                    referrer = u["referred_by"]
                    add_bonus(referrer)
                    try:
                        await bot.send_message(
                            referrer,
                            "🎁 *مكافأة!* صديقك الذي دعوته استخدم البوت\n"
                            "حصلت على استخدام مجاني إضافي 🎉",
                            parse_mode="Markdown"
                        )
                    except Exception: pass

                # عرض المزيد
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 حزمة بستايل آخر", callback_data=f"newpack_{uid}"),
                    InlineKeyboardButton("📦 ستيكراتي السابقة", callback_data="mystickers"),
                ]])
                await bot.send_message(
                    chat_id,
                    "هل تريد المزيد؟ 👇",
                    reply_markup=kb
                )

            finally:
                _processing.discard(uid)
                _queue.task_done()

        except Exception as e:
            log.error(f"queue_worker error: {e}")
            await asyncio.sleep(1)

# ══════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════
def kb_styles() -> InlineKeyboardMarkup:
    rows = []
    keys = list(STYLES.keys())
    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i:i+2]:
            row.append(InlineKeyboardButton(STYLES[k][0], callback_data=f"style_{k}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 إنشاء ستيكرات", callback_data="guide_photo")],
        [InlineKeyboardButton("📦 ستيكراتي",  callback_data="mystickers"),
         InlineKeyboardButton("🔗 دعوة أصدقاء", callback_data="invite")],
    ])

# ══════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)

    # معالجة الإحالة
    if context.args:
        ref_code = context.args[0]
        referrer = get_user_by_ref(ref_code)
        if referrer and referrer["uid"] != u.id:
            add_referred_by(u.id, referrer["uid"])

    free_left = check_free_uses(u.id)
    sep = "─" * 16
    text = (
        f"{sep}\n"
        f"🎨 *أهلاً {u.first_name}!*\n"
        f"بوت الستيكرات الذكي 🤖\n"
        f"{sep}\n"
        f"أرسل *صورة وجهك* وحوّلها إلى\n"
        f"8 ستيكرات كرتونية بتعابير مختلفة!\n\n"
        f"🆓 لديك اليوم: *{free_left} استخدام مجاني*\n"
        f"⭐ أو ادفع *{PACK_PRICE_STARS} Stars* للمزيد\n"
        f"{sep}\n"
        f"📸 ابدأ بإرسال صورة وجهك الآن!"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_main())


async def cmd_mystickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    packs = get_packs(u.id)
    if not packs:
        await update.message.reply_text(
            "📭 لا توجد ستيكرات بعد\nأرسل صورة وجهك للبدء! 📸"
        ); return
    await update.message.reply_text(
        f"📦 *آخر {len(packs)} حزمة ستيكرات:*",
        parse_mode="Markdown"
    )
    for pack in packs:
        file_ids = json.loads(pack["file_ids"])
        style    = STYLES.get(pack["style"], (pack["style"],))[0]
        date_str = pack["created_at"][:10]
        await update.message.reply_text(
            f"🎨 {style} — {date_str} ({len(file_ids)} ستيكر)"
        )
        for fid in file_ids[:3]:  # أرسل أول 3 كمعاينة
            try:
                await update.message.reply_sticker(fid)
                await asyncio.sleep(0.2)
            except Exception: pass


async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = get_user(u.id)
    if not user: save_user(u.id, u.username, u.full_name); user = get_user(u.id)
    ref_code = user["ref_code"]
    link     = f"https://t.me/{BOT_USERNAME}?start={ref_code}"
    text = (
        "🔗 *رابط الدعوة الخاص بك:*\n\n"
        f"`{link}`\n\n"
        "📋 شارك الرابط مع أصدقائك\n"
        "🎁 كل صديق يستخدم البوت = *استخدام مجاني مجاني لك!*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    s = get_stats()
    await update.message.reply_text(
        f"📊 *إحصائيات البوت*\n"
        f"━━━━━━━━━━━━━━\n"
        f"👥 المستخدمون: {s['users']:,}\n"
        f"📦 الحزم المولّدة: {s['packs']:,}\n"
        f"⭐ Stars المنفقة: {s['stars']:,}\n"
        f"💰 الإيراد: ~${s['stars']*0.013:.2f}",
        parse_mode="Markdown"
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)

    # Rate limit
    wait = is_rate_limited(u.id)
    if wait:
        await update.message.reply_text(
            f"⏱ انتظر {wait} ثانية قبل الطلب التالي"
        ); return

    # هل المستخدم في قائمة الانتظار؟
    if u.id in _processing:
        await update.message.reply_text(
            "⏳ طلبك السابق لا يزال قيد المعالجة، انتظر قليلاً"
        ); return

    # احصل على رابط الصورة
    photo  = update.message.photo[-1]
    file   = await context.bot.get_file(photo.file_id)
    image_url = file.file_path

    # خزّن الصورة في user_data
    context.user_data["pending_image"] = image_url
    set_rate(u.id)

    # عرض الاستخدامات المتبقية
    free_left = check_free_uses(u.id)
    note = (f"🆓 لديك *{free_left} استخدام مجاني* متبقٍ اليوم"
            if free_left > 0 else
            f"⭐ لا يوجد استخدام مجاني — ادفع *{PACK_PRICE_STARS} Stars*")

    await update.message.reply_text(
        f"✅ *تم استلام الصورة!*\n──────────────────\n"
        f"اختر الأسلوب الكرتوني 🎨\n\n{note}",
        parse_mode="Markdown",
        reply_markup=kb_styles()
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()

    data = q.data

    # اختيار الستايل
    if data.startswith("style_"):
        style_key = data[6:]
        image_url = context.user_data.get("pending_image")

        if not image_url:
            await q.edit_message_text("❌ أرسل صورة جديدة أولاً 📸"); return

        free_left = check_free_uses(uid)
        if free_left > 0:
            # استخدام مجاني
            await q.edit_message_text(
                f"🎨 *{STYLES[style_key][0]}*\n"
                f"جاري إضافتك لقائمة الانتظار...\n"
                f"⏳ سيصلك الرد خلال دقيقة",
                parse_mode="Markdown"
            )
            await _queue.put({
                "uid":       uid,
                "chat_id":   q.message.chat_id,
                "image_url": image_url,
                "style_key": style_key,
                "is_paid":   False,
            })
            context.user_data.pop("pending_image", None)
        else:
            # يحتاج دفع
            context.user_data["pending_style"] = style_key
            await q.edit_message_text(
                f"⭐ *الدفع مطلوب*\n──────────────────\n"
                f"الأسلوب: {STYLES[style_key][0]}\n"
                f"السعر: *{PACK_PRICE_STARS} Telegram Stars*\n"
                f"المحتوى: 8 ستيكرات فورية 🎨",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        f"⭐ ادفع {PACK_PRICE_STARS} Stars",
                        callback_data=f"pay_{style_key}"
                    )
                ]])
            )
        return

    # فاتورة الدفع
    if data.startswith("pay_"):
        style_key = data[4:]
        await context.bot.send_invoice(
            chat_id=uid,
            title="🎨 حزمة ستيكرات كرتونية",
            description=f"8 ستيكرات بأسلوب {STYLES[style_key][0]} بتعابير مختلفة",
            payload=f"stickers_{uid}_{style_key}",
            currency="XTR",
            prices=[LabeledPrice("حزمة ستيكرات", PACK_PRICE_STARS)],
        )
        return

    if data == "mystickers":
        packs = get_packs(uid)
        if not packs:
            await q.edit_message_text("📭 لا توجد ستيكرات بعد\nأرسل صورة للبدء! 📸"); return
        await q.edit_message_text(f"📦 لديك {len(packs)} حزمة — استخدم /mystickers لعرضها")
        return

    if data == "invite":
        user = get_user(uid)
        if not user: return
        link = f"https://t.me/{BOT_USERNAME}?start={user['ref_code']}"
        await q.edit_message_text(
            f"🔗 *رابط الدعوة:*\n\n`{link}`\n\n"
            f"🎁 كل صديق = استخدام مجاني لك!",
            parse_mode="Markdown"
        )
        return

    if data == "guide_photo":
        await q.edit_message_text(
            "📸 *كيفية الاستخدام:*\n──────────────────\n"
            "1️⃣ أرسل *صورة سيلفي واضحة* لوجهك\n"
            "2️⃣ اختر *الأسلوب الكرتوني*\n"
            "3️⃣ انتظر *30-60 ثانية*\n"
            "4️⃣ احصل على *8 ستيكرات* بتعابير مختلفة! 🎉\n\n"
            "📷 أرسل صورتك الآن 👇",
            parse_mode="Markdown"
        )
        return

    if data.startswith("newpack_"):
        context.user_data.pop("pending_image", None)
        await q.edit_message_text(
            "📸 أرسل صورة جديدة لإنشاء حزمة ستيكرات جديدة!"
        )
        return


async def handle_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    payment = update.message.successful_payment
    payload = payment.invoice_payload  # stickers_{uid}_{style_key}

    parts     = payload.split("_")
    style_key = parts[-1] if len(parts) >= 3 else "pixar"
    image_url = context.user_data.get("pending_image")

    if not image_url:
        await update.message.reply_text(
            "⚠️ تم الدفع لكن الصورة غير موجودة\nأرسل صورة جديدة مع كتابة /retry"
        )
        context.user_data["paid_style"] = style_key
        return

    await update.message.reply_text(
        f"✅ *تم الدفع بنجاح!* ⭐\n"
        f"جاري معالجة طلبك...",
        parse_mode="Markdown"
    )
    await _queue.put({
        "uid":       uid,
        "chat_id":   update.message.chat_id,
        "image_url": image_url,
        "style_key": style_key,
        "is_paid":   True,
    })
    context.user_data.pop("pending_image", None)


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════
async def post_init(app: Application):
    init_db()
    asyncio.create_task(queue_worker(app.bot))
    await app.bot.set_my_commands([
        ("start",      "🏠 القائمة الرئيسية"),
        ("mystickers", "📦 ستيكراتي السابقة"),
        ("invite",     "🔗 دعوة الأصدقاء"),
        ("stats",      "📊 إحصائيات (مشرف)"),
    ])
    log.info("✅ StickerBot ready!")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
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
    log.info("🚀 Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
