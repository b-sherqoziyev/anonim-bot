"""
Middleware to automatically update user information when they interact with the bot.
"""
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable

from db import update_user_info


class UserUpdateMiddleware(BaseMiddleware):
    """Middleware to update user info (username, name) on every interaction."""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Get user info from message or callback
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
        
        # Update user info if we have a user and database pool
        if user and "db" in data:
            pool = data["db"]
            username = user.username or ""
            name = user.full_name or ""
            await update_user_info(pool, user.id, username, name)
        
        # Continue to handler
        return await handler(event, data)

