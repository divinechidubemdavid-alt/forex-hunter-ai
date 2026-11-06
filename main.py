
import logging
import os

from telegram.ext import Application

from config import TELEGRAM_BOT_TOKEN
from bot import register_handlers
from auto_scanner import auto_scan_loop
from signal_monitor import monitor_signals_loop


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("forex_hunter_ai")


async def post_init(application):
    """Start the central automatic scanner after Telegram starts."""

    logger.info("Forex Hunter AI Telegram application started.")

    application.create_task(
        auto_scan_loop(application),
        name="forex-hunter-adaptive-scanner",
    )
    application.create_task(
        monitor_signals_loop(application),
        name="forex-hunter-signal-monitor",
    )


async def post_shutdown(application):
    """Log a clean application shutdown."""

    logger.info("Forex Hunter AI is shutting down.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. "
            "Add it to your environment secrets."
        )

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    register_handlers(application)

    logger.info("Starting Forex Hunter AI polling.")

    application.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
