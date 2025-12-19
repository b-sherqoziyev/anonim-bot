"""
Database module for managing database connections and operations.
Handles database pool creation and all database helper functions.
"""
import asyncpg
from datetime import datetime
from zoneinfo import ZoneInfo
from config import DATABASE_URL, TIMEZONE

# Valid plan types
VALID_PLANS = {
    '1_month': '1 month',
    '3_months': '3 months',
    '6_months': '6 months',
    '1_year': '1 year'
}

# Plan prices (in so'm)
PLAN_PRICES = {
    '1_month': 5000.00,  # TODO: Set actual prices
    '3_months': 12000.00,
    '6_months': 25000.00,
    '1_year': 50000.00
}

PAYMENT_STATUSES = {
    'pending': '⏳ Kutilmoqda',
    'processing': '🔄 Jarayonda',
    'completed': '✅ Muvaffaqiyatli',
    'failed': '❌ Muvaffaqiyatsiz',
    'cancelled': '🚫 Bekor qilindi',
    'refunded': '↩️ Qaytarildi',
    'expired': '⌛ Muddati o\'tgan',
    'on_hold': '⏸️ To\'xtatilgan'
}

# Valid payment methods
VALID_PAYMENT_METHODS = ['balance']  # 'balance' for internal subscription payments

# Payment method display names
PAYMENT_METHOD_NAMES = {
    'balance': 'Balans',
    'click': 'Click',  # Kept for backward compatibility with existing data
    'payme': 'Payme',  # Kept for backward compatibility with existing data
    'paynet': 'Paynet'  # Kept for backward compatibility with existing data
}


async def init_db():
    """
    Initialize database connection pool and create tables if they don't exist.
    Returns the connection pool.
    """
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        # Create plan enum type if it doesn't exist (for subscriptions table)
        await conn.execute("""
            DO $$ BEGIN
                CREATE TYPE plan_type AS ENUM ('1_month', '3_months', '6_months', '1_year');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """)

        # Create payment_status enum type if it doesn't exist
        await conn.execute("""
            DO $$ BEGIN
                CREATE TYPE payment_status AS ENUM (
                    'pending', 'processing', 'completed', 'failed', 
                    'cancelled', 'refunded', 'expired', 'on_hold'
                );
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """)

        # Create payment_method enum type if it doesn't exist
        # First try to add 'balance' to existing enum if it exists
        await conn.execute("""
            DO $$ 
            BEGIN
                -- Try to add 'balance' to existing enum if it exists
                IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_method') THEN
                    -- Check if 'balance' already exists
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_enum 
                        WHERE enumlabel = 'balance' 
                        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'payment_method')
                    ) THEN
                        ALTER TYPE payment_method ADD VALUE 'balance';
                    END IF;
                ELSE
                    -- Create new enum with all values (including old ones for backward compatibility)
                    CREATE TYPE payment_method AS ENUM ('click', 'payme', 'paynet', 'balance');
                END IF;
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $$;
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                name TEXT,
                token TEXT UNIQUE,
                is_admin BOOLEAN DEFAULT FALSE,
                is_superuser BOOLEAN DEFAULT FALSE,
                is_premium BOOLEAN DEFAULT FALSE,
                balance NUMERIC(10, 2) DEFAULT 0.00,
                total_deposited NUMERIC(10, 2) DEFAULT 0.00,
                is_hidden BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Add new columns to existing tables (migration for existing databases)
        # PostgreSQL doesn't support IF NOT EXISTS in ALTER TABLE, so we check first
        columns_to_add = [
            ('is_premium', 'BOOLEAN DEFAULT FALSE'),
            ('balance', 'NUMERIC(10, 2) DEFAULT 0.00'),
            ('total_deposited', 'NUMERIC(10, 2) DEFAULT 0.00'),
            ('referral_code', 'TEXT UNIQUE'),
            ('referral_by', 'BIGINT REFERENCES users(user_id)'),
            ('is_hidden', 'BOOLEAN DEFAULT FALSE'),
        ]

        for column_name, column_def in columns_to_add:
            try:
                # Check if column exists
                column_exists = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT 1 
                        FROM information_schema.columns 
                        WHERE table_name = 'users' AND column_name = $1
                    );
                """, column_name)

                if not column_exists:
                    await conn.execute(f"""
                        ALTER TABLE users 
                        ADD COLUMN {column_name} {column_def};
                    """)
            except Exception as e:
                # Log error but continue (column might already exist)
                print(f"Warning: Could not add column {column_name}: {e}")

        # Remove plan column from users table if it exists (migration)
        try:
            plan_column_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'users' AND column_name = 'plan'
                );
            """)

            if plan_column_exists:
                await conn.execute("""
                    ALTER TABLE users 
                    DROP COLUMN IF EXISTS plan;
                """)
        except Exception as e:
            print(f"Warning: Could not remove plan column: {e}")

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments(
                id              SERIAL PRIMARY KEY,
                user_id         BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
                amount          NUMERIC(10, 2) NOT NULL,
                method          payment_method NOT NULL,
                status          payment_status NOT NULL DEFAULT 'pending',
                transaction_id  TEXT UNIQUE,
                merchant_data   TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Add transaction_id column if it doesn't exist (migration)
        try:
            transaction_id_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'payments' AND column_name = 'transaction_id'
                );
            """)

            if not transaction_id_exists:
                await conn.execute("""
                    ALTER TABLE payments 
                    ADD COLUMN transaction_id TEXT;
                """)
                await conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS payments_transaction_id_unique 
                    ON payments(transaction_id) WHERE transaction_id IS NOT NULL;
                """)
        except Exception as e:
            print(f"Warning: Could not add transaction_id column: {e}")

        # Add merchant_data column if it doesn't exist (migration)
        try:
            merchant_data_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'payments' AND column_name = 'merchant_data'
                );
            """)

            if not merchant_data_exists:
                await conn.execute("""
                    ALTER TABLE payments 
                    ADD COLUMN merchant_data TEXT;
                """)
        except Exception as e:
            print(f"Warning: Could not add merchant_data column: {e}")

        # Migrate existing payments table status column from TEXT to payment_status enum
        try:
            status_column_type = await conn.fetchval("""
                SELECT udt_name 
                FROM information_schema.columns 
                WHERE table_name = 'payments' AND column_name = 'status';
            """)

            if status_column_type == 'text':
                # Update any invalid values to 'pending'
                await conn.execute("""
                    UPDATE payments 
                    SET status = 'pending' 
                    WHERE status IS NOT NULL 
                    AND status NOT IN ('pending', 'processing', 'completed', 'failed', 
                                       'cancelled', 'refunded', 'expired', 'on_hold');
                """)

                # Create a temporary column with the new type
                await conn.execute("""
                    ALTER TABLE payments 
                    ADD COLUMN status_new payment_status;
                """)

                # Copy valid values
                await conn.execute("""
                    UPDATE payments 
                    SET status_new = status::payment_status 
                    WHERE status IS NOT NULL;
                """)

                # Set default for NULL values
                await conn.execute("""
                    UPDATE payments 
                    SET status_new = 'pending' 
                    WHERE status_new IS NULL;
                """)

                # Drop old column and rename new one
                await conn.execute("""
                    ALTER TABLE payments 
                    DROP COLUMN status;
                """)

                await conn.execute("""
                    ALTER TABLE payments 
                    RENAME COLUMN status_new TO status;
                """)

                # Set NOT NULL constraint and default
                await conn.execute("""
                    ALTER TABLE payments 
                    ALTER COLUMN status SET NOT NULL,
                    ALTER COLUMN status SET DEFAULT 'pending';
                """)
        except Exception as e:
            # Log error but continue (column might already be correct type)
            print(f"Warning: Could not migrate payments status column: {e}")

        # Migrate existing payments table method column from TEXT to payment_method enum
        try:
            method_column_type = await conn.fetchval("""
                SELECT udt_name 
                FROM information_schema.columns 
                WHERE table_name = 'payments' AND column_name = 'method';
            """)

            if method_column_type == 'text':
                # Update any invalid values to 'balance' (default for new system)
                await conn.execute("""
                    UPDATE payments 
                    SET method = 'balance' 
                    WHERE method IS NOT NULL 
                    AND method NOT IN ('click', 'payme', 'paynet', 'balance');
                """)

                # Create a temporary column with the new type
                await conn.execute("""
                    ALTER TABLE payments 
                    ADD COLUMN method_new payment_method;
                """)

                # Copy valid values (including 'balance' if it exists)
                await conn.execute("""
                    UPDATE payments 
                    SET method_new = method::payment_method 
                    WHERE method IS NOT NULL 
                    AND method IN ('click', 'payme', 'paynet', 'balance');
                """)

                # Set default for NULL or invalid values to 'balance'
                await conn.execute("""
                    UPDATE payments 
                    SET method_new = 'balance' 
                    WHERE method_new IS NULL;
                """)

                # Drop old column and rename new one
                await conn.execute("""
                    ALTER TABLE payments 
                    DROP COLUMN method;
                """)

                await conn.execute("""
                    ALTER TABLE payments 
                    RENAME COLUMN method_new TO method;
                """)

                # Set NOT NULL constraint
                await conn.execute("""
                    ALTER TABLE payments 
                    ALTER COLUMN method SET NOT NULL;
                """)
        except Exception as e:
            # Log error but continue (column might already be correct type)
            print(f"Warning: Could not migrate payments method column: {e}")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions(
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
                plan        plan_type NOT NULL,
                start_date  TIMESTAMP NOT NULL,
                end_date    TIMESTAMP NOT NULL,
                is_active   BOOLEAN DEFAULT TRUE,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    return pool


async def get_user_by_token(pool, token: str):
    """Get user information by their unique token."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT user_id FROM users WHERE token = $1", token)


async def is_user_banned(pool, user_id: int) -> tuple[bool, datetime | None]:
    """
    Check if a user is currently banned.
    Returns (is_banned: bool, banned_until: datetime | None).
    Automatically removes expired ban records.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT muted_until FROM muted_users WHERE user_id = $1", user_id)
        if row:
            banned_until = row["muted_until"]
            current_time = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
            if banned_until > current_time:
                return True, banned_until
            else:
                await conn.execute("DELETE FROM muted_users WHERE user_id = $1", user_id)
        return False, None


async def is_user_admin(pool, user_id: int) -> bool:
    """Check if a user has admin privileges."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_admin FROM users WHERE user_id = $1", user_id)
        return bool(row and row['is_admin'])


async def is_user_premium(pool, user_id: int) -> bool:
    """
    Check if a user has premium status.
    Returns True if user exists and is_premium is True, False otherwise.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_premium FROM users WHERE user_id = $1", user_id)
        return bool(row and row['is_premium'])


async def get_all_admin_ids(pool):
    """Get all admin user IDs."""
    async with pool.acquire() as conn:
        admin_ids = await conn.fetch("SELECT user_id FROM users WHERE is_admin = TRUE")
        return [row['user_id'] for row in admin_ids]


async def update_user_info(pool, user_id: int, username: str, name: str):
    """
    Update user's username and name in the database if they've changed.
    This should be called whenever we receive a message from a user to keep data up to date.
    """
    async with pool.acquire() as conn:
        # Check current values
        current = await conn.fetchrow("""
            SELECT username, name FROM users WHERE user_id = $1
        """, user_id)
        
        if not current:
            # User doesn't exist, nothing to update
            return
        
        # Update if values have changed
        if current['username'] != username or current['name'] != name:
            await conn.execute("""
                UPDATE users 
                SET username = $1, name = $2 
                WHERE user_id = $3
            """, username, name, user_id)


async def set_user_hidden(pool, user_id: int) -> bool:
    """
    Set is_hidden to True for a user. Only works if user is premium.
    Returns True if successful, False if user is not premium.
    """
    async with pool.acquire() as conn:
        # Check if user is premium
        is_premium = await conn.fetchval("""
            SELECT is_premium FROM users WHERE user_id = $1
        """, user_id)
        
        if not is_premium:
            return False
        
        # Update is_hidden to True
        await conn.execute("""
            UPDATE users 
            SET is_hidden = TRUE
            WHERE user_id = $1
        """, user_id)
        
        return True


async def notify_admins_new_user(pool, bot, new_user_id: int, username: str, name: str):
    """
    Notify all admins when a new user joins the bot.
    """
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    try:
        admin_ids = await get_all_admin_ids(pool)
        if not admin_ids:
            return
        
        # Get user info
        user_link = f"tg://user?id={new_user_id}"
        username_text = f"@{username}" if username else "Yo'q"
        
        notification_text = (
            f"🆕 <b>Yangi foydalanuvchi qo'shildi!</b>\n\n"
            f"👤 <b>Ism:</b> {name}\n"
            f"🆔 <b>ID:</b> <code>{new_user_id}</code>\n"
            f"📱 <b>Username:</b> {username_text}\n"
            f"📅 <b>Vaqt:</b> {datetime.now(ZoneInfo(TIMEZONE)).strftime('%Y-%m-%d %H:%M')}"
        )
        
        # Try to create keyboard with profile link, but handle privacy restrictions
        from aiogram.exceptions import TelegramBadRequest
        
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👤 Profilni ko'rish", url=user_link)],
                [InlineKeyboardButton(text="📊 Ma'lumotlarni ko'rish", callback_data=f"admin:select_user:{new_user_id}")]
            ])
            has_profile_button = True
        except:
            # If button creation fails, create keyboard without profile button
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Ma'lumotlarni ko'rish", callback_data=f"admin:select_user:{new_user_id}")]
            ])
            has_profile_button = False
        
        # Send notification to all admins
        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
            except TelegramBadRequest as e:
                # If privacy restricted, try without profile button
                error_str = str(e).upper()
                if "BUTTON_USER_PRIVACY_RESTRICTED" in error_str or "PRIVACY_RESTRICTED" in error_str:
                    if has_profile_button:
                        # Retry without profile button
                        keyboard_no_profile = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📊 Ma'lumotlarni ko'rish", callback_data=f"admin:select_user:{new_user_id}")]
                        ])
                        try:
                            await bot.send_message(
                                chat_id=admin_id,
                                text=notification_text,
                                parse_mode='HTML',
                                reply_markup=keyboard_no_profile
                            )
                        except Exception as e2:
                            print(f"Error notifying admin {admin_id}: {e2}")
                    else:
                        print(f"Error notifying admin {admin_id}: {e}")
                else:
                    print(f"Error notifying admin {admin_id}: {e}")
            except Exception as e:
                # Log error but continue with other admins
                print(f"Error notifying admin {admin_id}: {e}")
    except Exception as e:
        print(f"Error in notify_admins_new_user: {e}")


async def get_user_balance_info(pool, user_id: int):
    """
    Get user's balance and total deposited amount.
    Returns (balance: float, total_deposited: float) or (None, None) if user doesn't exist.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT balance, total_deposited FROM users WHERE user_id = $1",
            user_id
        )
        if row:
            balance = float(row['balance']) if row['balance'] else 0.00
            total_deposited = float(row['total_deposited']) if row['total_deposited'] else 0.00
            return balance, total_deposited
        return None, None


async def get_user_premium_info(pool, user_id: int):
    """
    Get user's premium status and subscription information.
    Returns dict with: is_premium, balance, subscription (if active), or None if user doesn't exist.
    """
    async with pool.acquire() as conn:
        # Get user info
        user_row = await conn.fetchrow(
            "SELECT is_premium, balance FROM users WHERE user_id = $1",
            user_id
        )

        if not user_row:
            return None

        is_premium = bool(user_row['is_premium'])
        balance = float(user_row['balance']) if user_row['balance'] else 0.00

        result = {
            'is_premium': is_premium,
            'balance': balance
        }

        # Get active subscription if premium
        if is_premium:
            subscription = await conn.fetchrow("""
                SELECT plan, start_date, end_date, is_active
                FROM subscriptions
                WHERE user_id = $1 AND is_active = TRUE
                ORDER BY end_date DESC
                LIMIT 1
            """, user_id)

            if subscription:
                result['subscription'] = {
                    'plan': subscription['plan'],
                    'start_date': subscription['start_date'],
                    'end_date': subscription['end_date'],
                    'is_active': bool(subscription['is_active'])
                }

        return result


async def get_plan_price(plan: str) -> float:
    """Get price for a plan. Returns 0.0 if plan not found."""
    return PLAN_PRICES.get(plan, 0.0)


async def create_payment(pool, user_id: int, amount: float, method: str, transaction_id: str = None,
                         merchant_data: str = None):
    """
    Create a new payment record.
    method must be 'balance' (for internal subscription payments)
    Returns payment ID or None if failed.
    """
    # Validate payment method - only 'balance' is allowed for new payments
    if method != 'balance':
        print(f"Invalid payment method: {method}. Only 'balance' is allowed for new payments.")
        return None

    async with pool.acquire() as conn:
        try:
            payment_id = await conn.fetchval("""
                INSERT INTO payments (user_id, amount, method, status, transaction_id, merchant_data)
                VALUES ($1, $2, $3::payment_method, 'pending', $4, $5)
                RETURNING id
            """, user_id, amount, method, transaction_id, merchant_data)
            return payment_id
        except Exception as e:
            print(f"Error creating payment: {e}")
            return None


async def check_transaction_id_exists(pool, transaction_id: str) -> bool:
    """Check if transaction_id already exists (prevent duplicate callbacks)."""
    if not transaction_id:
        return False
    async with pool.acquire() as conn:
        exists = await conn.fetchval("""
            SELECT EXISTS(SELECT 1 FROM payments WHERE transaction_id = $1)
        """, transaction_id)
        return bool(exists)


async def update_payment_status(pool, payment_id: int, status: str, transaction_id: str = None):
    """Update payment status."""
    async with pool.acquire() as conn:
        if transaction_id:
            await conn.execute("""
                UPDATE payments 
                SET status = $1, transaction_id = $2 
                WHERE id = $3
            """, status, transaction_id, payment_id)
        else:
            await conn.execute("""
                UPDATE payments 
                SET status = $1 
                WHERE id = $2
            """, status, payment_id)


async def update_user_balance(pool, user_id: int, amount: float, add_to_total: bool = False):
    """
    Update user balance.
    If add_to_total is True, also add to total_deposited.
    Returns True if successful.
    """
    async with pool.acquire() as conn:
        try:
            if add_to_total:
                await conn.execute("""
                    UPDATE users 
                    SET balance = balance + $1, total_deposited = total_deposited + $1 
                    WHERE user_id = $2
                """, amount, user_id)
            else:
                await conn.execute("""
                    UPDATE users 
                    SET balance = balance + $1 
                    WHERE user_id = $2
                """, amount, user_id)
            return True
        except Exception as e:
            print(f"Error updating user balance: {e}")
            return False


async def activate_subscription(pool, user_id: int, plan: str):
    """
    Activate or extend a subscription for a user.
    If user has active subscription, extends it. Otherwise creates new one.
    Calculates end_date based on plan and sets is_premium to True.
    Returns (success: bool, subscription_id: int | None)
    """
    if plan not in VALID_PLANS:
        return False, None

    from datetime import timedelta

    async with pool.acquire() as conn:
        try:
            current_time = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)

            # Calculate days to add based on plan
            if plan == '1_month':
                days_to_add = 30
            elif plan == '3_months':
                days_to_add = 90
            elif plan == '6_months':
                days_to_add = 180
            elif plan == '1_year':
                days_to_add = 365
            else:
                return False, None

            # Check if user has active subscription
            active_sub = await conn.fetchrow("""
                SELECT id, end_date 
                FROM subscriptions 
                WHERE user_id = $1 AND is_active = TRUE 
                ORDER BY end_date DESC 
                LIMIT 1
            """, user_id)

            if active_sub:
                # Extend existing subscription
                existing_end_date = active_sub['end_date']
                
                # Ensure both datetimes are timezone-naive for comparison
                if existing_end_date.tzinfo is not None:
                    existing_end_date = existing_end_date.replace(tzinfo=None)

                # If subscription hasn't expired, extend from end_date, otherwise from now
                if existing_end_date > current_time:
                    new_end_date = existing_end_date + timedelta(days=days_to_add)
                    start_date = existing_end_date
                else:
                    new_end_date = current_time + timedelta(days=days_to_add)
                    start_date = current_time

                # Update existing subscription
                await conn.execute("""
                    UPDATE subscriptions 
                    SET plan = $1, end_date = $2, start_date = $3
                    WHERE id = $4
                """, plan, new_end_date.replace(tzinfo=None), start_date.replace(tzinfo=None), active_sub['id'])

                subscription_id = active_sub['id']
            else:
                # Create new subscription
                start_date = current_time
                end_date = start_date + timedelta(days=days_to_add)

                subscription_id = await conn.fetchval("""
                    INSERT INTO subscriptions (user_id, plan, start_date, end_date, is_active)
                    VALUES ($1, $2, $3, $4, TRUE)
                    RETURNING id
                """, user_id, plan, start_date.replace(tzinfo=None), end_date.replace(tzinfo=None))

            # Set user as premium
            await conn.execute("""
                UPDATE users 
                SET is_premium = TRUE 
                WHERE user_id = $1
            """, user_id)

            return True, subscription_id
        except Exception as e:
            print(f"Error activating subscription: {e}")
            return False, None


async def log_message(pool, sender_id, receiver_id, text):
    """Log a message to the message_log table with Tashkent timezone."""
    tashkent_time = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO message_log (sender_id, receiver_id, message, sent_at)
            VALUES ($1, $2, $3, $4)
        """, sender_id, receiver_id, text, tashkent_time)


async def generate_referral_code(pool, user_id: int) -> str:
    """
    Generate a unique referral code for a user.
    Returns existing code if user already has one, otherwise generates new.
    """
    import secrets
    import string
    
    async with pool.acquire() as conn:
        # Check if user already has a referral code
        existing_code = await conn.fetchval("""
            SELECT referral_code FROM users WHERE user_id = $1
        """, user_id)
        
        if existing_code:
            return existing_code
        
        # Generate new unique code (8 characters, alphanumeric uppercase)
        while True:
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            # Check if code already exists
            exists = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM users WHERE referral_code = $1)
            """, code)
            if not exists:
                # Save the code
                await conn.execute("""
                    UPDATE users SET referral_code = $1 WHERE user_id = $2
                """, code, user_id)
                return code


async def get_user_referral_code(pool, user_id: int) -> str | None:
    """Get user's referral code. Returns None if not exists."""
    async with pool.acquire() as conn:
        code = await conn.fetchval("""
            SELECT referral_code FROM users WHERE user_id = $1
        """, user_id)
        return code


async def get_user_by_referral_code(pool, referral_code: str):
    """Get user by referral code. Returns user dict or None."""
    async with pool.acquire() as conn:
        user = await conn.fetchrow("""
            SELECT user_id, username, name FROM users WHERE referral_code = $1
        """, referral_code)
        return dict(user) if user else None


async def process_referral(pool, new_user_id: int, referral_code: str, bot=None) -> bool:
    """
    Process a referral when a new user joins via referral code.
    Returns True if referral was processed, False if user already existed or invalid code.
    """
    async with pool.acquire() as conn:
        # Check if user already exists (was created before)
        user_exists = await conn.fetchval("""
            SELECT EXISTS(SELECT 1 FROM users WHERE user_id = $1)
        """, new_user_id)
        
        # Get user info to check if they're new
        user_info = await conn.fetchrow("""
            SELECT created_at, referral_by FROM users WHERE user_id = $1
        """, new_user_id)
        
        if not user_info:
            # User doesn't exist - shouldn't happen if called after get_or_create_user
            return False
        
        # If user already has a referrer, don't process (prevent duplicate)
        if user_info['referral_by']:
            return False
        
        # Check if user was created more than a minute ago (not a new referral)
        from datetime import datetime, timedelta
        if user_info['created_at']:
            time_diff = datetime.now() - user_info['created_at']
            if time_diff > timedelta(minutes=1):
                # User was created before, not a new referral
                return False
        
        # Get referrer by code
        referrer = await get_user_by_referral_code(pool, referral_code)
        if not referrer:
            return False
        
        referrer_id = referrer['user_id']
        
        # Don't allow self-referral
        if referrer_id == new_user_id:
            return False
        
        # Set referral_by for new user
        await conn.execute("""
            UPDATE users SET referral_by = $1 WHERE user_id = $2
        """, referrer_id, new_user_id)
        
        # Add 10 soums to referrer's balance
        await update_user_balance(pool, referrer_id, 10.00, add_to_total=False)
        
        # Send notification to referrer if bot is provided
        if bot:
            try:
                referrer_name = referrer.get('name', 'Foydalanuvchi')
                balance, _ = await get_user_balance_info(pool, referrer_id)
                await bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 <b>Referral bonus!</b>\n\n"
                         f"✅ Sizning taklif havolangiz orqali yangi foydalanuvchi qo'shildi.\n"
                         f"💰 Balansingizga <b>+10 so'm</b> qo'shildi!\n\n"
                         f"💵 Joriy balans: {balance or 0.00:.2f} so'm",
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"Error sending referral notification: {e}")
        
        return True


async def get_user_referral_stats(pool, user_id: int):
    """
    Get referral statistics for a user.
    Returns: (referral_count: int, referral_earnings: float, referral_code: str | None, referred_by: int | None, referrer_name: str | None)
    """
    async with pool.acquire() as conn:
        # Get referral code
        referral_code = await conn.fetchval("""
            SELECT referral_code FROM users WHERE user_id = $1
        """, user_id)
        
        # Count how many users this user referred
        referral_count = await conn.fetchval("""
            SELECT COUNT(*) FROM users WHERE referral_by = $1
        """, user_id)
        
        # Calculate earnings from referrals (10 soums per referral)
        referral_earnings = referral_count * 10.00
        
        # Get who referred this user
        referred_by_info = await conn.fetchrow("""
            SELECT referral_by FROM users WHERE user_id = $1
        """, user_id)
        
        referred_by = referred_by_info['referral_by'] if referred_by_info and referred_by_info['referral_by'] else None
        
        # Get referrer name if exists
        referrer_name = None
        if referred_by:
            referrer_info = await conn.fetchrow("""
                SELECT name FROM users WHERE user_id = $1
            """, referred_by)
            referrer_name = referrer_info['name'] if referrer_info else None
        
        return referral_count, referral_earnings, referral_code, referred_by, referrer_name


async def get_user_payment_history(pool, user_id: int, limit: int = 20):
    """
    Get payment history for a user.
    Returns list of payment records.
    """
    async with pool.acquire() as conn:
        payments = await conn.fetch("""
            SELECT id, amount, method, status, transaction_id, merchant_data, created_at
            FROM payments
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
        """, user_id, limit)
        return [dict(payment) for payment in payments]


async def get_user_full_info(pool, user_id: int):
    """
    Get comprehensive user information for admin panel.
    Returns dict with all user details.
    """
    async with pool.acquire() as conn:
        user = await conn.fetchrow("""
            SELECT 
                user_id, username, name, is_admin, is_superuser, is_premium,
                balance, total_deposited, referral_code, referral_by, created_at
            FROM users 
            WHERE user_id = $1
        """, user_id)
        
        if not user:
            return None
        
        user_dict = dict(user)
        
        # Get subscription info
        subscription = await conn.fetchrow("""
            SELECT plan, start_date, end_date, is_active
            FROM subscriptions
            WHERE user_id = $1 AND is_active = TRUE
            ORDER BY end_date DESC
            LIMIT 1
        """, user_id)
        
        user_dict['subscription'] = dict(subscription) if subscription else None
        
        # Get activity info (last message sent, last login, etc.)
        last_message = await conn.fetchrow("""
            SELECT sent_at FROM message_log
            WHERE sender_id = $1
            ORDER BY sent_at DESC
            LIMIT 1
        """, user_id)
        
        user_dict['last_activity'] = last_message['sent_at'] if last_message else None
        
        # Get referral stats
        referral_count, referral_earnings, referral_code, referred_by, referrer_name = await get_user_referral_stats(pool, user_id)
        user_dict['referral_count'] = referral_count
        user_dict['referral_earnings'] = referral_earnings
        user_dict['referrer_name'] = referrer_name
        
        return user_dict


async def get_or_create_user(pool, user_id: int, username: str, name: str, referral_code: str = None):
    """
    Get user token if exists, otherwise create new user and return token.
    If referral_code is provided and user is new, process the referral.
    Also updates username and name if they've changed.
    Returns (token: str, is_new_user: bool).
    """
    from utils import generate_token

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT token, referral_by, username, name FROM users WHERE user_id = $1", user_id)
        if row:
            # User exists - update username and name if changed
            if row['username'] != username or row['name'] != name:
                await conn.execute("""
                    UPDATE users 
                    SET username = $1, name = $2 
                    WHERE user_id = $3
                """, username, name, user_id)
            return row["token"], False
        else:
            # New user - create them
            # Note: Don't set referral_by here - let process_referral handle it
            # This ensures the bonus is properly added and notification is sent
            token = generate_token()
            tashkent_time = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
            
            await conn.execute(
                "INSERT INTO users (user_id, username, name, token, created_at) VALUES ($1, $2, $3, $4, $5)",
                user_id, username, name, token, tashkent_time
            )
            
            return token, True


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


async def get_all_banned_users(pool):
    """Get all banned users with their information."""
    async with pool.acquire() as conn:
        banned = await conn.fetch("""
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
        return banned


async def get_banned_users_count(pool):
    """Get count of currently banned users."""
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
