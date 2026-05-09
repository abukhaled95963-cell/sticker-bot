"""
🎨 Sticker Bot — Main Entry Point
Telegram bot that converts face photos to cartoon sticker packs
"""

import asyncio
import logging
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    filters,
)
from bot.handlers import (
    start_handler,
    help_handler,
    photo_handler,
    style_callback,
    invite_handler,
    mystickers_handler,
)
from bot.payment_handler import (
    precheckout_handler,
    successful_payment_handler,
    send_invoice_handler,
)
from workers.queue_worker import start_worker
from utils.config import BOT_TOKEN
from utils.database import init_db

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    """Runs after the bot starts — initialize DB and worker."""
    await init_db()
    asyncio.create_task(start_worker(application.bot))
    logger.info("✅ Bot initialized successfully")


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── Commands ──────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("invite", invite_handler))
    app.add_handler(CommandHandler("mystickers", mystickers_handler))

    # ── Photos ────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    # ── Inline buttons (style selection, retry, etc.) ─────────
    app.add_handler(CallbackQueryHandler(style_callback, pattern="^style_"))
    app.add_handler(CallbackQueryHandler(send_invoice_handler, pattern="^buy_"))

    # ── Payments ──────────────────────────────────────────────
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    logger.info("🚀 Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
