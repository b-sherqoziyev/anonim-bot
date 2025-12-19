"""
User handlers module.
Handles /start, /help commands and anonymous question messages from regular users.
"""
from aiogram import Router, Bot, F
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, CallbackQuery
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import LOG_CHANNEL_ID, TIMEZONE
from db import (
    is_user_banned, get_user_by_token, log_message, get_or_create_user,
    get_user_balance_info, get_user_premium_info, VALID_PLANS, PLAN_PRICES,
    get_plan_price, create_payment, update_payment_status, update_user_balance,
    activate_subscription, check_transaction_id_exists,
    generate_referral_code, get_user_referral_code
)
from states import QuestionStates, PremiumPurchaseState
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram.types import CallbackQuery

# Create router for user handlers
user_router = Router()


@user_router.message(Command("start"))
async def start_handler(message: Message, command: CommandObject, state: FSMContext, bot: Bot, dispatcher):
    """Handle /start command - create user token or start question flow."""
    pool = dispatcher["db"]
    user_id = message.from_user.id
    username = message.from_user.username
    name = message.from_user.full_name

    if command.args:
        # Check if it's a referral code (starts with "ref_")
        if command.args.startswith("ref_"):
            referral_code = command.args[4:]  # Remove "ref_" prefix

            # Check if user already exists in database
            from db import get_user_by_referral_code
            async with pool.acquire() as conn:
                existing_user = await conn.fetchrow("SELECT user_id, referral_by FROM users WHERE user_id = $1",
                                                    user_id)

            if not existing_user:
                # New user - create with referral
                token = await get_or_create_user(pool, user_id, username, name, referral_code)

                # Process referral bonus and send notification
                from db import process_referral
                await process_referral(pool, user_id, referral_code, bot)
            else:
                # User already exists - referral won't count
                token = await get_or_create_user(pool, user_id, username, name)

            bot_username = (await bot.me()).username
            link = f"https://t.me/{bot_username}?start={token}"
            share_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📤 Ulashish", url=f"https://t.me/share/url?url={link}")]
            ])

            await message.answer(
                f"<b>👋 Xush kelibsiz, {name}!\n</b>"
                f"<b>Bu sizning shaxsiy havolangiz:\n</b>"
                f"\n🔗 {link}\n\n"
                f"<b>Ulashish orqali anonim suhbat quring!</b>\n"
                f"<b>Bot haqida bilish uchun 👉 /help</b>",
                reply_markup=share_keyboard
            )
            return

        # User clicked on a link with token (for anonymous questions)
        is_banned, banned_until = await is_user_banned(pool, user_id)
        if is_banned:
            await message.answer(
                "⛔ Siz bloklangan va xabar yubora olmaysiz.\n"
                "Iltimos, admin bilan bog'laning."
            )
            return

        target = await get_user_by_token(pool, command.args)
        if target:
            await state.set_state(QuestionStates.waiting_for_question)
            await state.update_data(target_id=target["user_id"])
            await message.answer("<b>Murojaatingizni shu yerga yozing!</b>")
        else:
            await message.answer("<b>⚠️ Noto‘g‘ri havola.</b>")
    else:
        # Regular /start command - show user's personal link
        token = await get_or_create_user(pool, user_id, username, name)

        bot_username = (await bot.me()).username
        link = f"https://t.me/{bot_username}?start={token}"
        share_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Ulashish", url=f"https://t.me/share/url?url={link}")]
        ])

        await message.answer(
            f"<b>👋 Xush kelibsiz, {name}!\n</b>"
            f"<b>Bu sizning shaxsiy havolangiz:\n</b>"
            f"\n🔗 {link}\n\n"
            f"<b>Ulashish orqali anonim suhbat quring!</b>\n"
            f"<b>Bot haqida bilish uchun 👉 /help</b>",
            reply_markup=share_keyboard
        )


@user_router.message(QuestionStates.waiting_for_question)
async def handle_question(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Handle anonymous question messages from users."""
    pool = dispatcher["db"]
    data = await state.get_data()
    target_id = data.get("target_id")

    user_id = message.from_user.id
    username = message.from_user.username
    name = message.from_user.full_name

    # Get or create sender token
    sender_token = await get_or_create_user(pool, user_id, username, name)

    bot_username = (await bot.me()).username
    link = f"https://t.me/{bot_username}?start={sender_token}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Javob berish", url=link)]
    ])

    try:
        if message.text:
            # Text message
            await bot.send_message(
                chat_id=target_id,
                text=f"<b>📨 Sizga yangi anonim xabar bor!</b>\n\n{message.text}",
                reply_markup=keyboard
            )
            await log_message(pool, user_id, target_id, message.text)

        else:
            # Media messages (photo, video, voice, document)
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
                await message.answer("<b>⚠️ Ushbu turdagi xabar qo‘llab-quvvatlanmaydi.</b>")
                return

            # Log media messages to log channel
            sender_link = f'<a href="tg://user?id={user_id}">{name}</a>'
            receiver_link = f'<a href="tg://user?id={target_id}">{target_id}</a>'

            log_caption = (
                f"📥 <b>Yuboruvchi:</b> {sender_link}\n\n"
                f"👤 <b>Qabul qiluvchi:</b> {receiver_link}"
            )

            if message.photo:
                await bot.send_photo(LOG_CHANNEL_ID, message.photo[-1].file_id, caption=log_caption, parse_mode='HTML')
            elif message.video:
                await bot.send_video(LOG_CHANNEL_ID, message.video.file_id, caption=log_caption, parse_mode='HTML')
            elif message.voice:
                await bot.send_voice(LOG_CHANNEL_ID, message.voice.file_id, caption=log_caption, parse_mode='HTML')
            elif message.document:
                await bot.send_document(LOG_CHANNEL_ID, message.document.file_id, caption=log_caption,
                                        parse_mode='HTML')

        await message.answer("✅ Xabaringiz yuborildi!", reply_markup=ReplyKeyboardRemove())

    except TelegramForbiddenError:
        await message.answer("❌ Xabar yuborilmadi. Foydalanuvchi botni bloklagan.")
    except TelegramBadRequest as e:
        await message.answer(f"⚠️ Xatolik yuz berdi: {e.message}")

    await state.clear()


@user_router.message(Command("help"))
async def send_help(message: Message, bot: Bot, dispatcher):
    """Handle /help command - show help information."""
    from db import is_user_admin
    from config import ADMIN_URL

    pool = dispatcher["db"]
    user_id = message.from_user.id

    if await is_user_admin(pool, user_id):
        # Admin help
        await message.answer(
            "<b>🛠 Admin Yordam</b>\n\n"
            "Siz admin hisobidasiz. Quyidagilarni bajarishingiz mumkin:\n"
            "• /admin — admin panel\n"
        )
    else:
        # Regular user help
        await message.answer(
            "<b>❓ Yordam</b>\n\n"
            "Botning asosiy komandalarini bilib oling:\n"
            "• /start — botni ishga tushirish va shaxsiy havola olish\n"
            "• /help — yordam oynasi (shu xabar)\n"
            "• /balance — joriy balans va jami yuklangan summani ko'rish\n"
            "• /premium — premium status, plan va balansni ko'rish\n"
            "• /find_chat — anonim tarzda suhbatdosh qidirish\n"
            "• /end_chat — jonli chatni yakunlash\n"
            "• /info — bot haqida batafsil ma’lumot\n\n"
            "Ko‘proq ma’lumot olish uchun /info yuboring.",
            parse_mode="HTML"
        )


@user_router.message(Command("info"))
async def send_info(message: Message, bot: Bot, dispatcher):
    """Handle /info command - show detailed information about the bot."""
    from config import ADMIN_URL

    bot_username = (await bot.me()).username
    example_link = f"https://t.me/{bot_username}?start=example_token"

    info_text = (
        "<b>ℹ️ Bot haqida batafsil</b>\n\n"
        "👋 Salom! Bu bot anonim xabar yuborish va jonli chat qilish imkonini beradi.\n\n"
        "<b>1️⃣ Shaxsiy havola (start link)</b>\n"
        "/start komandasi orqali sizga maxsus shaxsiy havola beriladi.\n"
        "Bu havolani boshqalar bilan ulashsangiz, ular sizga anonim xabar yuborishi mumkin.\n\n"
        "<b>2️⃣ Anonim xabar yuborish</b>\n"
        "Havola orqali kelgan foydalanuvchi sizga anonim xabar yuboradi.\n"
        "Siz ham shunday havola orqali boshqa foydalanuvchilarga anonim xabar yuborishingiz mumkin.\n\n"
        "<b>3️⃣ Jonli chat qilish</b>\n"
        "• /find_chat komandasi yordamida tasodifiy foydalanuvchi bilan jonli suhbat boshlaysiz.\n"
        "• /end_chat orqali suhbatni yakunlashingiz mumkin.\n"
        "• Suhbat anonim tarzda kechadi, shaxsiy ma'lumotlar oshkor qilinmaydi.\n\n"
        f"<b>🔗 Qo'shimcha yordam</b>\n"
        f"Agar sizga yordam kerak bo'lsa yoki xatolik yuz bersa, admin bilan bog'laning: <a href='{ADMIN_URL}'>admin</a>"
    )

    await message.answer(info_text, parse_mode='HTML')


@user_router.message(Command("balance"))
async def show_balance(message: Message, dispatcher):
    """Handle /balance command - show user's current balance and total deposited."""
    pool = dispatcher["db"]
    user_id = message.from_user.id

    # Ensure user exists in database
    username = message.from_user.username
    name = message.from_user.full_name
    await get_or_create_user(pool, user_id, username, name)

    # Get balance information
    balance, total_deposited = await get_user_balance_info(pool, user_id)

    if balance is None:
        await message.answer("<b>⚠️ Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.</b>")
        return

    balance_text = (
        f"<b>💰 Balans</b>\n\n"
        f"💵 <b>Joriy balans:</b> {balance:,.2f} so'm\n"
        f"📊 <b>Jami yuklangan:</b> {total_deposited:,.2f} so'm"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")]
    ])

    await message.answer(balance_text, parse_mode='HTML', reply_markup=keyboard)


@user_router.message(Command("premium"))
async def show_premium_status(message: Message, dispatcher):
    """Handle /premium command - show user's premium status, plan, and balance."""
    pool = dispatcher["db"]
    user_id = message.from_user.id

    # Ensure user exists in database
    username = message.from_user.username
    name = message.from_user.full_name
    await get_or_create_user(pool, user_id, username, name)

    # Get premium information
    premium_info = await get_user_premium_info(pool, user_id)

    if premium_info is None:
        await message.answer("<b>⚠️ Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.</b>")
        return

    is_premium = premium_info['is_premium']
    balance = premium_info['balance']

    if is_premium:
        # User is premium - show remaining time and balance
        subscription = premium_info.get('subscription')

        if subscription:
            end_date = subscription['end_date']
            current_time = datetime.now(ZoneInfo(TIMEZONE))

            # Calculate remaining time
            if isinstance(end_date, datetime):
                # Ensure end_date is timezone-aware
                if end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=ZoneInfo(TIMEZONE))
                elif end_date.tzinfo != ZoneInfo(TIMEZONE):
                    end_date = end_date.astimezone(ZoneInfo(TIMEZONE))

                remaining = end_date - current_time

                if remaining.total_seconds() > 0:
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    minutes = (remaining.seconds % 3600) // 60

                    if days > 0:
                        time_remaining = f"{days} kun, {hours} soat"
                    elif hours > 0:
                        time_remaining = f"{hours} soat, {minutes} daqiqa"
                    else:
                        time_remaining = f"{minutes} daqiqa"

                    plan_name = VALID_PLANS.get(subscription['plan'], subscription['plan'])

                    premium_text = (
                        f"<b>⭐ Premium Status</b>\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"✅ <b>Premium:</b> <code>Faol</code>\n"
                        f"📦 <b>Plan:</b> <code>{plan_name}</code>\n"
                        f"⏰ <b>Qolgan vaqt:</b> <code>{time_remaining}</code>\n"
                        f"📅 <b>Tugash sanasi:</b> <code>{end_date.strftime('%Y-%m-%d %H:%M')}</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>"
                    )
                else:
                    # Subscription expired
                    premium_text = (
                        f"<b>⭐ Premium Status</b>\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"❌ <b>Premium:</b> <code>Muddati tugagan</code>\n"
                        f"📅 <b>Tugash sanasi:</b> <code>{end_date.strftime('%Y-%m-%d %H:%M')}</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>\n\n"
                        f"💡 <b>Premiumni yangilash uchun plan tanlang:</b>\n"
                        f"📅 1 oy | 📅 3 oy | 📅 6 oy | 📅 1 yil"
                    )
            else:
                premium_text = (
                    f"<b>⭐ Premium Status</b>\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ <b>Premium:</b> <code>Faol</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>"
                )
        else:
            premium_text = (
                f"<b>⭐ Premium Status</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Premium:</b> <code>Faol</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>"
            )
    else:
        # User is not premium - show available plans and balance
        premium_text = (
            f"<b>⭐ Premium Status</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>Premium:</b> <code>Faol emas</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>\n\n"
            f"💡 <b>Premiumga o'tish uchun plan tanlang va to'lov qiling.</b>"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Premium sotib olish", callback_data="premium:purchase")],
            [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")]
        ])
        await message.answer(premium_text, parse_mode='HTML', reply_markup=keyboard)
        return

    # Premium user - add top up button
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")]
    ])
    await message.answer(premium_text, parse_mode='HTML', reply_markup=keyboard)


# ==================== PREMIUM PURCHASE FLOW ====================

@user_router.callback_query(F.data == "premium:purchase")
async def show_plans(callback: CallbackQuery, dispatcher):
    """Show available premium plans."""
    pool = dispatcher["db"]
    user_id = callback.from_user.id

    # Get user balance
    balance, _ = await get_user_balance_info(pool, user_id)
    balance = balance or 0.00

    plans_text = "<b>📦 Premium Planlar</b>\n\n"
    plans_text += f"💰 <b>Joriy balans:</b> <code>{balance:,.2f} so'm</code>\n\n"
    plans_text += "━━━━━━━━━━━━━━━━━━━━\n"

    buttons = []
    for plan_key, plan_name in VALID_PLANS.items():
        price = await get_plan_price(plan_key)
        plans_text += f"📅 <b>{plan_name}</b> - <code>{price:,.2f} so'm</code>\n"
        buttons.append([InlineKeyboardButton(
            text=f"📅 {plan_name} - {price:,.2f} so'm",
            callback_data=f"premium:select:{plan_key}"
        )])

    plans_text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    plans_text += "💡 Plan tanlang:"

    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="premium:back")])

    await callback.message.edit_text(
        plans_text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@user_router.callback_query(F.data == "premium:back")
async def back_to_premium_status(callback: CallbackQuery, dispatcher):
    """Return to premium status screen."""
    pool = dispatcher["db"]
    user_id = callback.from_user.id

    # Get premium information
    premium_info = await get_user_premium_info(pool, user_id)

    if premium_info is None:
        await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
        return

    is_premium = premium_info['is_premium']
    balance = premium_info['balance']

    if is_premium:
        await callback.answer("Siz allaqachon premium foydalanuvchisisiz.")
        return

    premium_text = (
        f"<b>⭐ Premium Status</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ <b>Premium:</b> <code>Faol emas</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>\n\n"
        f"💡 <b>Premiumga o'tish uchun plan tanlang va to'lov qiling.</b>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Premium sotib olish", callback_data="premium:purchase")]
    ])

    # Add top up button to premium status
    keyboard.inline_keyboard.append(
        [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")])
    await callback.message.edit_text(premium_text, parse_mode='HTML', reply_markup=keyboard)
    await callback.answer()


@user_router.callback_query(F.data.startswith("premium:select:"))
async def select_plan(callback: CallbackQuery, dispatcher):
    """Handle plan selection - check balance and activate or show top-up."""
    pool = dispatcher["db"]
    user_id = callback.from_user.id
    plan = callback.data.split(":")[-1]

    if plan not in VALID_PLANS:
        await callback.answer("❌ Noto'g'ri plan.", show_alert=True)
        return

    # Get plan price and user balance
    price = await get_plan_price(plan)
    balance, _ = await get_user_balance_info(pool, user_id)
    balance = balance or 0.00

    plan_name = VALID_PLANS[plan]

    if balance >= price:
        # Sufficient balance - activate subscription
        success, subscription_id = await activate_subscription(pool, user_id, plan)

        if success:
            # Deduct from balance
            await update_user_balance(pool, user_id, -price)

            # Create payment record
            await create_payment(
                pool, user_id, price, 'balance',
                merchant_data=f"subscription:{subscription_id}"
            )

            await callback.message.edit_text(
                f"<b>✅ Premium faollashtirildi!</b>\n\n"
                f"📦 <b>Plan:</b> <code>{plan_name}</code>\n"
                f"💰 <b>To'langan:</b> <code>{price:,.2f} so'm</code>\n"
                f"💵 <b>Qolgan balans:</b> <code>{balance - price:,.2f} so'm</code>\n\n"
                f"🎉 Tabriklaymiz! Premium rejadan foydalanishingiz mumkin.",
                parse_mode='HTML'
            )
            await callback.answer("✅ Premium faollashtirildi!", show_alert=True)
        else:
            await callback.answer("❌ Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.", show_alert=True)
    else:
        # Insufficient balance - show message
        needed = price - balance
        await callback.message.edit_text(
            f"<b>⚠️ Balans yetarli emas</b>\n\n"
            f"📦 <b>Tanlangan plan:</b> <code>{plan_name}</code>\n"
            f"💰 <b>Narx:</b> <code>{price:,.2f} so'm</code>\n"
            f"💵 <b>Joriy balans:</b> <code>{balance:,.2f} so'm</code>\n"
            f"❌ <b>Yetishmaydi:</b> <code>{needed:,.2f} so'm</code>\n\n"
            f"💡 Balansni to'ldirish uchun admin bilan bog'laning.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="premium:purchase")]
            ])
        )
        await callback.answer()


# ==================== REFERRAL SYSTEM ====================

@user_router.callback_query(F.data == "topup:show_referral")
async def show_referral_code(callback: CallbackQuery, bot: Bot, dispatcher):
    """Show user's referral code and instructions."""
    pool = dispatcher["db"]
    user_id = callback.from_user.id

    # Ensure user exists
    username = callback.from_user.username
    name = callback.from_user.full_name
    await get_or_create_user(pool, user_id, username, name)

    # Get or generate referral code
    referral_code = await generate_referral_code(pool, user_id)

    bot_username = (await bot.me()).username
    referral_link = f"https://t.me/{bot_username}?start=ref_{referral_code}"

    referral_text = (
        f"<b>💰 Balansni to'ldirish</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 <b>Referral tizimi</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 <b>Sizning referral kodingiz:</b>\n"
        f"<code>{referral_code}</code>\n\n"
        f"🔗 <b>Havola:</b>\n"
        f"<code>{referral_link}</code>\n\n"
        f"💡 <b>Qanday ishlaydi:</b>\n"
        f"• Do'stlaringizni taklif qiling\n"
        f"• Har bir taklif qilingan do'st uchun <b>+10 so'm</b> bonus olasiz\n"
        f"• Balansingiz avtomatik to'ldiriladi\n\n"
        f"✅ Taklif qilingan do'stlar botdan foydalanishni boshlaganda, sizga avtomatik xabar keladi!"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Do'stlarga ulashish",
                              url=f"https://t.me/share/url?url={referral_link}&text=Men%20ushbu%20botdan%20foydalanaman!%20Siz%20ham%20qo'shiling:%20")]
    ])

    await callback.message.edit_text(referral_text, parse_mode='HTML', reply_markup=keyboard)
    await callback.answer()
