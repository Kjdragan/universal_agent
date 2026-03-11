"""
Telegram keyboard helpers for the Universal Agent bot.

Provides quick builders for InlineKeyboard markups used in mission approval flows
and other interactive patterns.

Usage:
    from universal_agent.bot.telegram_keyboards import make_vp_approval_keyboard
    markup = make_vp_approval_keyboard(mission_id)
    await msg.reply_text("A mission needs your approval:", reply_markup=markup)
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def make_vp_approval_keyboard(mission_id: str) -> InlineKeyboardMarkup:
    """
    Returns an inline keyboard for accepting or rejecting a VP mission.

    Callback data format:
        vp_accept_<mission_id>
        vp_reject_<mission_id>
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"vp_accept_{mission_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"vp_reject_{mission_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def make_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Returns the standard main menu InlineKeyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📋 Status", callback_data="menu_status"),
            InlineKeyboardButton("🛑 Cancel Task", callback_data="menu_cancel"),
        ],
        [
            InlineKeyboardButton("📄 Briefing", callback_data="menu_briefing"),
            InlineKeyboardButton("🚀 Delegate", callback_data="menu_delegate"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)
