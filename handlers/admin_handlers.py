"""
Admin handlers module.
Handles admin panel, ban/unban, broadcast, statistics, live chat monitoring, and settings.
"""
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import asyncio
import logging

from config import TIMEZONE
from db import (
    is_user_admin,
    get_all_active_chats,
    get_chat_message_count,
    get_all_banned_users,
    get_banned_users_count,
    admin_end_chat_by_id,
    log_admin_action,
    end_chat
)
from states import BanState, BroadcastState, SearchUserState

# Configure logging
logger = logging.getLogger(__name__)

# Create router for admin handlers
admin_router = Router()


async def safe_edit_text(callback: CallbackQuery, text: str, reply_markup=None, parse_mode=None):
    """Safely edit message text, handling TelegramBadRequest for unchanged content."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            # Message content hasn't changed, just answer silently
            await callback.answer()
        else:
            raise


def get_main_menu_keyboard():
    """Get main admin panel menu keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats")],
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin:users")],
        [InlineKeyboardButton(text="💬 Live chat monitoring", callback_data="admin:live_chats")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="admin:settings")],
    ])


@admin_router.message(Command("admin"))
async def admin_panel_entry(message: Message, bot: Bot, dispatcher):
    """Entry point for admin panel - shows main menu."""
    pool = dispatcher["db"]
    user_id = message.from_user.id

    if not await is_user_admin(pool, user_id):
        return

    await log_admin_action(pool, user_id, "admin_panel_opened")

    await message.answer(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=get_main_menu_keyboard()
    )


@admin_router.callback_query(F.data == "admin:back_to_panel")
async def back_to_main_menu(callback: CallbackQuery):
    """Return to main admin panel menu."""
    await callback.message.edit_text(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()


# ==================== STATISTICS ====================

@admin_router.callback_query(F.data == "admin:stats")
async def show_statistics(callback: CallbackQuery, bot: Bot, dispatcher):
    """Display bot statistics with all metrics."""
    pool = dispatcher["db"]
    user_id = callback.from_user.id

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
        active_chats_count = await conn.fetchval("SELECT COUNT(*) FROM chat_connections")
        banned_count = await get_banned_users_count(pool)
        queue_count = await conn.fetchval("SELECT COUNT(*) FROM chat_queue")

    text = (
        "<b>📊 Statistika</b>\n\n"
        f"👥 Umumiy foydalanuvchilar: <b>{total_users}</b>\n"
        f"📅 Oylik qo'shilganlar: <b>{month_users}</b>\n"
        f"📆 Kunlik qo'shilganlar: <b>{today_users}</b>\n"
        f"💬 Faol chatlar: <b>{active_chats_count}</b>\n"
        f"⛔ Bloklanganlar: <b>{banned_count}</b>\n"
        f"⏳ Navbatda: <b>{queue_count}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back_to_panel")]
        ])
    )
    await callback.answer()


# ==================== USERS SECTION ====================

@admin_router.callback_query(F.data == "admin:users")
async def open_users_menu(callback: CallbackQuery):
    """Open users management menu."""
    users_menu = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Foydalanuvchini qidirish", callback_data="admin:search")],
        [InlineKeyboardButton(text="🆕 So'nggi 10 user", callback_data="admin:recent_users:1")],
        [InlineKeyboardButton(text="⛔ Bloklanganlar", callback_data="admin:banned_list")],
        [InlineKeyboardButton(text="⛔ Bloklash", callback_data="admin:punish")],
        [InlineKeyboardButton(text="🔓 Blokdan chiqarish", callback_data="admin:unban")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back_to_panel")],
    ])

    await callback.message.edit_text(
        "<b>👥 Foydalanuvchilar bo'limi:</b>\nKerakli funksiyani tanlang:",
        reply_markup=users_menu
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:banned_list")
async def show_banned_users(callback: CallbackQuery, bot: Bot, dispatcher):
    """Show list of banned users."""
    pool = dispatcher["db"]
    banned_users = await get_all_banned_users(pool)

    if not banned_users:
        await callback.message.edit_text(
            "<b>⛔ Bloklangan foydalanuvchilar</b>\n\n"
            "😕 Hozircha bloklangan foydalanuvchilar yo'q.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:users")]
            ])
        )
        await callback.answer()
        return

    text = "<b>⛔ Bloklangan foydalanuvchilar:</b>\n\n"
    buttons = []

    for banned in banned_users:
        user_name = banned['name'] or "Noma'lum"
        text += (
            f"👤 <a href='tg://user?id={banned['user_id']}'>{user_name}</a>\n"
            f"🆔 ID: <code>{banned['user_id']}</code>\n\n"
        )
        button_text = banned['name'] or f"User {banned['user_id']}"
        buttons.append([
            InlineKeyboardButton(
                text=f"🔓 {button_text}",
                callback_data=f"admin:unban_user:{banned['user_id']}"
            )
        ])

    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:users")])

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:unban_user:"))
async def unban_user_from_list(callback: CallbackQuery, bot: Bot, dispatcher):
    """Unban user from the banned users list."""
    pool = dispatcher["db"]
    user_id = int(callback.data.split(":")[-1])
    admin_id = callback.from_user.id

    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM muted_users WHERE user_id = $1", user_id)

    if result == "DELETE 1":
        await log_admin_action(pool, admin_id, "unban_user", f"User ID: {user_id}")
        await callback.answer(f"✅ Foydalanuvchi blokdan chiqarildi!", show_alert=True)
        # Refresh the banned list
        await show_banned_users(callback, bot, dispatcher)
    else:
        await callback.answer("❌ Bu foydalanuvchi bazada bloklanmagan edi.", show_alert=True)


@admin_router.callback_query(F.data == "admin:search")
async def ask_user_id(callback: CallbackQuery, state: FSMContext):
    """Start user search flow."""
    await callback.message.edit_text(
        "🔍 Qidirish uchun foydalanuvchi ID sini yuboring:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin:cancel_search")]
        ])
    )
    await state.set_state(SearchUserState.waiting_for_user_id)
    await callback.answer()


@admin_router.callback_query(F.data == "admin:cancel_search")
async def cancel_search(callback: CallbackQuery, state: FSMContext):
    """Cancel search and return to admin panel."""
    await state.clear()
    await callback.message.edit_text(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer("❌ Qidiruv bekor qilindi.")


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

        banned_row = await conn.fetchrow("SELECT user_id FROM muted_users WHERE user_id = $1", user_id)
        # Check if in active chat
        chat_row = await conn.fetchrow("""
            SELECT user1_id, user2_id FROM chat_connections 
            WHERE user1_id = $1 OR user2_id = $1
        """, user_id)

    is_banned = bool(banned_row)
    in_chat = bool(chat_row)
    partner_id = None
    if chat_row:
        partner_id = chat_row["user2_id"] if chat_row["user1_id"] == user_id else chat_row["user1_id"]

    text = (
        f"👤 <b>Foydalanuvchi haqida:</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📛 Ism: {user['name']}\n"
        f"🗓 Ro'yxatdan o'tgan: {user['created_at']:%Y-%m-%d %H:%M}\n"
        f"🛡 Admin: {'✅' if user['is_admin'] else '❌'}\n"
        f"🔇 Blok: {'✅' if is_banned else '❌'}\n"
        f"💬 Chatda: {'✅' if in_chat else '❌'}"
    )

    buttons = []
    if is_banned:
        buttons.append([InlineKeyboardButton(
            text="🔓 Blokdan chiqarish",
            callback_data=f"admin:unban_user:{user_id}"
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="⛔ Bloklash",
            callback_data=f"admin:ban_user:{user_id}"
        )])

    if in_chat and partner_id:
        buttons.append([InlineKeyboardButton(
            text="💬 Chatni tugatish",
            callback_data=f"admin:end_user_chat:{user_id}"
        )])

    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:users")])

    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@admin_router.callback_query(F.data.startswith("admin:ban_user:"))
async def ban_user_from_info(callback: CallbackQuery, bot: Bot, dispatcher):
    """Ban a specific user immediately."""
    pool = dispatcher["db"]
    user_id = int(callback.data.split(":")[-1])
    admin_id = callback.from_user.id
    
    # Set muted_until to a far future date (e.g., 100 years from now)
    muted_until = (datetime.now(ZoneInfo(TIMEZONE)) + timedelta(days=36500)).replace(tzinfo=None)
    
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO muted_users (user_id, muted_until, reason)
            VALUES ($1, $2, NULL)
            ON CONFLICT (user_id) DO UPDATE
            SET muted_until = $2, reason = NULL, created_at = CURRENT_TIMESTAMP
        """, user_id, muted_until)

    await log_admin_action(pool, admin_id, "ban_user", f"User ID: {user_id}")
    await callback.answer(f"✅ Foydalanuvchi bloklandi!", show_alert=True)
    
    # Refresh the user info display
    await select_user(callback, bot, dispatcher)


@admin_router.callback_query(F.data.startswith("admin:end_user_chat:"))
async def end_user_chat_from_info(callback: CallbackQuery, bot: Bot, dispatcher):
    """End chat for a specific user."""
    pool = dispatcher["db"]
    user_id = int(callback.data.split(":")[-1])
    admin_id = callback.from_user.id

    ended, partner_id = await end_chat(pool, user_id)

    if ended and partner_id:
        await log_admin_action(pool, admin_id, "end_chat", f"Ended chat between {user_id} and {partner_id}")
        try:
            await bot.send_message(user_id, "✅ Chat admin tomonidan tugatildi.")
            await bot.send_message(partner_id, "✅ Chat admin tomonidan tugatildi.")
        except:
            pass
        await callback.answer("✅ Chat tugatildi!", show_alert=True)
        await callback.message.delete()
    else:
        await callback.answer("❌ Chat topilmadi.", show_alert=True)


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
    """Display detailed information about a selected user with action buttons."""
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

        banned_row = await conn.fetchrow("SELECT user_id FROM muted_users WHERE user_id = $1", user_id)
        chat_row = await conn.fetchrow("""
            SELECT user1_id, user2_id FROM chat_connections 
            WHERE user1_id = $1 OR user2_id = $1
        """, user_id)

    is_banned = bool(banned_row)
    in_chat = bool(chat_row)
    partner_id = None
    if chat_row:
        partner_id = chat_row["user2_id"] if chat_row["user1_id"] == user_id else chat_row["user1_id"]

    text = (
        f"👤 <b>Foydalanuvchi haqida:</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📛 Ism: {user['name']}\n"
        f"🗓 Ro'yxatdan o'tgan: {user['created_at']:%Y-%m-%d %H:%M}\n"
        f"🛡 Admin: {'✅' if user['is_admin'] else '❌'}\n"
        f"🔇 Blok: {'✅' if is_banned else '❌'}\n"
        f"💬 Chatda: {'✅' if in_chat else '❌'}"
    )

    buttons = []
    if is_banned:
        buttons.append([InlineKeyboardButton(
            text="🔓 Blokdan chiqarish",
            callback_data=f"admin:unban_user:{user_id}"
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="⛔ Bloklash",
            callback_data=f"admin:ban_user:{user_id}"
        )])

    if in_chat and partner_id:
        buttons.append([InlineKeyboardButton(
            text="💬 Chatni tugatish",
            callback_data=f"admin:end_user_chat:{user_id}"
        )])

    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:recent_users:1")])

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


# ==================== BAN/UNBAN ====================

@admin_router.callback_query(F.data == "admin:punish")
async def start_ban(callback: CallbackQuery, state: FSMContext):
    """Start ban user flow."""
    await state.set_state(BanState.waiting_for_user_id)
    await callback.message.edit_text(
        "🆔 Foydalanuvchi ID raqamini yuboring:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin:cancel_ban")]
        ])
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:cancel_ban")
async def cancel_ban(callback: CallbackQuery, state: FSMContext):
    """Cancel ban and return to admin panel."""
    await state.clear()
    await callback.message.edit_text(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer("❌ Bloklash bekor qilindi.")


@admin_router.message(BanState.waiting_for_user_id)
async def get_user_id(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Get user ID for banning and ban immediately."""
    try:
        user_id = int(message.text.strip())
        await state.clear()
        admin_id = message.from_user.id

        pool = dispatcher["db"]
        # Set muted_until to a far future date (e.g., 100 years from now)
        muted_until = (datetime.now(ZoneInfo(TIMEZONE)) + timedelta(days=36500)).replace(tzinfo=None)
        
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO muted_users (user_id, muted_until, reason)
                VALUES ($1, $2, NULL)
                ON CONFLICT (user_id) DO UPDATE
                SET muted_until = $2, reason = NULL, created_at = CURRENT_TIMESTAMP
            """, user_id, muted_until)

        await log_admin_action(pool, admin_id, "ban_user", f"User ID: {user_id}")

        await message.answer(
            f"✅ <a href='tg://user?id={user_id}'>Foydalanuvchi</a> bloklandi.",
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Qayta urinib ko'ring.")


@admin_router.callback_query(F.data == "admin:unban")
async def ask_user_id_for_unban(callback: CallbackQuery, state: FSMContext):
    """Start unban user flow."""
    await state.set_state(BanState.waiting_for_unban_id)
    await callback.message.edit_text(
        "🔓 Blokdan chiqariladigan foydalanuvchi ID sini kiriting:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin:cancel_unban")]
        ])
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:cancel_unban")
async def cancel_unban(callback: CallbackQuery, state: FSMContext):
    """Cancel unban and return to admin panel."""
    await state.clear()
    await callback.message.edit_text(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer("❌ Blokdan chiqarish bekor qilindi.")


@admin_router.message(BanState.waiting_for_unban_id)
async def unban_user(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Remove user from ban list."""
    user_id = message.text.strip()
    await state.clear()
    admin_id = message.from_user.id

    pool = dispatcher["db"]
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM muted_users WHERE user_id = $1", int(user_id))

    if result == "DELETE 1":
        await log_admin_action(pool, admin_id, "unban_user", f"User ID: {user_id}")
        await message.answer(
            f"✅ <a href='tg://user?id={user_id}'>Foydalanuvchi</a> blokdan chiqarildi.",
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Bu foydalanuvchi bazada bloklanmagan edi.")


# ==================== BROADCAST ====================

@admin_router.callback_query(F.data == "admin:broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    """Start broadcast message flow."""
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.edit_text(
        "<b>📢 Yubormoqchi bo'lgan xabaringizni yozing:</b>\n"
        "Matn yoki rasm/video bilan matn ham bo'lishi mumkin.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin:cancel_broadcast")]
        ])
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Cancel broadcast and return to admin panel."""
    await state.clear()
    await callback.message.edit_text(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer("❌ Broadcast bekor qilindi.")


@admin_router.message(BroadcastState.waiting_for_message)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Process and send broadcast message to all users."""
    pool = dispatcher["db"]
    admin_id = message.from_user.id
    await state.clear()

    # Show preview
    preview_text = "<b>📢 Broadcast ko'rinishi:</b>\n\n"
    if message.text:
        preview_text += message.text
    else:
        preview_text += "Media xabar (rasm/video/voice/document)"

    await message.answer(preview_text)
    await message.answer("<i>⏳ Xabar yuborilmoqda...</i>")

    success = 0
    fail = 0
    batch_size = 30
    delay_between_batches = 1.0

    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users")

    total_users = len(users)
    for i in range(0, total_users, batch_size):
        batch = users[i:i + batch_size]
        tasks = []

        for user in batch:
            tasks.append(bot.copy_message(
                chat_id=user['user_id'],
                from_chat_id=message.chat.id,
                message_id=message.message_id
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Failed to send message to user: {result}")
                fail += 1
            else:
                success += 1

        if (i + batch_size) % 100 == 0 or i + batch_size >= total_users:
            await message.answer(
                f"<i>📬 Yuborilmoqda: {min(i + batch_size, total_users)} / {total_users} foydalanuvchi...</i>",
                parse_mode=ParseMode.HTML
            )

        if i + batch_size < total_users:
            await asyncio.sleep(delay_between_batches)

    await log_admin_action(
        pool, admin_id, "broadcast",
        f"Sent to {success} users, failed: {fail}"
    )

    await message.answer(
        f"<b>✅ Broadcast yakunlandi!</b>\n\n"
        f"📬 Yuborildi: <b>{success}</b>\n"
        f"❌ Yuborilmadi: <b>{fail}</b>",
        parse_mode=ParseMode.HTML
    )


# ==================== LIVE CHAT MONITORING ====================

@admin_router.callback_query(F.data == "admin:live_chats")
async def show_live_chats(callback: CallbackQuery, bot: Bot, dispatcher):
    """Display all active live chats."""
    pool = dispatcher["db"]
    active_chats = await get_all_active_chats(pool)

    if not active_chats:
        await safe_edit_text(
            callback,
            "<b>💬 Live chat monitoring</b>\n\n"
            "😕 Hozircha faol chatlar yo'q.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Yangilash", callback_data="admin:live_chats")],
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back_to_panel")]
            ]),
            parse_mode=ParseMode.HTML
        )
        await callback.answer()
        return

    text = f"<b>💬 Live chat monitoring</b>\n\n"
    text += f"📊 Faol chatlar soni: <b>{len(active_chats)}</b>\n\n"

    buttons = []
    for chat in active_chats:
        user1_name = chat["user1_name"] or f"User {chat['user1_id']}"
        user2_name = chat["user2_name"] or f"User {chat['user2_id']}"
        created_at = chat["created_at"].strftime("%Y-%m-%d %H:%M")

        text += (
            f"💬 Chat #{chat['id']}\n"
            f"👤 {user1_name} ↔️ {user2_name}\n"
            f"🕒 Boshlangan: {created_at}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=f"💬 Chat #{chat['id']} - Tugatish",
                callback_data=f"admin:end_chat:{chat['id']}"
            )
        ])

    buttons.extend([
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="admin:live_chats")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back_to_panel")]
    ])

    await safe_edit_text(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:end_chat:"))
async def end_chat_by_admin(callback: CallbackQuery, bot: Bot, dispatcher):
    """End a specific chat by admin."""
    pool = dispatcher["db"]
    chat_id = int(callback.data.split(":")[-1])
    admin_id = callback.from_user.id

    success, user1_id, user2_id = await admin_end_chat_by_id(pool, chat_id)

    if success:
        await log_admin_action(
            pool, admin_id, "end_chat",
            f"Ended chat #{chat_id} between {user1_id} and {user2_id}"
        )

        try:
            await bot.send_message(user1_id, "✅ Chat admin tomonidan tugatildi.")
            await bot.send_message(user2_id, "✅ Chat admin tomonidan tugatildi.")
        except:
            pass

        await callback.answer("✅ Chat tugatildi!", show_alert=True)
        # Refresh the live chats list
        await show_live_chats(callback, bot, dispatcher)
    else:
        await callback.answer("❌ Chat topilmadi.", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin:chat_details:"))
async def show_chat_details(callback: CallbackQuery, bot: Bot, dispatcher):
    """Show detailed information about a specific chat."""
    pool = dispatcher["db"]
    chat_id = int(callback.data.split(":")[-1])

    async with pool.acquire() as conn:
        chat = await conn.fetchrow("""
            SELECT user1_id, user2_id, created_at FROM chat_connections WHERE id = $1
        """, chat_id)

        if not chat:
            await callback.answer("❌ Chat topilmadi.", show_alert=True)
            return

        user1_info = await conn.fetchrow(
            "SELECT name, username FROM users WHERE user_id = $1", chat["user1_id"]
        )
        user2_info = await conn.fetchrow(
            "SELECT name, username FROM users WHERE user_id = $1", chat["user2_id"]
        )

        message_count = await get_chat_message_count(pool, chat["user1_id"], chat["user2_id"])

    user1_name = user1_info['name'] or "Noma'lum"
    user2_name = user2_info['name'] or "Noma'lum"
    text = (
        f"<b>💬 Chat tafsilotlari</b>\n\n"
        f"🆔 Chat ID: <code>{chat_id}</code>\n"
        f"👤 Foydalanuvchi 1: <a href='tg://user?id={chat['user1_id']}'>{user1_name}</a> (<code>{chat['user1_id']}</code>)\n"
        f"👤 Foydalanuvchi 2: <a href='tg://user?id={chat['user2_id']}'>{user2_name}</a> (<code>{chat['user2_id']}</code>)\n"
        f"🕒 Boshlangan: {chat['created_at']:%Y-%m-%d %H:%M}\n"
        f"📨 Xabarlar soni: <b>{message_count}</b>"
    )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛑 Chatni tugatish", callback_data=f"admin:end_chat:{chat_id}")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:live_chats")]
        ])
    )
    await callback.answer()


# ==================== SETTINGS ====================

@admin_router.callback_query(F.data == "admin:settings")
async def show_settings(callback: CallbackQuery, bot: Bot, dispatcher):
    """Show bot settings and configuration."""
    pool = dispatcher["db"]

    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        active_chats = await conn.fetchval("SELECT COUNT(*) FROM chat_connections")
        banned_count = await get_banned_users_count(pool)
        queue_count = await conn.fetchval("SELECT COUNT(*) FROM chat_queue")

    text = (
        "<b>⚙️ Bot sozlamalari</b>\n\n"
        "<b>📊 Joriy holat:</b>\n"
        f"👥 Foydalanuvchilar: <b>{total_users}</b>\n"
        f"💬 Faol chatlar: <b>{active_chats}</b>\n"
        f"⛔ Bloklanganlar: <b>{banned_count}</b>\n"
        f"⏳ Navbatda: <b>{queue_count}</b>\n\n"
        "<b>🔧 Sozlamalar:</b>\n"
        "• Live chat: ✅ Faol\n"
        "• Anonim xabar: ✅ Faol\n"
        "• Broadcast: ✅ Faol"
    )

    await safe_edit_text(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="admin:settings")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back_to_panel")]
        ]),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()
