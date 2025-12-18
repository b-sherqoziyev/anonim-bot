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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_connections(
                id          SERIAL PRIMARY KEY,
                user1_id    BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
                user2_id    BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user1_id, user2_id)
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_queue(
                user_id     BIGINT PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
                joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs(
                id          SERIAL PRIMARY KEY,
                admin_id    BIGINT NOT NULL,
                action      TEXT NOT NULL,
                details     TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


# Chat-related database functions

async def add_to_chat_queue(pool, user_id: int):
    """Add user to the chat queue if not already in queue or in an active chat."""
    async with pool.acquire() as conn:
        # Check if user is already in a chat
        active_chat = await conn.fetchrow("""
            SELECT user1_id, user2_id FROM chat_connections 
            WHERE user1_id = $1 OR user2_id = $1
        """, user_id)
        
        if active_chat:
            return False, "already_in_chat"
        
        # Check if user is already in queue
        in_queue = await conn.fetchrow("SELECT user_id FROM chat_queue WHERE user_id = $1", user_id)
        if in_queue:
            return False, "already_in_queue"
        
        # Add to queue
        await conn.execute("INSERT INTO chat_queue (user_id) VALUES ($1)", user_id)
        return True, "added"


async def find_chat_partner(pool, user_id: int):
    """
    Find a random chat partner for the user from the queue.
    Returns (found: bool, partner_id: int | None)
    """
    async with pool.acquire() as conn:
        # Get a random user from queue (excluding current user)
        partner = await conn.fetchrow("""
            SELECT user_id FROM chat_queue 
            WHERE user_id != $1 
            ORDER BY RANDOM() 
            LIMIT 1
        """, user_id)
        
        if partner:
            partner_id = partner["user_id"]
            # Remove both users from queue
            await conn.execute("DELETE FROM chat_queue WHERE user_id IN ($1, $2)", user_id, partner_id)
            # Create chat connection
            await conn.execute("""
                INSERT INTO chat_connections (user1_id, user2_id) 
                VALUES ($1, $2)
            """, user_id, partner_id)
            return True, partner_id
        
        return False, None


async def get_chat_partner(pool, user_id: int):
    """Get the chat partner ID for a user. Returns partner_id or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT 
                CASE 
                    WHEN user1_id = $1 THEN user2_id 
                    ELSE user1_id 
                END as partner_id
            FROM chat_connections 
            WHERE user1_id = $1 OR user2_id = $1
        """, user_id)
        return row["partner_id"] if row else None


async def end_chat(pool, user_id: int):
    """
    End chat for a user and their partner.
    Returns (ended: bool, partner_id: int | None)
    """
    async with pool.acquire() as conn:
        # Get partner
        partner_row = await conn.fetchrow("""
            SELECT 
                CASE 
                    WHEN user1_id = $1 THEN user2_id 
                    ELSE user1_id 
                END as partner_id
            FROM chat_connections 
            WHERE user1_id = $1 OR user2_id = $1
        """, user_id)
        
        if partner_row:
            partner_id = partner_row["partner_id"]
            # Delete chat connection
            await conn.execute("""
                DELETE FROM chat_connections 
                WHERE (user1_id = $1 AND user2_id = $2) OR (user1_id = $2 AND user2_id = $1)
            """, user_id, partner_id)
            return True, partner_id
        
        return False, None


async def remove_from_chat_queue(pool, user_id: int):
    """Remove user from chat queue."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM chat_queue WHERE user_id = $1", user_id)


# Admin-related database functions

async def get_all_active_chats(pool):
    """Get all active chat connections with user information."""
    async with pool.acquire() as conn:
        chats = await conn.fetch("""
            SELECT 
                cc.id,
                cc.user1_id,
                cc.user2_id,
                cc.created_at,
                u1.name as user1_name,
                u2.name as user2_name
            FROM chat_connections cc
            LEFT JOIN users u1 ON cc.user1_id = u1.user_id
            LEFT JOIN users u2 ON cc.user2_id = u2.user_id
            ORDER BY cc.created_at DESC
        """)
        return chats


async def get_chat_message_count(pool, user1_id: int, user2_id: int):
    """Get message count between two users in message_log."""
    async with pool.acquire() as conn:
        count = await conn.fetchval("""
            SELECT COUNT(*) FROM message_log
            WHERE (sender_id = $1 AND receiver_id = $2) 
               OR (sender_id = $2 AND receiver_id = $1)
        """, user1_id, user2_id)
        return count or 0


async def get_all_muted_users(pool):
    """Get all muted users with their information."""
    async with pool.acquire() as conn:
        muted = await conn.fetch("""
            SELECT 
                mu.user_id,
                mu.muted_until,
                mu.reason,
                mu.created_at,
                u.name,
                u.username
            FROM muted_users mu
            LEFT JOIN users u ON mu.user_id = u.user_id
            WHERE mu.muted_until > CURRENT_TIMESTAMP
            ORDER BY mu.muted_until DESC
        """)
        return muted


async def get_muted_users_count(pool):
    """Get count of currently muted users."""
    async with pool.acquire() as conn:
        count = await conn.fetchval("""
            SELECT COUNT(*) FROM muted_users
            WHERE muted_until > CURRENT_TIMESTAMP
        """)
        return count or 0


async def admin_end_chat_by_id(pool, chat_id: int):
    """End a chat by chat connection ID. Returns (success: bool, user1_id, user2_id)."""
    async with pool.acquire() as conn:
        chat = await conn.fetchrow("""
            SELECT user1_id, user2_id FROM chat_connections WHERE id = $1
        """, chat_id)
        
        if chat:
            await conn.execute("DELETE FROM chat_connections WHERE id = $1", chat_id)
            return True, chat["user1_id"], chat["user2_id"]
        return False, None, None


async def log_admin_action(pool, admin_id: int, action: str, details: str = None):
    """Log an admin action to the database."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO admin_logs (admin_id, action, details)
            VALUES ($1, $2, $3)
        """, admin_id, action, details)
