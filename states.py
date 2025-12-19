"""
FSM States module.
Defines all Finite State Machine states used throughout the bot.
"""
from aiogram.fsm.state import State, StatesGroup


class QuestionStates(StatesGroup):
    """States for handling anonymous question flow."""
    waiting_for_question = State()


class BanState(StatesGroup):
    """States for banning/unbanning users."""
    waiting_for_user_id = State()
    waiting_for_duration = State()
    waiting_for_reason = State()
    waiting_for_unban_id = State()


class BroadcastState(StatesGroup):
    """States for broadcast message flow."""
    waiting_for_message = State()


class SearchUserState(StatesGroup):
    """States for searching users."""
    waiting_for_user_id = State()


class ChatState(StatesGroup):
    """States for live chat feature."""
    in_chat = State()


class PremiumPurchaseState(StatesGroup):
    """States for premium purchase flow."""
    waiting_for_plan_selection = State()


class AdminMessageState(StatesGroup):
    """States for admin sending anonymous messages."""
    waiting_for_message = State()

