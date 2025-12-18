"""
Main entry point for the Telegram bot.
Initializes the bot, sets up the dispatcher, and starts polling.
"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN
from db import init_db
from handlers.user_handlers import user_router
from handlers.admin_handlers import admin_router
from handlers.chat_handlers import chat_router

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


async def main():
    """Main function to initialize database and start the bot."""
    # Initialize database connection pool
    pool = await init_db()
    dp["db"] = pool

    # Include routers
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(chat_router)

    try:
        # Start polling
        await dp.start_polling(bot)
    finally:
        # Close database pool on shutdown
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
