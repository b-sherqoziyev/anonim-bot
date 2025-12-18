"""
Configuration module for the Telegram bot.
Loads and stores all global settings from environment variables.
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Bot configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")
ADMIN_URL = os.getenv("ADMIN_URL")

# Timezone configuration
TIMEZONE = "Asia/Tashkent"

