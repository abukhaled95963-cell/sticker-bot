#!/usr/bin/env python3
"""
StickerBot v1.0.0
"""

import asyncio, logging, os, sqlite3, json, time, hashlib
from datetime import datetime, date
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
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN        = os.environ["BOT_TOKEN"]
FAL_KEY          = os.environ["FAL_KEY"]
BOT_USERNAME     = os.environ.get("BOT_USERNAME", "StickerBot")
PACK_PRICE_STARS = int(os.environ.get("PACK_PRICE_STARS", "75"))
FREE_DAILY_LIMIT = int(os.environ.get("FREE_DAILY_LIMIT", "2"))
RATE_LIMIT_SECS  = int(os.environ.get("RATE_LIMIT_SECONDS", "60"))
ADMIN_IDS        = [int(x) for x in os.environ.get("ADMIN_IDS","").split(",") if x.strip().isdigit()]
DB_PATH          = os.environ.get("DB_PATH", "data/sticker_bot.db")
os.environ["FAL_KEY"] = FAL_KEY

STYLES = {
    "pixar":     ("Pixar 3D",    "Pixar 3D animation style, cute 3D character"),
    "anime":     ("Anime",       "anime style, Japanese animation, cute"),
    "watercolor":("Watercolor",  "watercolor painting style, soft brushstrokes"),
    "cartoon":   ("Cartoon",     "cartoon style, bold outlines, exaggerated features"),
    "pixel":     ("Pixel Art",   "pixel art style, 8-bit, retro game character"),
}

EXPRESSIONS = [
    ("happy",       "happy, smiling, joyful expression"),
    ("angry",       "angry, furious, mad expression"),
    ("sad",         "sad, crying, tearful expression"),
    ("surprised",   "surprised, shocked, amazed expression"),
    ("loving",      "loving, heart eyes, romantic expression"),
    ("sleeping",    "sleeping, tired, zzz expression"),
    ("thinking",    "thinking, pondering, curious expression"),
    ("celebrating", "celebrating, party hat, festive expression"),
]

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
            uid INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            ref_code TEXT UNIQUE, referred_by INTEGER,
            free_today INTEGER DEFAULT 0, last_date TEXT DEFAULT '',
            total_uses INTEGER DEFAULT 0, bonus_uses INTEGER DEFAULT 0,
            stars_spent INTEGER DEFAULT 0, joined_at TEXT, last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, style TEXT,
            file_ids TEXT, thumb_id TEXT, created_at TEXT
        );
        """)
        c.commit()
    log.info(f"DB ready: {DB_PATH}")

def save_user(uid, username, full_name):
    ref = hashlib.md5(str(uid).encode()).hexdigest()[:8]
    with get_db() as c:
        c.execute("""INSERT INTO users(uid,username,full_name,ref_code,joined_at,last_seen)
            VALUES(?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET
            username=excluded.username,full_name=excluded.full_name,last_seen=excluded.last_seen""",
            (uid, username or "", full_name or "", ref, datetime.now().isoformat(), datetime.now().isoformat()))
        c.commit()

def get_user(uid):
    with get_db() as c:
        r = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(r) if r else None

def get_user_by_ref(ref_code):
    with get_db() as c:
        r = c.execute("SELECT * FROM users WHERE ref_code=?", (ref_code,)).fetchone()
        return dict(r) if r else None

def check_free_uses(uid):
    u = get_user(uid)
    if not u: return FREE_DAILY_LIMIT
    today = date.today().isoformat()
    if u["last_date"] != today:
        with get_db() as c:
            c.execute("UPDATE users SET free_today=0,last_date=? WHERE uid=?", (today, uid))
            c.commit()
        return FREE_DAILY_LIMIT + (u["bonus_uses"] or 0)
    return max(0, FREE_DAILY_LIMIT - (u["free_today"] or 0) + (u["bonus_uses"] or 0))

def use_free(uid):
    with get_db() as c:
        today = date.today().isoformat()
        c.execute("""UPDATE users SET free_today=CASE WHEN last_date=? THEN free_today+1 ELSE 1 END,
            last_date=?,total_uses=total_uses+1,last_seen=? WHERE uid=?""",
            (today, today, datetime.now().isoformat(), uid))
        c.commit()

def add_bonus(uid):
    with get_db() as c:
        c.execute("UPDATE users SET bonus_uses=bonus_uses+1 WHERE uid=?", (uid,))
        c.commit()

def add_referred_by(uid, referrer_uid):
    with get_db() as c:
        c.execute("UPDATE users SET referred_by=? WHERE uid=? AND referred_by IS NULL", (referrer_uid, uid))
        c.commit()

def record_stars(uid, stars):
    with get_db() as c:
        c.execute("UPDATE users SET stars_spent=stars_spent+?,total_uses=total_uses+1,last_seen=? WHERE uid=?",
            (stars, datetime.now().isoformat(), uid))
        c.commit()

def save_pack(uid, style, file_ids, thumb_id=""):
    with get_db() as c:
        c.execute("INSERT INTO packs(uid,style,file_ids,thumb_id,created_at) VALUES(?,?,?,?,?)",
            (uid, style, json.dumps(file_ids), thumb_id, datetime.now().isoformat()))
        c.commit()

def get_packs(uid, limit=5):
    with get_db() as c:
        rows = c.execute("SELECT * FROM packs WHERE uid=? ORDER BY created_at DESC LIMIT ?", (uid, limit)).fetchall()
        return [dict(r) for r in rows]

def get_stats():
    with get_db() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        packs = c.execute("SELECT COUNT(*) FROM packs").fetchone()[0]
        stars = c.execute("SELECT COALESCE(SUM(stars_spent),0) FROM users").fetchone()[0]
        return {"users": users, "packs": packs, "stars": stars}

ratemap = {}

def is_rate_limited(uid):
    if uid in ADMIN_IDS: return 0
    diff = time.time() - ratemap.get(uid, 0)
    return int(RATE_LIMIT_SECS - diff) if diff < RATE_LIMIT_SECS else 0

def set_rate(uid):
    ratemap[uid] = time.time()

FAL_HEADERS = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}

async def generate_sticker_fal(image_url, style_prompt, expression_prompt):
    prompt = f"{style_prompt}, {expression_prompt}, sticker art, white background, high quality, cute character"
    payload = {
        "image_url": image_url, "prompt": prompt,
        "negative_prompt": "nsfw, nude, violence, realistic, photo, ugly, deformed, blurry",
        "instant_id_strength": 0.7, "guidance_scale": 7.5, "num_inference_steps": 20,
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post("https://queue.fal.run/fal-ai/face-to-sticker", headers=FAL_HEADERS, json=payload)
            if r.status_code not in (200, 201): return None
            data = r.json()
            req_id = data.get("request_id")
            status_url = data.get("status_url", f"https://queue.fal.run/fal-ai/face-to-sticker/requests/{req_id}")
            for _ in range(60):
                await asyncio.sleep(3)
                sr = await client.get(status_url, headers=FAL_HEADERS)
                sd = sr.json()
                if sd.get("status") == "COMPLETED":
                    result_url = sd.get("response_url", status_url.replace("/status", "/response"))
                    rr = await client.get(result_url, headers=FAL_HEADERS)
                    rd = rr.json()
                    img = rd.get("image") or (rd.get("images") or [{}])[0]
                    return img.get("url")
                elif sd.get("status") == "FAILED":
                    return None
    except Exception as e:
        log.error(f"generate_sticker_fal: {e}")
    return None

async def generate_pack(image_url, style_key, bot):
    _, style_prompt = STYLES[style_key]
    tasks = [generate_sticker_fal(image_url, style_prompt, ep) for _, ep in EXPRESSIONS]
    urls = await asyncio.gather(*tasks, return_exceptions=True)
    file_ids = []
    for url in urls:
        if isinstance(url, str) and url:
            file_ids.append(url)
    return file_ids

_queue = asyncio.Queue()
_processing = set()

async def queue_worker(bot):
    log.info("Queue worker started")
    while True:
        try:
            job = await asyncio.wait_for(_queue.get(), timeout=5.0)
            uid = job["uid"]
            chat_id = job["chat_id"]
            image_url = job["image_url"]
            style_key = job["style_key"]
            is_paid = job.get("is_paid", False)
            _processing.add(uid)
            try:
                await bot.send_message(chat_id, f"Generating stickers with {STYLES[style_key][0]} style... 30-60 sec")
                file_ids = await generate_pack(image_url, style_key, bot)
                if not file_ids:
                    await bot.send_message(chat_id, "Generation failed, try again later.")
                    continue
                await bot.send_message(chat_id, f"Done! {len(file_ids)} stickers ready!")
                for fid in file_ids:
                    try:
                        if fid.startswith("http"):
                            await bot.send_photo(chat_id, fid)
                        else:
                            await bot.send_sticker(chat_id, fid)
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
                save_pack(uid, style_key, file_ids)
                if is_paid:
                    record_stars(uid, PACK_PRICE_STARS)
                else:
                    use_free(uid)
                u = get_user(uid)
                if u and u.get("referred_by") and u.get("total_uses", 0) == 1:
                    add_bonus(u["referred_by"])
                    try:
                        await bot.send_message(u["referred_by"], "Your friend used the bot! You got a free use!")
                    except Exception:
                        pass
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("New Style", callback_data=f"newpack_{uid}"),
                    InlineKeyboardButton("My Stickers", callback_data="mystickers"),
                ]])
                await bot.send_message(chat_id, "Want more?", reply_markup=kb)
            finally:
                _processing.discard(uid)
                _queue.task_done()
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            log.info("Queue worker cancelled cleanly")
            break
        except Exception as e:
            log.error(f"queue_worker error: {e}")
            await asyncio.sleep(1)

def kb_styles():
    rows = []
    keys = list(STYLES.keys())
    for i in range(0, len(keys), 2):
        row = [InlineKeyboardButton(STYLES[k][0], callback_data=f"style_{k}") for k in keys[i:i+2]]
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Create Stickers", callback_data="guide_photo")],
        [InlineKeyboardButton("My Stickers", callback_data="mystickers"),
         InlineKeyboardButton("Invite Friends", callback_data="invite")],
    ])

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)
    if context.args:
        referrer = get_user_by_ref(context.args[0])
        if referrer and referrer["uid"] != u.id:
            add_referred_by(u.id, referrer["uid"])
    free_left = check_free_uses(u.id)
    await update.message.reply_text(
        f"Welcome {u.first_name}! Send me a face photo to create stickers!\n"
        f"Free uses today: {free_left} | Price: {PACK_PRICE_STARS} Stars",
        reply_markup=kb_main()
    )

async def cmd_mystickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    packs = get_packs(update.effective_user.id)
    if not packs:
        await update.message.reply_text("No stickers yet. Send a photo to start!")
        return
    await update.message.reply_text(f"Your last {len(packs)} packs:")
    for pack in packs:
        file_ids = json.loads(pack["file_ids"])
        await update.message.reply_text(f"{STYLES.get(pack['style'], (pack['style'],))[0]} - {pack['created_at'][:10]} ({len(file_ids)} stickers)")

async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = get_user(u.id)
    if not user:
        save_user(u.id, u.username, u.full_name)
        user = get_user(u.id)
    link = f"https://t.me/{BOT_USERNAME}?start={user['ref_code']}"
    await update.message.reply_text(f"Your invite link:\n{link}\nEach friend = 1 free use!")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    s = get_stats()
    await update.message.reply_text(f"Users: {s['users']}\nPacks: {s['packs']}\nStars: {s['stars']}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.full_name)
    wait = is_rate_limited(u.id)
    if wait:
        await update.message.reply_text(f"Wait {wait} seconds")
        return
    if u.id in _processing:
        await update.message.reply_text("Your previous request is still processing")
        return
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    context.user_data["pending_image"] = file.file_path
    set_rate(u.id)
    free_left = check_free_uses(u.id)
    note = f"Free uses left: {free_left}" if free_left > 0 else f"No free uses - pay {PACK_PRICE_STARS} Stars"
    await update.message.reply_text(f"Photo received! Choose style:\n{note}", reply_markup=kb_styles())

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    data = q.data
    if data.startswith("style_"):
        style_key = data[6:]
        image_url = context.user_data.get("pending_image")
        if not image_url:
            await q.edit_message_text("Send a photo first!")
            return
        if check_free_uses(uid) > 0:
            await q.edit_message_text(f"Added to queue! Style: {STYLES[style_key][0]}")
            await _queue.put({"uid": uid, "chat_id": q.message.chat_id, "image_url": image_url, "style_key": style_key, "is_paid": False})
            context.user_data.pop("pending_image", None)
        else:
            context.user_data["pending_style"] = style_key
            await q.edit_message_text(
                f"Payment required: {PACK_PRICE_STARS} Stars for {STYLES[style_key][0]}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"Pay {PACK_PRICE_STARS} Stars", callback_data=f"pay_{style_key}")]])
            )
        return
    if data.startswith("pay_"):
        style_key = data[4:]
        await context.bot.send_invoice(
            chat_id=uid, title="Sticker Pack",
            description=f"8 stickers in {STYLES[style_key][0]} style",
            payload=f"stickers_{uid}_{style_key}", currency="XTR",
            prices=[LabeledPrice("Sticker Pack", PACK_PRICE_STARS)],
        )
        return
    if data == "mystickers":
        packs = get_packs(uid)
        await q.edit_message_text(f"You have {len(packs)} packs. Use /mystickers to view." if packs else "No stickers yet!")
        return
    if data == "invite":
        user = get_user(uid)
        if user:
            link = f"https://t.me/{BOT_USERNAME}?start={user['ref_code']}"
            await q.edit_message_text(f"Your invite link:\n{link}")
        return
    if data == "guide_photo":
        await q.edit_message_text("Send a clear selfie photo to start!")
        return
    if data.startswith("newpack_"):
        context.user_data.pop("pending_image", None)
        await q.edit_message_text("Send a new photo to create a new sticker pack!")
        return

async def handle_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    payment = update.message.successful_payment
    parts = payment.invoice_payload.split("_")
    style_key = parts[-1] if len(parts) >= 3 else "pixar"
    image_url = context.user_data.get("pending_image")
    if not image_url:
        await update.message.reply_text("Payment received but no photo found. Send a photo again.")
        context.user_data["paid_style"] = style_key
        return
    await update.message.reply_text("Payment successful! Processing your request...")
    await _queue.put({"uid": uid, "chat_id": update.message.chat_id, "image_url": image_url, "style_key": style_key, "is_paid": True})
    context.user_data.pop("pending_image", None)

workertask = None

async def post_init(app: Application):
    global workertask
    init_db()
    workertask = app.create_task(queue_worker(app.bot), name="queue_worker")
    await app.bot.set_my_commands([
        ("start", "Main menu"),
        ("mystickers", "My sticker packs"),
        ("invite", "Invite friends"),
        ("stats", "Stats (admin)"),
    ])
    log.info("StickerBot ready!")

async def post_shutdown(app: Application):
    global workertask
    if workertask and not workertask.done():
        workertask.cancel()
        try:
            await workertask
        except asyncio.CancelledError:
            pass
    log.info("StickerBot stopped cleanly")

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("mystickers", cmd_mystickers))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(PreCheckoutQueryHandler(handle_precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_payment))
    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
