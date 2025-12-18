"""
Admin handlers module.
Handles admin panel, mute/unmute, broadcast, statistics, and user search functionality.
"""
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import asyncio
import logging

from config import TIMEZONE
from db import is_user_admin
from states import MuteState, BroadcastState, SearchUserState

# Configure logging
logger = logging.getLogger(__name__)

# Create router for admin handlers
admin_router = Router()


@admin_router.message(Command("admin"))
async def admin_panel_entry(message: Message, bot: Bot, dispatcher):
    """Entry point for admin panel - shows main menu."""
    pool = dispatcher["db"]
    user_id = message.from_user.id

    if not await is_user_admin(pool, user_id):
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats")],
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin:users")],
    ])

    await message.answer(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=keyboard
    )


@admin_router.callback_query(F.data == "admin:stats")
async def show_statistics(callback: CallbackQuery, bot: Bot, dispatcher):
    """Display bot statistics (total users, monthly, daily)."""
    pool = dispatcher["db"]

    tashkent_now = datetime.now(ZoneInfo(TIMEZONE))
    today_start = tashkent_now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    month_start = tashkent_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)

    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        today_users = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= $1", today_start
        )
        month_users = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= $1", month_start
        )

    text = (
        "<b>📊 Statistika</b>\n\n"
        f"👥 Umumiy foydalanuvchilar: <b>{total_users}</b>\n"
        f"📅 Oylik qo‘shilganlar: <b>{month_users}</b>\n"
        f"📆 Kunlik qo‘shilganlar: <b>{today_users}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back_to_panel")]
        ])
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:back_to_panel")
async def back_to_main_menu(callback: CallbackQuery):
    """Return to main admin panel menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats")],
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin:users")],
    ])

    await callback.message.edit_text(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=keyboard
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:users")
async def open_users_menu(callback: CallbackQuery):
    """Open users management menu."""
    users_menu = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Foydalanuvchini qidirish", callback_data="admin:search")],
        [InlineKeyboardButton(text="🆕 So‘nggi 10 user", callback_data="admin:recent_users:1")],
        [InlineKeyboardButton(text="⛔ Bloklash / Mute", callback_data="admin:punish")],
        [InlineKeyboardButton(text="🔓 Mute'dan chiqarish", callback_data="admin:unmute")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin:back_to_panel")],
    ])

    await callback.message.edit_text(
        "<b>👥 Foydalanuvchilar bo'limi:</b>\nKerakli funksiyani tanlang:",
        reply_markup=users_menu
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    """Start broadcast message flow."""
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.edit_text(
        "<b>📢 Yubormoqchi bo'lgan xabaringizni yozing:</b>\n"
        "Matn yoki rasm/video bilan matn ham bo'lishi mumkin."
    )
    await callback.answer()


@admin_router.message(BroadcastState.waiting_for_message)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Process and send broadcast message to all users."""
    pool = dispatcher["db"]
    await state.clear()

    await message.answer("<i>⏳ Xabar yuborilmoqda...</i>")

    success = 0
    fail = 0
    batch_size = 30  # Batch size to avoid hitting Telegram rate limits
    delay_between_batches = 1.0  # Delay in seconds between batches

    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users")

    total_users = len(users)
    for i in range(0, total_users, batch_size):
        batch = users[i:i + batch_size]
        tasks = []

        for user in batch:
            # Use bot.copy_message to create a coroutine
            tasks.append(bot.copy_message(
                chat_id=user['user_id'],
                from_chat_id=message.chat.id,
                message_id=message.message_id
            ))

        # Send messages in parallel for this batch
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Failed to send message to user: {result}")
                fail += 1
            else:
                success += 1

        # Progress update every 100 users
        if (i + batch_size) % 100 == 0 or i + batch_size >= total_users:
            await message.answer(
                f"<i>📬 Yuborilmoqda: {min(i + batch_size, total_users)} / {total_users} foydalanuvchi...</i>",
                parse_mode=ParseMode.HTML
            )

        # Delay to respect Telegram rate limits
        if i + batch_size < total_users:
            await asyncio.sleep(delay_between_batches)

    await message.answer(
        f"<b>✅ Broadcast yakunlandi!</b>\n\n"
        f"📬 Yuborildi: <b>{success}</b>\n"
        f"❌ Yuborilmadi: <b>{fail}</b>",
        parse_mode=ParseMode.HTML
    )


@admin_router.callback_query(F.data == "admin:punish")
async def start_mute(callback: CallbackQuery, state: FSMContext):
    """Start mute user flow."""
    await state.set_state(MuteState.waiting_for_user_id)
    await callback.message.edit_text("🆔 Foydalanuvchi ID raqamini yuboring:")
    await callback.answer()


@admin_router.message(MuteState.waiting_for_user_id)
async def get_user_id(message: Message, state: FSMContext):
    """Get user ID for muting."""
    try:
        user_id = int(message.text.strip())
        await state.update_data(user_id=user_id)
        await state.set_state(MuteState.waiting_for_duration)
        await message.answer("⏰ Mute necha daqiqaga bo'lsin? (Masalan: 60)")
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Qayta urinib ko'ring.")


@admin_router.message(MuteState.waiting_for_duration)
async def get_duration(message: Message, state: FSMContext):
    """Get mute duration in minutes."""
    try:
        minutes = int(message.text.strip())
        muted_until = (datetime.now(ZoneInfo(TIMEZONE)) + timedelta(minutes=minutes)).replace(tzinfo=None)
        await state.update_data(muted_until=muted_until)
        await state.set_state(MuteState.waiting_for_reason)
        await message.answer("📝 Sababni yozing:")
    except ValueError:
        await message.answer("❌ Noto'g'ri raqam. Qayta urinib ko'ring.")


@admin_router.message(MuteState.waiting_for_reason)
async def finish_mute(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Complete mute process and save to database."""
    data = await state.get_data()
    await state.clear()

    user_id = data['user_id']
    muted_until = data['muted_until']
    reason = message.text.strip()

    pool = dispatcher["db"]
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO muted_users (user_id, muted_until, reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE
            SET muted_until = $2, reason = $3, created_at = CURRENT_TIMESTAMP
        """, user_id, muted_until, reason)

    await message.answer(
        f"✅ <a href='tg://user?id={user_id}'>Foydalanuvchi</a> {muted_until:%Y-%m-%d %H:%M} gacha mute qilindi.\n"
        f"Sabab: <i>{reason}</i>",
        parse_mode="HTML"
    )


@admin_router.callback_query(F.data == "admin:unmute")
async def ask_user_id_for_unmute(callback: CallbackQuery, state: FSMContext):
    """Start unmute user flow."""
    await state.set_state(MuteState.waiting_for_unmute_id)
    await callback.message.answer("🔓 Mute'dan chiqariladigan foydalanuvchi ID sini kiriting:")
    await callback.answer()


@admin_router.message(MuteState.waiting_for_unmute_id)
async def unmute_user(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Remove user from mute list."""
    user_id = message.text.strip()
    await state.clear()

    pool = dispatcher["db"]
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM muted_users WHERE user_id = $1", int(user_id))

    if result == "DELETE 1":
        await message.answer(
            f"✅ <a href='tg://user?id={user_id}'>Foydalanuvchi</a> mute'dan chiqarildi.",
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Bu foydalanuvchi bazada mute qilinmagan edi.")


@admin_router.callback_query(F.data == "admin:search")
async def ask_user_id(callback: CallbackQuery, state: FSMContext):
    """Start user search flow."""
    await callback.message.edit_text("🔍 Qidirish uchun foydalanuvchi ID sini yuboring:")
    await state.set_state(SearchUserState.waiting_for_user_id)


@admin_router.message(SearchUserState.waiting_for_user_id)
async def show_user_info(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Display user information by ID."""
    await state.clear()
    pool = dispatcher["db"]

    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID format. Iltimos, faqat raqam yuboring.")
        return

    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT user_id, username, name, is_admin, created_at FROM users WHERE user_id = $1",
            user_id
        )
        if not user:
            await message.answer("😕 Bunday foydalanuvchi topilmadi.")
            return

        muted_row = await conn.fetchrow("SELECT muted_until FROM muted_users WHERE user_id = $1", user_id)

    is_muted = bool(muted_row)
    muted_until = muted_row["muted_until"] if muted_row else None

    await message.answer(
        f"👤 <b>Foydalanuvchi haqida:</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📛 Ism: {user['name']}\n"
        f"🗓 Ro'yxatdan o'tgan: {user['created_at']:%Y-%m-%d %H:%M}\n"
        f"🛡 Admin: {'✅' if user['is_admin'] else '❌'}\n"
        f"🔇 Mute: {'✅ ' + muted_until.strftime('%Y-%m-%d %H:%M') if is_muted else '❌'}",
        parse_mode=ParseMode.HTML
    )


@admin_router.callback_query(F.data.startswith("admin:recent_users:"))
async def show_recent_users(callback: CallbackQuery, bot: Bot, dispatcher):
    """Display recent users with pagination."""
    pool = dispatcher["db"]
    page = int(callback.data.split(":")[-1])
    users_per_page = 10
    offset = (page - 1) * users_per_page

    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        users = await conn.fetch(
            "SELECT user_id, name, created_at FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            users_per_page, offset
        )

    total_pages = (total_users + users_per_page - 1) // users_per_page

    text = "<b>🆕 So'nggi foydalanuvchilar:</b>\n\n"
    if not users:
        text += "😕 Foydalanuvchilar topilmadi."
    else:
        for user in users:
            text += f"🆔 <code>{user['user_id']}</code> | {user['name']} | {user['created_at']:%Y-%m-%d %H:%M}\n"

    # Pagination buttons
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"admin:recent_users:{page - 1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"admin:recent_users:{page + 1}"))

    # User selection buttons
    user_buttons = [
        [InlineKeyboardButton(text=f"👤 {user['name']}", callback_data=f"admin:select_user:{user['user_id']}")]
        for user in users
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        *user_buttons,
        buttons,
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:users")]
    ])

    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:select_user:"))
async def select_user(callback: CallbackQuery, bot: Bot, dispatcher):
    """Display detailed information about a selected user."""
    pool = dispatcher["db"]
    user_id = int(callback.data.split(":")[-1])

    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT user_id, username, name, is_admin, created_at FROM users WHERE user_id = $1",
            user_id
        )
        if not user:
            await callback.message.edit_text("😕 Bunday foydalanuvchi topilmadi.", parse_mode=ParseMode.HTML)
            await callback.answer()
            return

        muted_row = await conn.fetchrow("SELECT muted_until FROM muted_users WHERE user_id = $1", user_id)

    is_muted = bool(muted_row)
    muted_until = muted_row["muted_until"] if muted_row else None

    text = (
        f"👤 <b>Foydalanuvchi haqida:</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📛 Ism: {user['name']}\n"
        f"🗓 Ro'yxatdan o'tgan: {user['created_at']:%Y-%m-%d %H:%M}\n"
        f"🛡 Admin: {'✅' if user['is_admin'] else '❌'}\n"
        f"🔇 Mute: {'✅ ' + muted_until.strftime('%Y-%m-%d %H:%M') if is_muted else '❌'}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:recent_users:1")]
    ])

    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer()

