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
    generate_referral_code, get_user_referral_code,
    notify_admins_new_user, is_user_premium, set_user_hidden
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
                token, is_new = await get_or_create_user(pool, user_id, username, name, referral_code)

                # Process referral bonus and send notification
                from db import process_referral
                await process_referral(pool, user_id, referral_code, bot)
                
                # Notify admins about new user
                if is_new:
                    await notify_admins_new_user(pool, bot, user_id, username, name)
            else:
                # User already exists - referral won't count
                token, _ = await get_or_create_user(pool, user_id, username, name)

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
        token, is_new = await get_or_create_user(pool, user_id, username, name)
        
        # Notify admins about new user
        if is_new:
            await notify_admins_new_user(pool, bot, user_id, username, name)

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
    sender_token, _ = await get_or_create_user(pool, user_id, username, name)

    bot_username = (await bot.me()).username
    link = f"https://t.me/{bot_username}?start={sender_token}"
    
    try:
        if message.text:
            # Text message - store original text and token for back button
            import base64
            # Encode message text and token for callback data
            message_text_encoded = base64.b64encode(message.text.encode('utf-8')).decode('utf-8')[:200]  # Limit length
            token_encoded = base64.b64encode(sender_token.encode('utf-8')).decode('utf-8')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Javob berish", url=link)],
                [InlineKeyboardButton(text="👤 Kimdan", callback_data=f"reveal:sender:{user_id}:{token_encoded}:{message_text_encoded}")]
            ])
            
            await bot.send_message(
                chat_id=target_id,
                text=f"<b>📨 Sizga yangi anonim xabar bor!</b>\n\n{message.text}",
                reply_markup=keyboard
            )
            await log_message(pool, user_id, target_id, message.text)

        else:
            # Media messages (photo, video, voice, document)
            # Store token for back button
            import base64
            token_encoded = base64.b64encode(sender_token.encode('utf-8')).decode('utf-8')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Javob berish", url=link)],
                [InlineKeyboardButton(text="👤 Kimdan", callback_data=f"reveal:sender:{user_id}:{token_encoded}:media")]
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
        "<b>4️⃣ Premium rejasi</b>\n"
        "• /premium komandasi orqali premium rejangizni ko'rishingiz va sotib olishingiz mumkin.\n"
        "• Premium rejada turli xil imtiyozlar va qo'shimcha funksiyalar mavjud.\n"
        "• Balansingizni to'ldirish uchun do'stlaringizni taklif qiling va har bir taklif uchun 10 so'm bonus oling.\n\n"
        f"<b>🔗 Qo'shimcha yordam</b>\n"
        f"Agar sizga yordam kerak bo'lsa yoki xatolik yuz bersa, admin bilan bog'laning: <a href='{ADMIN_URL}'>admin</a>"
    )

    await message.answer(info_text, parse_mode='HTML')


@user_router.message(Command("balance"))
async def show_balance(message: Message, bot: Bot, dispatcher):
    """Handle /balance command - show user's current balance and total deposited."""
    pool = dispatcher["db"]
    user_id = message.from_user.id

    # Ensure user exists in database
    username = message.from_user.username
    name = message.from_user.full_name
    _, is_new = await get_or_create_user(pool, user_id, username, name)
    
    # Notify admins about new user
    if is_new:
        await notify_admins_new_user(pool, bot, user_id, username, name)
    
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
async def show_premium_status(message: Message, bot: Bot, dispatcher):
    """Handle /premium command - show user's premium status, plan, and balance."""
    pool = dispatcher["db"]
    user_id = message.from_user.id

    # Ensure user exists in database
    username = message.from_user.username
    name = message.from_user.full_name
    _, is_new = await get_or_create_user(pool, user_id, username, name)
    
    # Notify admins about new user
    if is_new:
        await notify_admins_new_user(pool, bot, user_id, username, name)
    
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
                        f"<b>💎 Premium Status</b>\n\n"
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
                        f"<b>💎 Premium Status</b>\n\n"
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
                    f"<b>💎 Premium Status</b>\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ <b>Premium:</b> <code>Faol</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>"
                )
        else:
            premium_text = (
                f"<b>💎 Premium Status</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Premium:</b> <code>Faol</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>"
            )
    else:
        # User is not premium - show available plans and balance
        premium_text = (
            f"<b>💎 Premium Status</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>Premium:</b> <code>Faol emas</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>\n\n"
            f"💡 <b>Premiumga o'tish uchun plan tanlang va to'lov qiling.</b>"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Premium sotib olish", callback_data="premium:purchase")],
            [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")]
        ])
        await message.answer(premium_text, parse_mode='HTML', reply_markup=keyboard)
        return

    # Premium user - add top up button
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")]
    ])
    await message.answer(premium_text, parse_mode='HTML', reply_markup=keyboard)


@user_router.message(Command("profile"))
async def show_profile(message: Message, bot: Bot, dispatcher):
    """Handle /profile command - show user's profile information."""
    pool = dispatcher["db"]
    user_id = message.from_user.id
    
    # Ensure user exists in database
    username = message.from_user.username
    name = message.from_user.full_name
    _, is_new = await get_or_create_user(pool, user_id, username, name)
    
    # Notify admins about new user
    if is_new:
        await notify_admins_new_user(pool, bot, user_id, username, name)
    
    # Get user information
    async with pool.acquire() as conn:
        user_info = await conn.fetchrow("""
            SELECT 
                user_id, username, name, token, is_premium, balance, 
                total_deposited, referral_code, created_at, is_hidden
            FROM users 
            WHERE user_id = $1
        """, user_id)
    
    if not user_info:
        await message.answer("<b>⚠️ Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.</b>")
        return
    
    # Get premium info for subscription details
    premium_info = await get_user_premium_info(pool, user_id)
    subscription = premium_info.get('subscription') if premium_info else None
    
    # Build profile text (excluding is_admin and is_superuser)
    profile_text = (
        f"<b>👤 Profil</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> <code>{user_info['user_id']}</code>\n"
        f"📛 <b>Ism:</b> {user_info['name']}\n"
    )
    
    if user_info['username']:
        profile_text += f"👤 <b>Username:</b> @{user_info['username']}\n"
    else:
        profile_text += f"👤 <b>Username:</b> Yo'q\n"
    
    profile_text += f"🗓 <b>Ro'yxatdan o'tgan:</b> {user_info['created_at']:%Y-%m-%d %H:%M}\n\n"
    
    profile_text += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>💰 Balans ma'lumotlari</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Joriy balans:</b> {user_info['balance']:,.2f} so'm\n"
        f"📊 <b>Jami yuklangan:</b> {user_info['total_deposited']:,.2f} so'm\n\n"
    )
    
    profile_text += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>💎 Premium ma'lumotlari</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>Premium:</b> {'✅ Faol' if user_info['is_premium'] else '❌ Faol emas'}\n"
    )
    
    if subscription:
        plan_name = VALID_PLANS.get(subscription['plan'], subscription['plan'])
        profile_text += f"📦 <b>Plan:</b> {plan_name}\n"
        profile_text += f"📅 <b>Boshlanish:</b> {subscription['start_date']:%Y-%m-%d %H:%M}\n"
        profile_text += f"📅 <b>Tugash:</b> {subscription['end_date']:%Y-%m-%d %H:%M}\n"
    else:
        profile_text += f"📦 <b>Plan:</b> Yo'q\n"
    
    profile_text += f"\n"
    
    if user_info['referral_code']:
        profile_text += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>🎁 Referral</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>Referral kodi:</b> <code>{user_info['referral_code']}</code>\n\n"
        )
    
    if user_info['is_hidden']:
        profile_text += f"🔒 <b>Profil holati:</b> Anonim (Premium obunachilar ham sizni tanib ololmaydi)\n"
    else:
        profile_text += f"🔓 <b>Profil holati:</b> Ochiq\n"
    
    # Add button to make profile anonymous
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 Profilni anonimlashtirish", callback_data="profile:make_anonymous")]
    ])
    
    await message.answer(profile_text, parse_mode='HTML', reply_markup=keyboard)


@user_router.callback_query(F.data == "profile:make_anonymous")
async def make_profile_anonymous(callback: CallbackQuery, bot: Bot, dispatcher):
    """Handle making profile anonymous - only works for premium users."""
    pool = dispatcher["db"]
    user_id = callback.from_user.id
    
    # Check if user is premium
    is_premium = await is_user_premium(pool, user_id)
    
    if is_premium:
        # User is premium - set is_hidden to True
        success = await set_user_hidden(pool, user_id)
        
        if success:
            await callback.message.edit_text(
                "<b>🔒 Profil anonimlashtirildi</b>\n\n"
                "✅ Endi hatto Premium obunachilar ham sizni tanib ololmaydi.\n"
                "Sizning profil ma'lumotlaringiz boshqalar uchun yashirin.",
                parse_mode='HTML'
            )
            await callback.answer("✅ Profil anonimlashtirildi!")
        else:
            await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
    else:
        # User is not premium - show premium purchase message
        premium_info = await get_user_premium_info(pool, user_id)
        
        if premium_info is None:
            await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
            return
        
        balance = premium_info.get('balance', 0.00)
        
        # Show the same message format as /premium command when user is not premium
        premium_text = (
            f"<b>💎 Premium Status</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>Premium:</b> <code>Faol emas</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>\n\n"
            f"💡 <b>Profilni anonimlashtirish uchun Premium rejaga o'ting.</b>\n\n"
            f"💡 <b>Premiumga o'tish uchun plan tanlang va to'lov qiling.</b>"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Premium sotib olish", callback_data="premium:purchase")],
            [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")]
        ])
        
        await callback.message.edit_text(premium_text, parse_mode='HTML', reply_markup=keyboard)
        await callback.answer()


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
        f"<b>💎 Premium Status</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ <b>Premium:</b> <code>Faol emas</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Balans:</b> <code>{balance:,.2f} so'm</code>\n\n"
        f"💡 <b>Premiumga o'tish uchun plan tanlang va to'lov qiling.</b>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Premium sotib olish", callback_data="premium:purchase")]
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
            f"💡 Balansni to'ldirish kerak.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")],
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
    _, is_new = await get_or_create_user(pool, user_id, username, name)
    
    # Notify admins about new user
    if is_new:
        await notify_admins_new_user(pool, bot, user_id, username, name)

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


# ==================== REVEAL SENDER ====================

@user_router.callback_query(F.data.startswith("reveal:sender:"))
async def reveal_sender(callback: CallbackQuery, bot: Bot, dispatcher):
    """Handle reveal sender button - check receiver's premium and show profile or premium message."""
    pool = dispatcher["db"]
    receiver_id = callback.from_user.id
    
    # Parse callback data: reveal:sender:sender_id:token_encoded:message_text_encoded
    parts = callback.data.split(":")
    sender_id = int(parts[2])
    token_encoded = parts[3] if len(parts) > 3 else None
    message_data = parts[4] if len(parts) > 4 else None
    
    # Check if RECEIVER (the person clicking the button) is premium
    receiver_is_premium = await is_user_premium(pool, receiver_id)
    
    if receiver_is_premium:
        # Receiver is premium - show sender's Telegram profile link
        # Get sender's info
        async with pool.acquire() as conn:
            sender_row = await conn.fetchrow(
                "SELECT name, username FROM users WHERE user_id = $1",
                sender_id
            )
        
        if not sender_row:
            await callback.answer("❌ Foydalanuvchi topilmadi.", show_alert=True)
            return
        
        sender_name = sender_row['name']
        sender_username = f"@{sender_row['username']}" if sender_row['username'] else "Yo'q"
            
        profile_text = (
            f"👤 <b>Xabar yuboruvchi</b>\n\n"
            f"📛 <b>Ism:</b> {sender_name}\n"
            f"📱 <b>Username:</b> {sender_username}\n"
        )
        
        # Create back button with original message data if available
        if message_data and message_data != "media" and token_encoded:
            back_data = f"reveal:back:{sender_id}:{token_encoded}:{message_data}"
        elif token_encoded:
            back_data = f"reveal:back:{sender_id}:{token_encoded}:media"
        else:
            back_data = "reveal:back:media"
        
        # Try to create profile link button, but handle privacy restrictions
        sender_link = f"tg://user?id={sender_id}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Profilni ko'rish", url=sender_link)],
            [InlineKeyboardButton(text="↩️ Orqaga", callback_data=back_data)]
        ])
        
        try:
            await callback.message.edit_text(profile_text, parse_mode='HTML', reply_markup=keyboard)
        except TelegramBadRequest as e:
            # If profile link is restricted by privacy settings, show info without button
            error_str = str(e).upper()
            error_message = getattr(e, 'message', '')
            if error_message:
                error_str += " " + str(error_message).upper()
            
            if "BUTTON_USER_PRIVACY_RESTRICTED" in error_str or "PRIVACY_RESTRICTED" in error_str:
                profile_text += "\n\n⚠️ <i>Foydalanuvchi profiliga kirish cheklangan (privacy settings).</i>"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Orqaga", callback_data=back_data)]
                ])
                await callback.message.edit_text(profile_text, parse_mode='HTML', reply_markup=keyboard)
            else:
                # Re-raise if it's a different error
                raise
        
        await callback.answer()
    else:
        # Receiver is NOT premium - show premium purchase message (like /premium command)
        receiver_info = await get_user_premium_info(pool, receiver_id)
        
        if receiver_info is None:
            await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
            return
        
        receiver_balance = receiver_info.get('balance', 0.00)
        
        # Show the same message format as /premium command when user is not premium
        premium_text = (
            f"<b>💎 Premium Status</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>Premium:</b> <code>Faol emas</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 <b>Balans:</b> <code>{receiver_balance:,.2f} so'm</code>\n\n"
            f"💡 <b>Xabar yuboruvchini ko'rish uchun Premium rejaga o'ting.</b>\n\n"
            f"💡 <b>Premiumga o'tish uchun plan tanlang va to'lov qiling.</b>"
        )
        
        # Create back button with original message data if available
        if message_data and message_data != "media" and token_encoded:
            back_data = f"reveal:back:{sender_id}:{token_encoded}:{message_data}"
        elif token_encoded:
            back_data = f"reveal:back:{sender_id}:{token_encoded}:media"
        else:
            back_data = "reveal:back:media"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Premium sotib olish", callback_data="premium:purchase")],
            [InlineKeyboardButton(text="💰 Balansni to'ldirish", callback_data="topup:show_referral")],
            [InlineKeyboardButton(text="↩️ Orqaga", callback_data=back_data)]
        ])
        
        await callback.message.edit_text(premium_text, parse_mode='HTML', reply_markup=keyboard)
        await callback.answer()


@user_router.callback_query(F.data.startswith("reveal:back:"))
async def reveal_back(callback: CallbackQuery, bot: Bot):
    """Go back from reveal sender screen - restore original message."""
    import base64
    
    parts = callback.data.split(":")
    if len(parts) >= 5:
        # Restore text message
        try:
            sender_id = int(parts[2])
            token_encoded = parts[3]
            message_text_encoded = parts[4]
            
            # Decode token and message
            sender_token = base64.b64decode(token_encoded).decode('utf-8')
            message_text = base64.b64decode(message_text_encoded).decode('utf-8')
            
            bot_username = (await bot.me()).username
            link = f"https://t.me/{bot_username}?start={sender_token}"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Javob berish", url=link)],
                [InlineKeyboardButton(text="👤 Kimdan", callback_data=f"reveal:sender:{sender_id}:{token_encoded}:{message_text_encoded}")]
            ])


            await callback.message.edit_text(
                f"<b>📨 Sizga yangi anonim xabar bor!</b>\n\n{message_text}",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
            await callback.answer()
        except Exception as e:
            await callback.answer("ℹ️ Xabarni ko'rish uchun chat tarixini tekshiring.", show_alert=True)
    elif len(parts) >= 4 and parts[3] == "media":
        # Media message - can't restore easily, show helpful message
        await callback.answer("ℹ️ Media xabarni ko'rish uchun chat tarixini tekshiring.", show_alert=True)
    else:
        await callback.answer("ℹ️ Xabarni ko'rish uchun chat tarixini tekshiring.", show_alert=True)
