"""
🤖 Handlers — Bot commands and photo processing
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.config import FREE_DAILY_LIMIT, RATE_LIMIT_SECONDS, BOT_USERNAME
from utils.database import (
    upsert_user, get_user, can_use_free, consume_free,
    create_order, check_rate_limit, update_rate_limit,
    get_user_stickers, add_invite_credit, use_invite_credit,
)
from utils.moderation import check_image_safe
from utils.messages import (
    WELCOME, HELP, CHOOSE_STYLE, PROCESSING, PAYMENT_REQUIRED,
    RATE_LIMITED, MODERATION_FAILED, ERROR_GENERIC,
    INVITE_MESSAGE, MY_STICKERS_EMPTY, MY_STICKERS_HEADER, STYLES,
)
from workers.queue_worker import enqueue_job, queue_size

logger = logging.getLogger(__name__)

async def start_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
      user = update.effective_user
      args = ctx.args
      invited_by = None
      if args and args[0].startswith("ref_"):
                try:
                              invited_by = int(args[0].replace("ref_", ""))
                              if invited_by != user.id:
                                                await add_invite_credit(invited_by)
                except ValueError:
                              pass
                      await upsert_user(user.id, user.username or "", user.full_name, invited_by)
            db_user = await get_user(user.id)
    free_used = db_user.get("free_used", 0) if db_user else 0
    free_remaining = max(0, FREE_DAILY_LIMIT - free_used)
    await update.message.reply_text(
              WELCOME.format(free_remaining=free_remaining), parse_mode="Markdown")

async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
      await update.message.reply_text(HELP, parse_mode="Markdown")

async def invite_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
      user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user:
              await upsert_user(user.id, user.username or "", user.full_name)
              db_user = await get_user(user.id)
          import aiosqlite
    from utils.database import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
              async with db.execute(
                            "SELECT COUNT(*) FROM users WHERE invited_by = ?", (user.id,)
              ) as cur:
                            row = await cur.fetchone()
                            invited_count = row[0] if row else 0
                    await update.message.reply_text(
                              INVITE_MESSAGE.format(
                                            bot_username=BOT_USERNAME, user_id=user.id,
                                            invited_count=invited_count, credits=db_user.get("invite_credits", 0),
                              ), parse_mode="Markdown")

async def mystickers_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
      user = update.effective_user
    stickers = await get_user_stickers(user.id)
    if not stickers:
              await update.message.reply_text(MY_STICKERS_EMPTY, parse_mode="Markdown")
        return
    await update.message.reply_text(
              MY_STICKERS_HEADER.format(count=len(stickers)), parse_mode="Markdown")
    for s in stickers[:10]:
              await update.message.reply_photo(
                  photo=s["file_id"], caption=f"🎨 {s['style']} — {s['created_at'][:10]}")

async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
      user = update.effective_user
    await upsert_user(user.id, user.username or "", user.full_name)
    allowed, wait_secs = await check_rate_limit(user.id, RATE_LIMIT_SECONDS)
    if not allowed:
              await update.message.reply_text(
                  RATE_LIMITED.format(seconds=wait_secs), parse_mode="Markdown")
        return
    try:
              photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())
except Exception as e:
        logger.error(f"Photo download error: {e}")
        await update.message.reply_text(ERROR_GENERIC, parse_mode="Markdown")
        return
    is_safe, _ = await check_image_safe(image_bytes)
    if not is_safe:
              await update.message.reply_text(MODERATION_FAILED, parse_mode="Markdown")
        return
    ctx.user_data["pending_image"] = image_bytes.hex()
    await update_rate_limit(user.id)
    keyboard = _build_style_keyboard()
    await update.message.reply_text(
              CHOOSE_STYLE, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def style_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
      query = update.callback_query
    await query.answer()
    user = update.effective_user
    style_key = query.data.replace("style_", "")
    if style_key not in STYLES:
              return
    style_name, style_prompt = STYLES[style_key]
    image_hex = ctx.user_data.get("pending_image")
    if not image_hex:
              await query.edit_message_text("⚠️ انتهت صلاحية الصورة. أرسل صورتك مرة أخرى.")
        return
    db_user = await get_user(user.id)
    invite_credits = db_user.get("invite_credits", 0) if db_user else 0
    has_free = await can_use_free(user.id, FREE_DAILY_LIMIT)
    if has_free or invite_credits > 0:
              if invite_credits > 0:
                            await use_invite_credit(user.id)
else:
            await consume_free(user.id)
        order_id = await create_order(user.id, style_name)
        await query.edit_message_text(PROCESSING, parse_mode="Markdown")
        await enqueue_job({
                      "user_id": user.id, "order_id": order_id,
                      "image_hex": image_hex, "style_prompt": style_prompt,
                      "style_name": style_name, "bot_username": BOT_USERNAME,
        })
        pos = queue_size()
        if pos > 1:
                      await ctx.bot.send_message(user.id, f"📋 طلبك في الصف رقم {pos}.")
        else:
        ctx.user_data["pending_style_key"] = style_key
        ctx.user_data["pending_style_name"] = style_name
        ctx.user_data["pending_style_prompt"] = style_prompt
        keyboard = [[InlineKeyboardButton("💳 ادفع مقابل الحزمة", callback_data=f"buy_{style_key}")]]
        await query.edit_message_text(
                      PAYMENT_REQUIRED, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

def _build_style_keyboard():
      buttons = [
                InlineKeyboardButton(label, callback_data=f"style_{key}")
                for key, (label, _) in STYLES.items()
      ]
    return [buttons[i:i+2] for i in range(0, len(buttons), 2)]
