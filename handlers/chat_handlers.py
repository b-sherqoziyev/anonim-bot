"""
Chat handlers module.
Handles live chat feature: /find_chat, /end_chat, and anonymous message delivery during chat.
"""
import random
from aiogram import Router, Bot, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import LOG_CHANNEL_ID
from db import (
    add_to_chat_queue,
    find_chat_partner,
    get_chat_partner,
    end_chat,
    remove_from_chat_queue,
    is_user_banned,
    get_or_create_user
)
from states import ChatState

# Create router for chat handlers
chat_router = Router()


@chat_router.message(Command("find_chat"))
async def find_chat_handler(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Handle /find_chat command - add user to queue and find a partner."""
    pool = dispatcher["db"]
    user_id = message.from_user.id
    username = message.from_user.username
    name = message.from_user.full_name

    # Check if user is banned
    is_banned, banned_until = await is_user_banned(pool, user_id)
    if is_banned:
        await message.answer(
            "⛔ Siz bloklangan va chat qidira olmaysiz.\n"
            "Iltimos, admin bilan bog'laning."
        )
        return

    # Check if user is already in a chat
    existing_partner = await get_chat_partner(pool, user_id)
    if existing_partner:
        await message.answer("⚠️ Siz allaqachon chatdasiz! Chatni tugatish uchun /end_chat buyrug'ini yuboring.")
        return

    # Ensure user exists in database
    _, is_new = await get_or_create_user(pool, user_id, username, name)
    
    # Notify admins about new user
    if is_new:
        from db import notify_admins_new_user
        await notify_admins_new_user(pool, bot, user_id, username, name)

    # Try to add to queue
    added, status = await add_to_chat_queue(pool, user_id)
    
    if not added:
        if status == "already_in_chat":
            await message.answer("⚠️ Siz allaqachon chatdasiz! Chatni tugatish uchun /end_chat buyrug'ini yuboring.")
            return
        elif status == "already_in_queue":
            await message.answer("⏳ Siz allaqachon navbatdasiz. Suhbatdosh qidirilmoqda...\n\nBekor qilish uchun 👉 /end_chat")
            return

    # Try to find a partner
    found, partner_id = await find_chat_partner(pool, user_id)
    
    if found and partner_id:
        # Chat found! Set both users to in_chat state
        await state.set_state(ChatState.in_chat)
        
        # Notify both users
        try:
            await bot.send_message(
                chat_id=user_id,
                text="✅ Suhbatdosh topildi!\n\n Chatni yakunlash uchun: /end_chat"
            )
            await bot.send_message(
                chat_id=partner_id,
                text="✅ Suhbatdosh topildi!\n\n Chatni yakunlash uchun: /end_chat"
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            # If partner blocked the bot, remove from queue and notify user
            await remove_from_chat_queue(pool, user_id)
            await end_chat(pool, user_id)
            await message.answer("❌ Suhbatdosh topildi, lekin bot bilan bog'lanishda muammo yuz berdi.")
            return
    else:
        # No partner found, user is in queue
        await message.answer("⏳ Suhbatdosh qidirilmoqda... Iltimos, kuting.\n\nBekor qilish uchun 👉 /end_chat")


@chat_router.message(Command("end_chat"))
async def end_chat_handler(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Handle /end_chat command - end the current chat."""
    pool = dispatcher["db"]
    user_id = message.from_user.id

    # Check if user is in a chat
    partner_id = await get_chat_partner(pool, user_id)
    
    if not partner_id:
        # User is not in a chat, check if in queue
        async with pool.acquire() as conn:
            in_queue = await conn.fetchrow("SELECT user_id FROM chat_queue WHERE user_id = $1", user_id)
        
        if in_queue:
            await remove_from_chat_queue(pool, user_id)
            await message.answer("✅ Navbatdan chiqdingiz.")
        else:
            await message.answer("⚠️ Siz hozircha chatda emassiz.")
        await state.clear()
        return

    # End the chat
    ended, partner = await end_chat(pool, user_id)
    
    if ended and partner:
        # Clear state
        await state.clear()
        
        # Notify both users
        try:
            await message.answer("✅ Chat tugatildi")
            await bot.send_message(
                chat_id=partner,
                text="✅ Chat tugatildi"
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            await message.answer("✅ Chat tugatildi")
    else:
        await message.answer("⚠️ Chatni tugatishda muammo yuz berdi.")
        await state.clear()


async def deliver_chat_message(message: Message, state: FSMContext, bot: Bot, dispatcher, partner_id: int):
    """Helper function to deliver chat message to partner."""
    pool = dispatcher["db"]
    user_id = message.from_user.id
    username = message.from_user.username
    name = message.from_user.full_name

    # Ensure user exists in database
    _, is_new = await get_or_create_user(pool, user_id, username, name)
    
    # Notify admins about new user
    if is_new:
        from db import notify_admins_new_user
        await notify_admins_new_user(pool, bot, user_id, username, name)

    try:
        if message.text:
            # Text message
            await bot.send_message(
                chat_id=partner_id,
                text=f"<b>💬 Anonim xabar:</b>\n\n{message.text}"
            )
            # Send confirmation to sender
            await message.answer("✅ Xabar yuborildi!")
        else:
            # Media messages (photo, video, voice, document)
            if message.photo:
                await bot.send_photo(
                    partner_id,
                    message.photo[-1].file_id,
                    caption="<b>💬 Anonim xabar</b>"
                )
            elif message.video:
                await bot.send_video(
                    partner_id,
                    message.video.file_id,
                    caption="<b>💬 Anonim xabar</b>"
                )
            elif message.voice:
                await bot.send_voice(
                    partner_id,
                    message.voice.file_id,
                    caption="<b>💬 Anonim xabar</b>"
                )
            elif message.document:
                await bot.send_document(
                    partner_id,
                    message.document.file_id,
                    caption="<b>💬 Anonim xabar</b>"
                )
            else:
                await message.answer("<b>⚠️ Ushbu turdagi xabar qo'llab-quvvatlanmaydi.</b>")
                return

            # Send confirmation to sender
            await message.answer("✅ Xabar yuborildi!")

            # Log media messages to log channel
            sender_link = f'<a href="tg://user?id={user_id}">{name}</a>'
            receiver_link = f'<a href="tg://user?id={partner_id}">{partner_id}</a>'

            log_caption = (
                f"💬 <b>Live Chat</b>\n\n"
                f"📥 <b>Yuboruvchi:</b> {sender_link}\n"
                f"👤 <b>Qabul qiluvchi:</b> {receiver_link}"
            )

            if message.photo:
                await bot.send_photo(LOG_CHANNEL_ID, message.photo[-1].file_id, caption=log_caption, parse_mode='HTML')
            elif message.video:
                await bot.send_video(LOG_CHANNEL_ID, message.video.file_id, caption=log_caption, parse_mode='HTML')
            elif message.voice:
                await bot.send_voice(LOG_CHANNEL_ID, message.voice.file_id, caption=log_caption, parse_mode='HTML')
            elif message.document:
                await bot.send_document(LOG_CHANNEL_ID, message.document.file_id, caption=log_caption, parse_mode='HTML')

    except TelegramForbiddenError:
        # Partner blocked the bot, end the chat
        await end_chat(pool, user_id)
        await state.clear()
        await message.answer("❌ Suhbatdosh botni bloklagan. Chat tugatildi.")
    except TelegramBadRequest as e:
        await message.answer(f"⚠️ Xatolik yuz berdi: {e.message}")


@chat_router.message(ChatState.in_chat)
async def handle_chat_message(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """Handle messages during active chat - deliver anonymously to partner."""
    pool = dispatcher["db"]
    user_id = message.from_user.id

    partner_id = await get_chat_partner(pool, user_id)
    
    if not partner_id:
        await state.clear()
        await message.answer("⚠️ Suhbatdosh topilmadi. Chat tugatildi.")
        return
    
    # Deliver the message
    await deliver_chat_message(message, state, bot, dispatcher, partner_id)


@chat_router.message()
async def handle_chat_message_check(message: Message, state: FSMContext, bot: Bot, dispatcher):
    """
    Handle messages that might be in chat - check database for active chat.
    This handles cases where user is in chat but FSM state wasn't set.
    Only processes non-command messages when user is not in other FSM states.
    """
    # Skip if it's a command
    if message.text and message.text.startswith('/'):
        return
    
    pool = dispatcher["db"]
    user_id = message.from_user.id
    
    # Check current FSM state
    current_state = await state.get_state()
    
    # If already in ChatState, the state-specific handler will handle it
    if current_state == ChatState.in_chat:
        return
    
    # If in other states (like QuestionStates), let those handlers process it
    if current_state is not None:
        return
    
    # Check database for active chat (only if no FSM state is active)
    partner_id = await get_chat_partner(pool, user_id)
    
    if partner_id:
        # User is in chat, set state and deliver message
        await state.set_state(ChatState.in_chat)
        await deliver_chat_message(message, state, bot, dispatcher, partner_id)

