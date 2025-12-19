"""
Admin handlers module.
Handles admin panel, ban/unban, broadcast, statistics, live chat monitoring, and settings.
"""
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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
    end_chat,
    get_user_full_info,
    get_user_payment_history,
    VALID_PLANS,
    PAYMENT_STATUSES,
    PAYMENT_METHOD_NAMES,
    log_message,
    get_or_create_user
)
from states import BanState, BroadcastState, SearchUserState, AdminMessageState

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
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast_options")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats")],
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin:users")],
        [InlineKeyboardButton(text="💬 Live chat monitoring", callback_data="admin:live_chats")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="admin:settings")],
    ])


@admin_router.callback_query(F.data == "admin:main")
async def admin_panel_main(callback: CallbackQuery, bot: Bot, dispatcher):
    """Return to admin panel main menu."""
    pool = dispatcher["db"]
    user_id = callback.from_user.id

    if not await is_user_admin(pool, user_id):
        await callback.answer("❌ Sizda admin huquqi yo'q.", show_alert=True)
        return
    
    await callback.message.edit_text(
        "<b>👨‍💻 Admin panelga xush kelibsiz!</b>\nQuyidagilardan birini tanlang:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


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

    # Get comprehensive user info
    user_info = await get_user_full_info(pool, user_id)
    if not user_info:
        await message.answer("😕 Bunday foydalanuvchi topilmadi.")
        return

    async with pool.acquire() as conn:
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

    # Build comprehensive user info text
    text = f"👤 <b>Foydalanuvchi ma'lumotlari</b>\n\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🆔 <b>ID:</b> <code>{user_info['user_id']}</code>\n"
    text += f"📛 <b>Ism:</b> {user_info['name']}\n"
    if user_info['username']:
        text += f"👤 <b>Username:</b> @{user_info['username']}\n"
    text += f"🗓 <b>Ro'yxatdan o'tgan:</b> {user_info['created_at']:%Y-%m-%d %H:%M}\n"
    text += f"🛡 <b>Admin:</b> {'✅' if user_info['is_admin'] else '❌'}\n"
    text += f"👑 <b>Superuser:</b> {'✅' if user_info['is_superuser'] else '❌'}\n"
    text += f"🔇 <b>Blok:</b> {'✅' if is_banned else '❌'}\n"
    text += f"💬 <b>Chatda:</b> {'✅' if in_chat else '❌'}\n\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>💰 Balans ma'lumotlari</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"💵 <b>Joriy balans:</b> {user_info['balance']:,.2f} so'm\n"
    text += f"📊 <b>Jami yuklangan:</b> {user_info['total_deposited']:,.2f} so'm\n\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>💎 Premium ma'lumotlari</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"💎 <b>Premium:</b> {'✅ Faol' if user_info['is_premium'] else '❌ Faol emas'}\n"
    
    if user_info['subscription']:
        sub = user_info['subscription']
        plan_name = VALID_PLANS.get(sub['plan'], sub['plan'])
        text += f"📦 <b>Plan:</b> {plan_name}\n"
        text += f"📅 <b>Boshlanish:</b> {sub['start_date']:%Y-%m-%d %H:%M}\n"
        text += f"📅 <b>Tugash:</b> {sub['end_date']:%Y-%m-%d %H:%M}\n"
        text += f"🔄 <b>Faol:</b> {'✅' if sub['is_active'] else '❌'}\n"
    else:
        text += f"📦 <b>Plan:</b> Yo'q\n"
    text += f"\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>🎁 Referral ma'lumotlari</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    if user_info['referral_code']:
        text += f"🔑 <b>Referral kodi:</b> <code>{user_info['referral_code']}</code>\n"
    else:
        text += f"🔑 <b>Referral kodi:</b> Yo'q\n"
    text += f"👥 <b>Taklif qilingan:</b> {user_info['referral_count']} ta\n"
    text += f"💰 <b>Referral daromadi:</b> {user_info['referral_earnings']:,.2f} so'm\n"
    if user_info['referrer_name']:
        text += f"👤 <b>Taklif qilgan:</b> {user_info['referrer_name']} (ID: {user_info.get('referral_by', 'N/A')})\n"
    else:
        text += f"👤 <b>Taklif qilgan:</b> Yo'q\n"
    text += f"\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>📊 Faollik</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    if user_info['last_activity']:
        text += f"🕐 <b>Oxirgi faollik:</b> {user_info['last_activity']:%Y-%m-%d %H:%M}\n"
    else:
        text += f"🕐 <b>Oxirgi faollik:</b> Ma'lumot yo'q\n"

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
    
    # Add profile button
    user_profile_link = f"tg://user?id={user_id}"
    buttons.append([InlineKeyboardButton(
        text="👤 Profilni ko'rish",
        url=user_profile_link
    )])
    
    # Add payment history button
    buttons.append([InlineKeyboardButton(
        text="💳 To'lovlar tarixi",
        callback_data=f"admin:payment_history:{user_id}"
    )])
    
    # Add anonymous message button
    buttons.append([InlineKeyboardButton(
        text="📨 Anonim xabar yuborish",
        callback_data=f"admin:send_message:{user_id}"
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

    # Get comprehensive user info
    user_info = await get_user_full_info(pool, user_id)
    if not user_info:
        await callback.message.edit_text("😕 Bunday foydalanuvchi topilmadi.", parse_mode=ParseMode.HTML)
        await callback.answer()
        return

    async with pool.acquire() as conn:
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

    # Build comprehensive user info text
    text = f"👤 <b>Foydalanuvchi ma'lumotlari</b>\n\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🆔 <b>ID:</b> <code>{user_info['user_id']}</code>\n"
    text += f"📛 <b>Ism:</b> {user_info['name']}\n"
    if user_info['username']:
        text += f"👤 <b>Username:</b> @{user_info['username']}\n"
    text += f"🗓 <b>Ro'yxatdan o'tgan:</b> {user_info['created_at']:%Y-%m-%d %H:%M}\n"
    text += f"🛡 <b>Admin:</b> {'✅' if user_info['is_admin'] else '❌'}\n"
    text += f"👑 <b>Superuser:</b> {'✅' if user_info['is_superuser'] else '❌'}\n"
    text += f"🔇 <b>Blok:</b> {'✅' if is_banned else '❌'}\n"
    text += f"💬 <b>Chatda:</b> {'✅' if in_chat else '❌'}\n\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>💰 Balans ma'lumotlari</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"💵 <b>Joriy balans:</b> {user_info['balance']:,.2f} so'm\n"
    text += f"📊 <b>Jami yuklangan:</b> {user_info['total_deposited']:,.2f} so'm\n\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>💎 Premium ma'lumotlari</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"💎 <b>Premium:</b> {'✅ Faol' if user_info['is_premium'] else '❌ Faol emas'}\n"
    
    if user_info['subscription']:
        sub = user_info['subscription']
        plan_name = VALID_PLANS.get(sub['plan'], sub['plan'])
        text += f"📦 <b>Plan:</b> {plan_name}\n"
        text += f"📅 <b>Boshlanish:</b> {sub['start_date']:%Y-%m-%d %H:%M}\n"
        text += f"📅 <b>Tugash:</b> {sub['end_date']:%Y-%m-%d %H:%M}\n"
        text += f"🔄 <b>Faol:</b> {'✅' if sub['is_active'] else '❌'}\n"
    else:
        text += f"📦 <b>Plan:</b> Yo'q\n"
    text += f"\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>🎁 Referral ma'lumotlari</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    if user_info['referral_code']:
        text += f"🔑 <b>Referral kodi:</b> <code>{user_info['referral_code']}</code>\n"
    else:
        text += f"🔑 <b>Referral kodi:</b> Yo'q\n"
    text += f"👥 <b>Taklif qilingan:</b> {user_info['referral_count']} ta\n"
    text += f"💰 <b>Referral daromadi:</b> {user_info['referral_earnings']:,.2f} so'm\n"
    if user_info['referrer_name']:
        text += f"👤 <b>Taklif qilgan:</b> {user_info['referrer_name']} (ID: {user_info.get('referral_by', 'N/A')})\n"
    else:
        text += f"👤 <b>Taklif qilgan:</b> Yo'q\n"
    text += f"\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>📊 Faollik</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    if user_info['last_activity']:
        text += f"🕐 <b>Oxirgi faollik:</b> {user_info['last_activity']:%Y-%m-%d %H:%M}\n"
    else:
        text += f"🕐 <b>Oxirgi faollik:</b> Ma'lumot yo'q\n"

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
    
    # Add profile button
    user_profile_link = f"tg://user?id={user_id}"
    buttons.append([InlineKeyboardButton(
        text="👤 Profilni ko'rish",
        url=user_profile_link
    )])
    
    # Add payment history button
    buttons.append([InlineKeyboardButton(
        text="💳 To'lovlar tarixi",
        callback_data=f"admin:payment_history:{user_id}"
    )])
    
    # Add anonymous message button
    buttons.append([InlineKeyboardButton(
        text="📨 Anonim xabar yuborish",
        callback_data=f"admin:send_message:{user_id}"
    )])

    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:recent_users:1")])

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:send_message:"))
async def start_admin_message(callback: CallbackQuery, state: FSMContext):
    """Start the flow for admin to send anonymous message to a user."""
    user_id = int(callback.data.split(":")[-1])
    
    # Store target user ID in state
    await state.update_data(target_id=user_id)
    await state.set_state(AdminMessageState.waiting_for_message)
    
    await callback.message.edit_text(
        "<b>📨 Anonim xabar yuborish</b>\n\n"
        "Xabarni yuboring (matn, rasm, video, ovoz yoki hujjat):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin:cancel_message")]
        ])
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:cancel_message")
async def cancel_admin_message(callback: CallbackQuery, state: FSMContext):
    """Cancel sending anonymous message."""
    await state.clear()
    await callback.message.edit_text(
        "<b>❌ Xabar yuborish bekor qilindi.</b>",
        parse_mode=ParseMode.HTML
    )
    await callback.answer("❌ Bekor qilindi.")


@admin_router.message(AdminMessageState.waiting_for_message)
async def handle_admin_message(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Handle admin's anonymous message and send it to target user."""
    pool = dispatcher["db"]
    data = await state.get_data()
    target_id = data.get("target_id")
    
    if not target_id:
        await message.answer("❌ Xatolik: Foydalanuvchi ID topilmadi.")
        await state.clear()
        return
    
    admin_id = message.from_user.id
    admin_username = message.from_user.username
    admin_name = message.from_user.full_name
    
    # Get or create admin user (for token)
    admin_token, _ = await get_or_create_user(pool, admin_id, admin_username, admin_name)
    
    bot_username = (await bot.me()).username
    link = f"https://t.me/{bot_username}?start={admin_token}"
    
    try:
        if message.text:
            # Text message
            import base64
            message_text_encoded = base64.b64encode(message.text.encode('utf-8')).decode('utf-8')[:200]
            token_encoded = base64.b64encode(admin_token.encode('utf-8')).decode('utf-8')
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Javob berish", url=link)],
                [InlineKeyboardButton(text="👤 Kimdan", callback_data=f"reveal:sender:{admin_id}:{token_encoded}:{message_text_encoded}")]
            ])
            
            await bot.send_message(
                chat_id=target_id,
                text=f"<b>📨 Sizga yangi anonim xabar bor!</b>\n\n{message.text}",
                reply_markup=keyboard
            )
            await log_message(pool, admin_id, target_id, message.text)
            
        else:
            # Media messages
            import base64
            token_encoded = base64.b64encode(admin_token.encode('utf-8')).decode('utf-8')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Javob berish", url=link)],
                [InlineKeyboardButton(text="👤 Kimdan", callback_data=f"reveal:sender:{admin_id}:{token_encoded}:media")]
            ])
            
            if message.photo:
                await bot.send_photo(
                    target_id,
                    message.photo[-1].file_id,
                    caption="<b>📨 Sizga yangi anonim xabar bor!</b>",
                    reply_markup=keyboard
                )
            elif message.video:
                await bot.send_video(
                    target_id,
                    message.video.file_id,
                    caption="<b>📨 Sizga yangi anonim xabar bor!</b>",
                    reply_markup=keyboard
                )
            elif message.voice:
                await bot.send_voice(
                    target_id,
                    message.voice.file_id,
                    caption="<b>📨 Sizga yangi anonim xabar bor!</b>",
                    reply_markup=keyboard
                )
            elif message.document:
                await bot.send_document(
                    target_id,
                    message.document.file_id,
                    caption="<b>📨 Sizga yangi anonim xabar bor!</b>",
                    reply_markup=keyboard
                )
            else:
                await message.answer("<b>⚠️ Ushbu turdagi xabar qo'llab-quvvatlanmaydi.</b>")
                await state.clear()
                return
            
            # Log media messages to log channel
            sender_link = f'<a href="tg://user?id={admin_id}">{admin_name}</a>'
            receiver_link = f'<a href="tg://user?id={target_id}">{target_id}</a>'
            
            log_caption = (
                f"📥 <b>Yuboruvchi (Admin):</b> {sender_link}\n\n"
                f"👤 <b>Qabul qiluvchi:</b> {receiver_link}"
            )
            
            try:
                if message.photo:
                    await bot.send_photo(LOG_CHANNEL_ID, message.photo[-1].file_id, caption=log_caption, parse_mode='HTML')
                elif message.video:
                    await bot.send_video(LOG_CHANNEL_ID, message.video.file_id, caption=log_caption, parse_mode='HTML')
                elif message.voice:
                    await bot.send_voice(LOG_CHANNEL_ID, message.voice.file_id, caption=log_caption, parse_mode='HTML')
                elif message.document:
                    await bot.send_document(LOG_CHANNEL_ID, message.document.file_id, caption=log_caption, parse_mode='HTML')
            except TelegramForbiddenError:
                pass  # Log channel not accessible
        
        await message.answer("✅ Anonim xabar yuborildi!", reply_markup=ReplyKeyboardRemove())
        await log_admin_action(pool, admin_id, "send_anonymous_message", f"Sent to user ID: {target_id}")
        
    except TelegramForbiddenError:
        await message.answer("❌ Foydalanuvchi botni bloklagan yoki xabar yuborib bo'lmaydi.")
    except Exception as e:
        await message.answer(f"⚠️ Xatolik yuz berdi: {str(e)}")
        logger.error(f"Error sending admin anonymous message: {e}", exc_info=True)
    finally:
        await state.clear()


@admin_router.callback_query(F.data.startswith("admin:payment_history:"))
async def show_payment_history(callback: CallbackQuery, dispatcher):
    """Display payment history for a user."""
    pool = dispatcher["db"]
    user_id = int(callback.data.split(":")[-1])
    
    # Get user name
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT name FROM users WHERE user_id = $1", user_id)
        if not user:
            await callback.answer("❌ Foydalanuvchi topilmadi.", show_alert=True)
            return
    
    # Get payment history
    payments = await get_user_payment_history(pool, user_id, limit=50)
    
    if not payments:
        text = f"💳 <b>To'lovlar tarixi</b>\n\n"
        text += f"👤 <b>Foydalanuvchi:</b> {user['name']}\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"📭 To'lovlar mavjud emas."
    else:
        text = f"💳 <b>To'lovlar tarixi</b>\n\n"
        text += f"👤 <b>Foydalanuvchi:</b> {user['name']}\n"
        text += f"📊 <b>Jami to'lovlar:</b> {len(payments)} ta\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        
        total_amount = sum(float(p['amount']) for p in payments)
        text += f"💰 <b>Jami summa:</b> {total_amount:,.2f} so'm\n\n"
        text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, payment in enumerate(payments[:20], 1):  # Show last 20 payments
            status_emoji = PAYMENT_STATUSES.get(payment['status'], payment['status'])
            method_name = PAYMENT_METHOD_NAMES.get(payment['method'], payment['method'])
            
            text += f"<b>#{i}</b> | {payment['created_at']:%Y-%m-%d %H:%M}\n"
            text += f"💰 {payment['amount']:,.2f} so'm | {method_name} | {status_emoji}\n"
            if payment['transaction_id']:
                text += f"🆔 <code>{payment['transaction_id'][:20]}...</code>\n"
            text += f"\n"
        
        if len(payments) > 20:
            text += f"\n... va yana {len(payments) - 20} ta to'lov"
    
    buttons = [
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"admin:select_user:{user_id}")]
    ]
    
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

@admin_router.callback_query(F.data == "admin:broadcast_options")
async def show_broadcast_options(callback: CallbackQuery):
    """Show broadcast options: all users or non-premium users only."""
    await callback.message.edit_text(
        "<b>📢 Broadcast</b>\n\n"
        "Kimlarga xabar yubormoqchisiz?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Barcha foydalanuvchilar", callback_data="admin:broadcast:all")],
            [InlineKeyboardButton(text="❌ Premium bo'lmaganlar", callback_data="admin:broadcast:non_premium")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:main")]
        ])
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:broadcast:"))
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    """Start broadcast message flow with selected target type."""
    broadcast_type = callback.data.split(":")[-1]  # "all" or "non_premium"
    
    # Store broadcast type in state
    await state.update_data(broadcast_type=broadcast_type)
    await state.set_state(BroadcastState.waiting_for_message)
    
    target_text = "barcha foydalanuvchilar" if broadcast_type == "all" else "premium bo'lmagan foydalanuvchilar"
    
    await callback.message.edit_text(
        f"<b>📢 Broadcast - {target_text.title()}</b>\n\n"
        "Yubormoqchi bo'lgan xabaringizni yozing:\n"
        "Matn yoki rasm/video bilan matn ham bo'lishi mumkin.",
        parse_mode=ParseMode.HTML,
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
    """Process and send broadcast message to selected users."""
    pool = dispatcher["db"]
    admin_id = message.from_user.id
    data = await state.get_data()
    broadcast_type = data.get("broadcast_type", "all")  # Default to "all" if not set
    await state.clear()

    # Show preview
    target_text = "barcha foydalanuvchilar" if broadcast_type == "all" else "premium bo'lmagan foydalanuvchilar"
    preview_text = f"<b>📢 Broadcast ko'rinishi ({target_text}):</b>\n\n"
    if message.text:
        preview_text += message.text
    else:
        preview_text += "Media xabar (rasm/video/voice/document)"

    await message.answer(preview_text, parse_mode=ParseMode.HTML)
    await message.answer("<i>⏳ Xabar yuborilmoqda...</i>")

    success = 0
    fail = 0
    batch_size = 30
    delay_between_batches = 1.0

    # Get users based on broadcast type
    async with pool.acquire() as conn:
        if broadcast_type == "non_premium":
            users = await conn.fetch("SELECT user_id FROM users WHERE is_premium = FALSE")
        else:
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

    target_description = "barcha foydalanuvchilar" if broadcast_type == "all" else "premium bo'lmagan foydalanuvchilar"
    
    await log_admin_action(
        pool, admin_id, "broadcast",
        f"Sent to {success} users ({target_description}), failed: {fail}"
    )

    await message.answer(
        f"<b>✅ Broadcast yakunlandi!</b>\n\n"
        f"🎯 <b>Maqsad:</b> {target_description.title()}\n"
        f"📬 <b>Yuborildi:</b> {success}\n"
        f"❌ <b>Yuborilmadi:</b> {fail}",
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
