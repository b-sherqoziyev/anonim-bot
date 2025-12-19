"""
User handlers module.
Handles /start, /help commands and anonymous question messages from regular users.
"""
from aiogram import Router, Bot
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import LOG_CHANNEL_ID
from db import is_user_muted, get_user_by_token, log_message, get_or_create_user
from states import QuestionStates

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
        # User clicked on a link with token
        is_muted, muted_until = await is_user_muted(pool, user_id)
        if is_muted:
            vaqt_str = muted_until.strftime("%Y-%m-%d %H:%M:%S")
            await message.answer(
                f"⛔ Siz vaqtinchalik xabar yubora olmaysiz.\n"
                f"🕒 Mute Toshkent vaqti bilan {vaqt_str} gacha davom etadi.\n"
                f"Iltimos, kuting."
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
            "• /find_chat — anonim tarzda suhbatdosh qidirish\n"
            "• /stop_chat — jonli chatni yakunlash\n"
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
