"""
Database module for managing database connections and operations.
Handles database pool creation and all database helper functions.
"""
import asyncpg
from datetime import datetime
from zoneinfo import ZoneInfo
from config import DATABASE_URL, TIMEZONE


async def init_db():
    """
    Initialize database connection pool and create tables if they don't exist.
    Returns the connection pool.
    """
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                name TEXT,
                token TEXT UNIQUE,
                is_admin BOOLEAN DEFAULT FALSE,
                is_superuser BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS muted_users(
                user_id     BIGINT PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
                muted_until TIMESTAMP NOT NULL,
                reason      TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS message_log(
                id          SERIAL PRIMARY KEY,
                sender_id   BIGINT,
                receiver_id BIGINT,
                message     TEXT,
                sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    return pool


async def get_user_by_token(pool, token: str):
    """Get user information by their unique token."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT user_id FROM users WHERE token = $1", token)


async def is_user_muted(pool, user_id: int) -> tuple[bool, datetime | None]:
    """
    Check if a user is currently muted.
    Returns (is_muted: bool, muted_until: datetime | None).
    Automatically removes expired mute records.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT muted_until FROM muted_users WHERE user_id = $1", user_id)
        if row:
            muted_until = row["muted_until"]
            current_time = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
            if muted_until > current_time:
                return True, muted_until
            else:
                await conn.execute("DELETE FROM muted_users WHERE user_id = $1", user_id)
        return False, None


async def is_user_admin(pool, user_id: int) -> bool:
    """Check if a user has admin privileges."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_admin FROM users WHERE user_id = $1", user_id)
        return bool(row and row['is_admin'])


async def log_message(pool, sender_id, receiver_id, text):
    """Log a message to the message_log table with Tashkent timezone."""
    tashkent_time = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO message_log (sender_id, receiver_id, message, sent_at)
            VALUES ($1, $2, $3, $4)
        """, sender_id, receiver_id, text, tashkent_time)


async def get_or_create_user(pool, user_id: int, username: str, name: str):
    """
    Get user token if exists, otherwise create new user and return token.
    Returns the user's token.
    """
    from utils import generate_token

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT token FROM users WHERE user_id = $1", user_id)
        if row:
            return row["token"]
        else:
            token = generate_token()
            tashkent_time = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
            await conn.execute(
                "INSERT INTO users (user_id, username, name, token, created_at) VALUES ($1, $2, $3, $4, $5)",
                user_id, username, name, token, tashkent_time
            )
            return token
